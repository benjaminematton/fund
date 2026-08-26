#!/usr/bin/env python3
"""Read-only production health check for developers.

    make dev-status

Answers one question: is every stated invariant and Phase 2 acceptance
criterion still true on the box that trades? The checks live in devcheck/ and
are pure; this file is the only place that opens an ssh connection, a broker
client, or the database.

READ-ONLY, ALWAYS. Nothing here writes, places, cancels, amends or deploys.
Every finding is for a human to act on.

EXIT 0 ALWAYS. A check that cannot run renders as a finding. A non-zero exit
would hide every other check behind the first failure, which is the opposite
of the job.

EVERY PRODUCTION READ IS AGAINST THE DROPLET, NOT THIS CHECKOUT. The local
tree is not what trades — reading `agents/config/*.yaml` here would check the
wrong host and always agree with the suite that just went green.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from devcheck.evaluate import apply_suppression, evaluate      # noqa: E402
from devcheck.model import (                                   # noqa: E402
    OrderRow,
    Position,
    ServiceResult,
    Snapshot,
)
from devcheck.render import render                             # noqa: E402

HEALTH = ROOT / ".claude" / "health.md"

DROPLET = "root@138.197.47.97"
REMOTE_ROOT = "/opt/fund"
# NOT a constant: the path is read from the droplet's own FUND_DB, because a
# hardcoded guess is silently wrong rather than loudly wrong. The first guess
# here was /var/lib/fund/fund.db, which EXISTS on the box as a 0-byte stray
# file — so every query returned no rows with exit 0 and five checks rendered
# green against an empty database.
_ENV_CACHE: dict[str, str | None] = {}
UNITS = ("fund-daily", "fund-pnl")
SEATS = ("exec", "pm", "analyst", "news", "critic")


def read_suppressed(path: Path) -> frozenset[str]:
    """Parse `suppress:` from the descriptor's YAML front matter.

    Absent file, absent key, or malformed front matter all mean "suppress
    nothing" — never a crash. A descriptor problem must not be able to stop
    the checks from running.
    """
    try:
        text = path.read_text()
    except OSError:
        return frozenset()
    if not text.startswith("---"):
        return frozenset()
    _, _, rest = text.partition("---")
    front, _, _ = rest.partition("---")
    out: set[str] = set()
    in_block = False
    for line in front.splitlines():
        if line.strip().startswith("suppress:"):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped.startswith("- "):
                out.add(stripped[2:].strip())
            elif stripped:
                break
    return frozenset(out)


def _ssh(cmd: str, timeout: int = 15) -> str | None:
    """Run one read-only command on the droplet. None on any failure.

    None means "could not read", never "read an empty result" — the callers
    depend on that difference, because rendering absence as health is the
    exact failure this package exists to prevent.
    """
    try:
        out = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", DROPLET, cmd],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _droplet_var(name: str) -> str | None:
    """One variable from the droplet's own environment files.

    Every path this script reads comes from here rather than a constant. Both
    hardcoded guesses in the first draft — the database and the journals root
    — resolved to real but empty locations on the box, so the checks that
    depended on them rendered green against nothing.
    """
    if name not in _ENV_CACHE:
        raw = _ssh(f"grep -h '^{name}=' /etc/fund/env {REMOTE_ROOT}/.env 2>/dev/null | head -1")
        value = None
        if raw and "=" in raw:
            _, _, rest = raw.strip().partition(f"{name}=")
            value = rest.strip().strip("'\"") or None
        _ENV_CACHE[name] = value
    return _ENV_CACHE[name]


def _remote_db() -> str | None:
    """The droplet's FUND_DB, read from the same env the fund itself uses."""
    return _droplet_var("FUND_DB")


def _sql(query: str) -> list[dict] | None:
    """One read-only SQLite query on the droplet's live database, as JSON.

    None means the read failed; the caller must never turn that into an empty
    result. `mode=ro` is load-bearing twice over: opening the live DB
    read-write would apply a pending migration as a side effect of a health
    check (issue #17's shape), and it makes a missing file an ERROR rather
    than an empty database sqlite3 helpfully creates.

    -json rather than a separator. A `-separator '\\x1f'` passed through ssh
    arrives as the four literal characters, so every multi-column row came
    back unsplit as one string — checkpoints then filtered itself to zero and
    printed "0 checkpoint(s), all done" against 56 real rows. JSON has no
    separator to get wrong, and a malformed reply raises here rather than
    quietly losing columns.
    """
    db = _remote_db()
    if db is None:
        return None
    raw = _ssh(f"sqlite3 -json 'file:{db}?mode=ro' \"{query}\"", timeout=20)
    if raw is None:
        return None
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def _droplet_env() -> dict[str, str]:
    value = _droplet_var("ALPACA_PAPER_TRADE")
    return {"ALPACA_PAPER_TRADE": value} if value is not None else {}


