#!/usr/bin/env python3
"""Capture one real droplet read into a replay recording.

    make record-status          # writes tests/recordings/dev-status.json

WHY THIS EXISTS. `devcheck/`'s checks are thoroughly tested against Snapshots
built BY HAND in tests/test_devcheck_checks.py. Nothing tested what
`build_snapshot()` actually puts in them, and on 2026-09-02 that gap held six
separate defects at once — most sharply `check_degradations`, which had a
passing test feeding it `["pm_timeout"]` while the builder fed it event `kind`s
(`alert`/`digest`/`pnl`). Those sets never intersect, so the check was green on
every day the fund had ever run. The function was never wrong. Its input was,
and only the composition root could see that.

A hand-seeded fake droplet would not have caught it either — it just relocates
the hand-writing that caused the bug. So the fixture is REAL BYTES from the real
box, and writing assertions against it forces someone to read what production
actually sends. That reading is the step that was missing all six times.

This is Fowler's self-initializing fake, recorded once rather than lazily:
"the first time you call the fake it passes the call onto the actual remote
service, and as it returns the data it takes and saves a copy."

STALENESS IS HANDLED SEPARATELY, and that half is not optional. A recording
replayed forever drifts into being confidently wrong — the exact failure this
whole exercise is about. `tests/test_status_faithful.py` (live, `-m live`, run
by `make status-faithful`) re-reads the real droplet and checks the recording's
SHAPES still hold. That is the same job `make surface-pin` already does for the
broker: detection of WHEN an external contract moved, so it is a decision
someone makes rather than a fact someone discovers.

DOCTRINE, because #190 leaves it open. This recording is a golden INPUT, not a
golden output. Re-capturing it is not "changing an expected value to make a test
pass" — it is capturing reality again, and it is legitimate whenever
`make status-faithful` says the shapes moved. Relaxing an assertion in
tests/test_status_replay.py is the forbidden move. Inputs may be re-captured;
expectations may not be weakened.

READ-ONLY. This runs the same reads `make dev-status` runs and writes nothing to
the droplet.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.dev_status as ds  # noqa: E402

OUT = ROOT / "tests" / "recordings" / "dev-status.json"


def main() -> int:
    captured: dict[str, str | None] = {}

    def recording_transport(cmd: str, timeout: int = 15) -> str | None:
        out = ds._real_ssh(cmd, timeout)
        # Record the FIRST response per command. _ENV_CACHE means most commands
        # run once anyway; recording the first keeps the file deterministic if
        # that ever changes.
        captured.setdefault(cmd, out)
        return out

    with ds.using_transport(recording_transport):
        ds.build_snapshot()

    unreadable = [c for c, r in captured.items() if r is None]
    if unreadable:
        # Recording a failed read would bake "could not read" into the fixture
        # and the replay test would then assert against an outage.
        print("REFUSING TO WRITE: these reads failed, so the droplet was not "
              "fully readable when this ran:", file=sys.stderr)
        for cmd in unreadable:
            print(f"  {cmd}", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n")
    print(f"recorded {len(captured)} droplet reads -> {OUT.relative_to(ROOT)}")
    print("Now read the fixture and write assertions about what it actually "
          "contains — that reading is the point, not the file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
