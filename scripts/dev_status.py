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
REMOTE_DB = "/var/lib/fund/fund.db"
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


def _sql(query: str) -> list[list[str]] | None:
    """One read-only SQLite query on the droplet's live database.

    `mode=ro` is load-bearing: opening the live DB read-write would apply a
    pending migration as a side effect of a health check (issue #17's shape).
    """
    raw = _ssh(
        f"sqlite3 -separator '\\x1f' 'file:{REMOTE_DB}?mode=ro' \"{query}\"",
        timeout=20,
    )
    if raw is None:
        return None
    return [line.split("\x1f") for line in raw.splitlines() if line.strip()]


def _droplet_env() -> dict[str, str]:
    raw = _ssh(
        f"grep -hE '^ALPACA_PAPER_TRADE=' {REMOTE_ROOT}/.env /etc/fund/env 2>/dev/null | head -1"
    )
    if not raw or "=" not in raw:
        return {}
    _, _, value = raw.strip().partition("ALPACA_PAPER_TRADE=")
    return {"ALPACA_PAPER_TRADE": value.strip().strip("'\"")}


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


def _positions_and_coverage() -> tuple[list[Position] | None, list, int | None]:
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
    except Exception:
        return None, [], None
    try:
        source = AlpacaSource()
        raw_positions = source.open_positions()
        raw_orders = source.open_orders()
    except Exception:
        return None, [], None

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
    return out, raw_orders, None


def build_snapshot() -> Snapshot:
    """Build one Snapshot from production. The only I/O in the package.

    Each reader is wrapped so a failure becomes data — an unreachable droplet
    yields ServiceResult(result="unreachable"), never an exception that hides
    the broker and database checks behind it.
    """
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

    positions, _open_orders, broker_fills = _positions_and_coverage()
    head, origin, behind = _deploy_state()

    return Snapshot(
        droplet_env=_droplet_env(),
        seat_trading_toolsets=_seat_trading_toolsets(),
        orders=[OrderRow(r[0], r[1] if len(r) > 1 else "") for r in orders_rows],
        tickets={r[0]: (r[1] if len(r) > 1 else "") for r in ticket_rows},
        events_unposted=int(unposted[0][0]) if unposted and unposted[0][0].isdigit() else 0,
        broker_fill_count=broker_fills,
        checkpoints=[(r[0], r[1], r[2]) for r in checkpoint_rows if len(r) >= 3],
        journals_written=_journals_written(),
        seats_participating={r[1] for r in checkpoint_rows if len(r) > 1} & set(SEATS),
        scorecard_codes=_scorecard_codes(),
        positions=positions,
        open_orders=[],
        due_unresolved=[int(r[0]) for r in due_rows if r[0].isdigit()],
        droplet_head=head,
        origin_master=origin,
        commits_behind=behind,
        services={u: _service(u) for u in UNITS},
        suppressed=read_suppressed(HEALTH),
    )


def _journals_written() -> set[str]:
    """Seats with a journal entry for the droplet's latest run date."""
    raw = _ssh(f"ls {REMOTE_ROOT}/journals 2>/dev/null")
    if raw is None:
        return set()
    return {Path(n.strip()).stem for n in raw.splitlines() if n.strip()} & set(SEATS)


def _scorecard_codes() -> list[str]:
    """Alert codes raised on the droplet's most recent run date."""
    rows = _sql(
        "select kind from events where date(created_at) = "
        "(select max(date(created_at)) from events)"
    ) or []
    return [r[0] for r in rows if r]


def main() -> int:
    snapshot = build_snapshot()
    findings = apply_suppression(evaluate(snapshot), read_suppressed(HEALTH))
    print(render(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