def _seat_trading_toolsets() -> dict[str, bool]:
    """Which seats hold the `trading` toolset ON THE DROPLET.

    Read remotely on purpose. The local checkout is not what runs, so a local
    read would confirm the invariant against a file no seat ever loads.
    An unreadable config yields no entry rather than a False, so a failed read
    can never be mistaken for "this seat is safe".
    """
    raw = _ssh(f"grep -H '^alpaca_toolsets:' {REMOTE_ROOT}/agents/config/*.yaml 2>/dev/null")
    if raw is None:
        return {}
    out: dict[str, bool] = {}
    for line in raw.splitlines():
        path, _, value = line.partition(":")
        seat = Path(path).stem
        if seat in SEATS:
            out[seat] = "trading" in value.split("#")[0]
    return out


def _tracked_checks() -> frozenset[str]:
    """Open issues labelled check:<id>. Any gh failure means "nothing is
    tracked" — the check then over-reports, which is the safe direction."""
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--state", "open", "--limit", "100",
             "--json", "labels", "-q", ".[].labels[].name"],
            capture_output=True, text=True, timeout=20, cwd=str(ROOT),
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if out.returncode != 0:
        return frozenset()
    return frozenset(
        line.strip()[len("check:"):]
        for line in out.stdout.splitlines()
        if line.strip().startswith("check:")
    )


def _service(unit: str) -> ServiceResult:
    raw = _ssh(f"systemctl show {unit}.service -p Result -p ExecMainExitTimestamp --value")
    if raw is None:
        return ServiceResult(unit, "unreachable", "")
    parts = [p.strip() for p in raw.splitlines() if p.strip()]
    result = parts[0] if parts else "unknown"
    last = parts[1] if len(parts) > 1 else ""
    return ServiceResult(unit, result, last)


def _deploy_state() -> tuple[str, str, int]:
    """(droplet HEAD, origin/master, commits the droplet is behind).

    The count is computed locally against a freshly fetched origin/master, so
    it cannot be stale in the way a hand-quoted SHA is. An unreachable droplet
    yields a 0 count beside an empty head — `deploy_state` then reads ok while
    `services` alerts unreachable, which is the honest split: nothing was
    measured about the deploy, and the reason is already on the report.
    """
    head = (_ssh(f"git -C {REMOTE_ROOT} rev-parse HEAD") or "").strip()
    subprocess.run(["git", "-C", str(ROOT), "fetch", "--quiet", "origin", "master"],
                   capture_output=True, timeout=60, check=False)
    origin = _run_local("git", "-C", str(ROOT), "rev-parse", "origin/master")
    if not head:
        return "unreadable", origin[:7], 0
    behind = _run_local("git", "-C", str(ROOT), "rev-list", "--count", f"{head}..origin/master")
    return head[:7], origin[:7], int(behind) if behind.isdigit() else 0


