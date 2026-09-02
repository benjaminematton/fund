#!/usr/bin/env python3
"""Nightly scoring job — writes the `weights` table (specs/improvement.md §2.1).

    make weights           # == python scripts/weights_day.py

resolve_day.py writes the calibration INPUT and stops at the data; this is
the consumer calibration.md §0 always promised ("Ops runs the scoreboard
job; agents read it"). One row per graded seat per night: every AgentScore
field, the deterministic PM weight, and the behavioural rates the Proposer
is later graded against. The morning brief reads the row as data.

WHY IT RIDES THE 16:35 TIMER, THIRD. It needs tonight's resolutions, which
resolve_day writes one leg earlier. It sits BEFORE reflect_day for two
reasons: reflect_day drains the outbox, so an alert this job appends reaches
Slack the same night without this job holding a token; and reflect_day is
perishable (a reflection missed for seven nights is destroyed) while this
job is not — a missed scoreboard night is recomputed, identically, the next
night. That is also why a SCORING failure exits 0 here: Type=oneshot stops
at the first non-zero ExecStart, and a broken scoreboard must not hold back
the leg that cannot be retried. Only a failure BEFORE the database is open
(a missing env var, a paper-guard trip, a config missing a key) exits
non-zero — nothing can alert yet, and OnFailure= is the alert. If the
database itself is what failed, the alert write fails too: that is logged
to stdout (journald) and the job STILL exits 0 — reflect_day then hits the
same database and fails loud on its own, which is OnFailure='s job.

NO SLACK, NO SEAT, NO BROKER. The job needs the database and the committed
config and nothing else. Requiring a token would let an unrelated missing
var stop the scoreboard from ever being written. The cost of holding no
token: an alert this job appends is posted by reflect_day's drain, and if
that leg exits before its drain (missing key, lock held), the row sits
undrained and reddens the next audit — audit_day's undrained check has no
date bound. The same posture reflect_day's own alerts already have.

Posture (invariant 4 / improvement.md §0.7: no row beats a wrong row):
  * ALPACA_PAPER_TRADE != 'true'  -> exit 1 before anything else
  * a missing env var             -> exit 1 naming every missing var
  * config missing a key          -> exit 1 (KeyError names the key)
  * write_weights raises          -> no row for any seat (it rolled back),
                                     last good rows stand, ONE alert
                                     (weights_job_failed), exit 0
  * ...and the alert write raises -> logged "ALERT NOT WRITTEN", exit 0
                                     (either alert: the failure one or the
                                     skipped-seat one — after connect(),
                                     no branch exits non-zero)
  * a seat's load-bearing value   -> that seat skipped, the rest written,
    is not finite                    ONE alert naming every such seat
                                     (weights_seat_skipped), exit 0
  * unchanged inputs              -> nothing written, logged as unchanged

Re-running is safe and free: unchanged seats hash to their latest row and
write nothing; a changed seat the same night replaces that night's row.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/weights_day.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import yaml                                           # noqa: E402

import run_day                                        # noqa: E402
from orchestrator.improve import WeightsConfig, write_weights   # noqa: E402
from state.db import connect                          # noqa: E402

REQUIRED_ENV = ("FUND_DB",)
CONFIG_YAML = ROOT / "config" / "improvement.yaml"


def log(msg: str) -> None:
    print(f"weights_day: {msg}", flush=True)


def load_config(path: Path) -> WeightsConfig:
    """config/improvement.yaml -> WeightsConfig. A missing key raises KeyError
    naming it; a value below 1 raises from the dataclass. Both are exit 1
    before the database is touched: a scoreboard computed over a guessed
    window is worse than none."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    return WeightsConfig(window_days=int(data["window_days"]),
                         horizon_days=int(data["horizon_days"]))


def write_and_log(conn, clock, cfg: WeightsConfig) -> dict:
    """Score tonight and report. Never raises on a scoring failure: the
    failure becomes one alert in the outbox and `failed: True` in the
    return, so main() can exit 0 and the perishable leg behind it runs."""
    try:
        out = write_weights(conn, clock, cfg)
    except Exception as exc:
        # write_weights rolled back before re-raising: no row for any seat.
        # The alert rides the same connection; if the DATABASE is what
        # failed it raises too, and a raise out of here would exit 1 and
        # hold back the perishable leg. Log it and return instead.
        text = (f"weights_job_failed — {type(exc).__name__}: {exc};"
                " no weights row written tonight, last good rows stand")
        try:
            run_day._alert(conn, clock, "weights_job_failed", text)
        except Exception as alert_exc:
            log(f"ALERT NOT WRITTEN ({type(alert_exc).__name__}:"
                f" {alert_exc}) — {text}")
        return {"failed": True, "written": [], "unchanged": [], "skipped": []}
    if out["skipped"]:
        # Same guard as above, inline rather than through a shared wrapper:
        # scripts/check_alert_codes.py checks the literal code at the call
        # of run_day._alert, and a wrapper of another name would leave its
        # callers unlinted (the lint's own documented edge).
        text = (f"weights_seat_skipped — {len(out['skipped'])} seat(s)"
                " had a non-finite load-bearing score and got no row"
                f" tonight: {', '.join(out['skipped'])}")
        try:
            run_day._alert(conn, clock, "weights_seat_skipped", text)
        except Exception as alert_exc:
            log(f"ALERT NOT WRITTEN ({type(alert_exc).__name__}:"
                f" {alert_exc}) — {text}")
    log(f"{out['as_of_date']} · written {', '.join(out['written']) or '—'}"
        f" · unchanged {', '.join(out['unchanged']) or '—'}"
        f" · skipped {', '.join(out['skipped']) or '—'}")
    return out


def main(argv: list[str] | None = None) -> int:
    import os

    from agents.wallclock import WallClock

    environ = os.environ
    run_day.paper_guard(environ)             # invariant 1, before anything else
    env = run_day.require_env(REQUIRED_ENV, environ)
    cfg = load_config(CONFIG_YAML)

    write_and_log(connect(env["FUND_DB"]), WallClock(), cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
