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

import json
import sqlite3
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