def _run_local(*argv: str) -> str:
    try:
        out = subprocess.run(list(argv), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _positions_and_coverage() -> tuple[list[Position] | None, list, int | None, str]:
    """Positions with aggregate stop coverage, straight from the broker.

    Coverage is computed by orchestrator.protection._covering_qty — the same
    function the fund's own protection pass uses. Re-deriving it here would
    be a second answer to a question that already has one, and its careful
    'unreadable order means unknown, never a smaller number' behaviour is the
    part a re-derivation would lose.
    """
    try:
        from market.source_alpaca import AlpacaSource
        from orchestrator.protection import _CLOSING_SIDE, _covering_qty, _qty
    except Exception as exc:
        return None, [], None, f"broker client unavailable: {exc}"

    missing = [k for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY") if not os.environ.get(k)]
    if missing:
        # Named explicitly: a local shell without credentials and a broker
        # outage are different problems, and a red row that cannot tell them
        # apart trains the reader to skip the row.
        return None, [], None, (
            f"{', '.join(missing)} not in this shell — run `set -a; source .env; set +a`"
        )
    try:
        source = AlpacaSource()
        raw_positions = source.open_positions()
        raw_orders = source.open_orders()
    except Exception as exc:
        return None, [], None, f"broker read failed: {exc}"

    out: list[Position] = []
    for raw in raw_positions:
        p = raw if isinstance(raw, dict) else {}
        symbol = str(p.get("symbol") or "?")
        held = _qty(p.get("qty"))
        closing_side = _CLOSING_SIDE.get(str(p.get("side") or "").lower())
        if held is None or closing_side is None:
            # Unreadable: report zero cover so it alerts. Never silently ok.
            out.append(Position(symbol, qty=1.0, covering_qty=0.0))
            continue
        covered = _covering_qty(raw_orders, symbol, closing_side)
        out.append(Position(symbol, float(held), float(covered) if covered is not None else 0.0))

    # AlpacaSource has no fill-history method, so this stays None and
    # db_broker_agreement reports itself unwired. Defaulting it to len(orders)
    # would print a green row for a comparison nobody performed.
    return out, raw_orders, None, ""


def build_snapshot() -> Snapshot:
    """Build one Snapshot from production. The only I/O in the package.

    Each reader is wrapped so a failure becomes data — an unreachable droplet
    yields ServiceResult(result="unreachable"), never an exception that hides
    the broker and database checks behind it.
    """
    # A probe first: it distinguishes "the database has no rows" from "the
    # database was never read", which every query below would otherwise
    # collapse into the same empty list.
    probe = _sql("select count(*) from orders")
    db_read_ok = probe is not None

    orders_rows = _sql("select client_order_id, symbol from orders") or []
    ticket_rows = _sql("select id, ticker from tickets") or []
    unposted = _sql("select count(*) from events where posted_at is null")
    checkpoint_rows = _sql(
        "select run_date, stage, status from checkpoints "
        "where run_date = (select max(run_date) from checkpoints)"
    ) or []
    due_rows = _sql(
        "select d.id from decisions d left join resolutions r on r.decision_id = d.id "
        "where r.id is null and d.action in ('buy','sell') "
        "and date(d.run_date, '+7 day') <= date('now')"
    ) or []

    run_date = str(checkpoint_rows[0]["run_date"]) if checkpoint_rows else ""
    participants = _sql(
        "select distinct agent from costs where run_date = "
        "(select max(run_date) from costs)"
    ) or []

    positions, _open_orders, broker_fills, broker_error = _positions_and_coverage()
    head, origin, behind = _deploy_state()

    return Snapshot(
        droplet_env=_droplet_env(),
        seat_trading_toolsets=_seat_trading_toolsets(),
        orders=[OrderRow(str(r["client_order_id"]), str(r.get("symbol") or "")) for r in orders_rows],
        tickets={str(r["id"]): str(r.get("ticker") or "") for r in ticket_rows},
        events_unposted=int(next(iter(unposted[0].values()))) if unposted else 0,
        broker_fill_count=broker_fills,
        checkpoints=[(str(r["run_date"]), str(r["stage"]), str(r["status"])) for r in checkpoint_rows],
        journals_written=_journals_written(run_date),
        seats_participating={str(r["agent"]) for r in participants} & set(SEATS),
        scorecard_codes=_scorecard_codes(),
        positions=positions,
        open_orders=[],
        due_unresolved=[int(r["id"]) for r in due_rows],
        droplet_head=head,
        origin_master=origin,
        commits_behind=behind,
        services={u: _service(u) for u in UNITS},
        broker_error=broker_error,
        db_read_ok=db_read_ok,
        suppressed=read_suppressed(HEALTH),
        tracked_checks=_tracked_checks(),
    )


def _journals_written(run_date: str) -> set[str]:
    """Seats whose journal carries a `## <run_date>` entry.

    The root comes from FUND_JOURNALS, not a constant: /opt/fund/journals
    exists on the box and holds only a .gitkeep, so listing it reported every
    seat silent while the real journals sat in /var/lib/fund.

    Scoped to the run date on purpose. A seat that wrote an entry last week
    has a file, and a check that only asked whether the file exists would go
    green for a seat that said nothing today.
    """
    root = _droplet_var("FUND_JOURNALS")
    if root is None or not run_date:
        return set()
    raw = _ssh(f"grep -l '^## {run_date}$' {root}/*.md 2>/dev/null")
    if raw is None:
        return set()
    return {Path(n.strip()).stem for n in raw.splitlines() if n.strip()} & set(SEATS)


def _scorecard_codes() -> list[str]:
    """Alert codes raised on the droplet's most recent run date."""
    rows = _sql(
        "select kind from events where date(created_at) = "
        "(select max(date(created_at)) from events)"
    ) or []
    return [str(r["kind"]) for r in rows if r.get("kind")]


def main() -> int:
    snapshot = build_snapshot()
    findings = apply_suppression(evaluate(snapshot), read_suppressed(HEALTH))
    print(render(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
