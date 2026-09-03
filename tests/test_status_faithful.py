"""Is tests/recordings/dev-status.json still a faithful double of the droplet?

    make status-faithful

WHY THIS EXISTS, and it is not optional. tests/test_status_replay.py builds a
Snapshot from recorded production bytes, which is what finally covers the
builder. But a recording replayed forever drifts into being confidently wrong —
the exact failure the whole exercise is about. Fowler's answer to that is a
second suite that calls the real service "and check[s] that they returned the
same value that was saved"; the general principle is that contract tests are
what keep a test double faithful.

This repo already built that pattern for the OTHER external service and never
applied it to this one. `make surface-pin`: "DETECTION, not protection... says
WHEN the surface moved, so a new mutating verb is a decision someone makes
rather than a fact someone discovers." Same job, same posture, different
boundary.

SHAPES, NOT VALUES. Counts, dates and ids change every day; asserting them would
make this red daily and train the reader to skip it. What must not change
silently is the SHAPE of the reply — above all a SQL column set, because
`kind` vs `code` is precisely the drift that cost three days.

READ-ONLY, and `-m live` so it never runs in `make test`.

WHEN THIS FAILS: re-record with `make record-status`, then read the new fixture
and update tests/test_status_replay.py's assertions to match reality. That is a
legitimate re-capture of a golden INPUT, which #190's doctrine gap does not
cover and which this file is the trigger for. Weakening a replay assertion to
make it pass is the forbidden move — inputs may be re-captured, expectations may
not be weakened.
"""

import json
from pathlib import Path

import pytest

import scripts.dev_status as ds

RECORDING = Path(__file__).resolve().parents[1] / "tests/recordings/dev-status.json"

pytestmark = pytest.mark.live


def _columns(raw: str | None) -> set[str] | None:
    """The column set of a `sqlite3 -json` reply, or None if it is not one."""
    if not raw or not raw.strip():
        return None
    try:
        rows = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return set(rows[0].keys())


def test_every_recorded_command_still_answers():
    """A command the droplet no longer accepts is a builder reading a world that
    has moved — the recording would keep the suite green over it."""
    recorded = json.loads(RECORDING.read_text())
    dead = [cmd for cmd in recorded if ds._real_ssh(cmd, 20) is None]
    assert not dead, (
        "these recorded reads no longer answer on the droplet:\n  "
        + "\n  ".join(dead)
        + "\nRe-record with `make record-status` and update the replay assertions.")


def test_sql_column_sets_have_not_moved():
    """The `kind` vs `code` class, caught directly.

    A renamed or dropped column keeps every offline test green — the replay
    fixture still holds the old shape — while production sends the new one.
    This is the only thing that notices.
    """
    recorded = json.loads(RECORDING.read_text())
    moved = []
    for cmd, saved in recorded.items():
        if "sqlite3 -json" not in cmd:
            continue
        was = _columns(saved)
        if was is None:
            continue                      # empty result then; nothing to compare
        now = _columns(ds._real_ssh(cmd, 20))
        if now is None:
            continue                      # empty result now; not a shape change
        if was != now:
            moved.append(f"{cmd[:70]}...\n    recorded {sorted(was)} -> live {sorted(now)}")
    assert not moved, "SQL column sets moved since the recording:\n  " + "\n  ".join(moved)


def test_the_recording_is_not_absurdly_old():
    """Not a correctness check — a nudge. The two tests above are the real
    guards; this one exists because a fixture nobody has re-read in months
    stops being evidence and starts being furniture."""
    import subprocess

    out = subprocess.run(
        ["git", "log", "-1", "--format=%cr", "--", str(RECORDING)],
        capture_output=True, text=True, cwd=str(RECORDING.parents[2]))
    age = out.stdout.strip()
    assert age, "the recording is not committed, so its age cannot be read"
    assert "year" not in age, (
        f"the droplet recording was last captured {age}. The shape checks above "
        "may still pass while the fixture has stopped describing anything anyone "
        "recognises. Re-record and re-read it.")
