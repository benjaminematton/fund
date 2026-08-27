#!/usr/bin/env python3
"""File an unmatched alert as a GitHub issue (docs/agents/devops.md).

Zero dependencies (stdlib only) and argv-driven, so it runs against a live or
backed-up DB with nothing installed:

    python3 scripts/file_alert_issues.py state/fund.sqlite --since 2026-08-21
    python3 scripts/file_alert_issues.py state/fund.sqlite --since 2026-08-21 --apply

DRY RUN IS THE DEFAULT. Filing is the only irreversible act here — an issue can
be closed but not un-filed — so producing issues needs an explicit --apply.

This is not a detector. It never decides whether a condition is true; it reads
alerts the software already raised and asks the tracker what is already open.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling audit_day
import audit_day                                           # noqa: E402

MAX_TITLE = 110


@dataclass(frozen=True)
class Filing:
    action: str                  # "create" | "skip" | "comment"
    labels: tuple[str, ...]
    code: str
    ticker: str | None
    title: str
    body: str
    issue: int | None = None


def _title(code: str, ticker: str | None, text: str) -> str:
    head = f"{code}: " + (f"{ticker} — " if ticker else "")
    room = MAX_TITLE - len(head)
    return head + (text if len(text) <= room else text[:room - 1] + "…")


def _body(code, ticker, occurrences, cleared, db_path) -> str:
    lines = [f"Filed automatically from `{db_path}` by "
             "`scripts/file_alert_issues.py`.", "",
             f"- **code:** `{code}`"]
    if ticker:
        lines.append(f"- **ticker:** `{ticker}`")
    lines += [f"- **occurrences in window:** {len(occurrences)}", ""]
    if cleared:
        lines += ["> A clearing alert arrived for this condition. The symptom"
                  " resolved; whether the underlying defect is fixed is a"
                  " human judgement.", ""]
    lines.append("### Alert text seen")
    for created_at, text in occurrences:
        lines.append(f"- `{created_at}` — {text}")
    return "\n".join(lines)


def plan_filings(conn, since: str, tracker, db_path: str = "") -> tuple[list[Filing], list[str]]:
    start, _ = audit_day.et_day_window(since)
    rows = conn.execute(
        "SELECT payload, created_at FROM events WHERE kind = 'alert'"
        " AND created_at >= ? ORDER BY id", (start,)).fetchall()

    groups: dict[tuple[str, ...], dict] = {}
    malformed: list[str] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            malformed.append(f"{row['created_at']}: unparseable payload")
            continue
        if payload.get(audit_day.SELF_ALERT_KEY):
            continue                       # the rollup restates the others
        code = payload.get("code")
        if not code:
            malformed.append(f"{row['created_at']}: no code — "
                             f"{payload.get('text', '')[:80]}")
            continue
        ticker = payload.get("ticker")
        labels = (f"alert:{code}",) + ((f"ticker:{ticker}",) if ticker else ())
        group = groups.setdefault(labels, {"code": code, "ticker": ticker,
                                           "occurrences": [], "cleared": False})
        if payload.get("clears"):
            group["cleared"] = True
        else:
            group["cleared"] = False       # a fresh firing reopens the finding
            group["occurrences"].append((row["created_at"], payload.get("text", "")))

    filings: list[Filing] = []
    for labels, g in groups.items():
        existing = tracker.open_issue(labels)
        body = _body(g["code"], g["ticker"], g["occurrences"], g["cleared"], db_path)
        if not g["occurrences"]:
            # Nothing but clearing alerts. Comment if something is tracking
            # it; never file an issue to announce that a problem went away.
            if existing is not None:
                filings.append(Filing("comment", labels, g["code"], g["ticker"],
                                      "", body, existing))
            continue
        title = _title(g["code"], g["ticker"], g["occurrences"][0][1])
        action = "skip" if existing is not None else "create"
        filings.append(Filing(action, labels, g["code"], g["ticker"], title,
                              body, existing))
    return filings, malformed


class GhTracker:
    """The tracker, over the `gh` CLI (docs/agents/issue-tracker.md)."""

    def __init__(self, repo: str, run=None):
        self._run = run or subprocess.run
        self.repo = repo

    def _gh(self, *args: str) -> str:
        try:
            r = self._run(["gh", *args, "--repo", self.repo],
                          capture_output=True, text=True)
        except FileNotFoundError as e:
            raise RuntimeError(f"gh {' '.join(args)} failed: {e}") from e
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout

    def open_issue(self, labels):
        argv = ["issue", "list", "--state", "open", "--json", "number"]
        for label in labels:
            argv += ["--label", label]
        found = json.loads(self._gh(*argv) or "[]")
        return found[0]["number"] if found else None

    def ensure_label(self, label: str) -> None:
        self._gh("label", "create", label, "--force")

    def create_issue(self, title, body, labels) -> None:
        argv = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            argv += ["--label", label]
        self._gh(*argv)

    def comment_issue(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--body", body)


def main(argv: list[str] | None = None, run=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db")
    ap.add_argument("--since", required=True, help="ET calendar date YYYY-MM-DD")
    ap.add_argument("--repo", default="benjaminematton/fund")
    ap.add_argument("--apply", action="store_true",
                    help="actually file; without it this is a dry run")
    args = ap.parse_args(argv)

    # Named before anything else. Chained to the nightly pull this reads a
    # mirror that cannot tell stale from fresh (#110), so which snapshot it
    # worked from is the one fact a human needs to trust the run at all.
    print(f"reading {Path(args.db).name}")

    # READ-ONLY, ALWAYS. Unattended, against the only off-box copy of the
    # fund's records: a read-write open applies a pending migration as a side
    # effect, which is why dev_status.py opens every production read `mode=ro`,
    # and the argument is stronger for a job nobody is watching.
    #
    # It also fixes a quieter thing. Plain sqlite3.connect CREATES a missing
    # file rather than raising, so a wrong path used to surface as "no such
    # table: events" on the first query — indistinguishable from a database
    # with nothing in it, and leaving an empty file behind. The `mode=ro` URI
    # raises on connect instead.
    try:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    except sqlite3.Error as e:
        print(f"cannot read {args.db}: {e}", file=sys.stderr)
        return 1
    conn.row_factory = sqlite3.Row
    tracker = GhTracker(args.repo, run=run)
    try:
        filings, malformed = plan_filings(conn, args.since, tracker, args.db)
    except RuntimeError as e:
        print(f"tracker unavailable: {e}", file=sys.stderr)
        return 1
    except sqlite3.Error as e:
        print(f"cannot read {args.db}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    for m in malformed:
        print(f"MALFORMED (not filed): {m}", file=sys.stderr)

    failed = 0
    for f in filings:
        if f.action == "skip":
            print(f"already tracked as #{f.issue}: {' '.join(f.labels)}")
            continue
        verb = "filing" if args.apply else "would file"
        if f.action == "comment":
            print(f"{verb} a comment on #{f.issue}: cleared {f.code}")
        else:
            print(f"{verb}: {f.title}  [{' '.join(f.labels)}]")
        if not args.apply:
            continue
        try:
            if f.action == "comment":
                tracker.comment_issue(f.issue, f.body)
            else:
                for label in f.labels:
                    tracker.ensure_label(label)
                tracker.create_issue(f.title, f.body, f.labels)
        except RuntimeError as e:
            # Reported, never retried: a retry with a fresh id is how you get
            # two issues for one condition.
            print(f"FAILED {f.action} for {' '.join(f.labels)}: {e}",
                  file=sys.stderr)
            failed += 1

    if not filings and not malformed:
        print(f"no alerts needing an issue since {args.since}")
    return 1 if (failed or malformed) else 0


if __name__ == "__main__":
    sys.exit(main())
