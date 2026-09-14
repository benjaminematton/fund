"""state/worklist.py — Lane B row ops (resident-seats R1, #228)."""
import re

import pytest

from fundbt.hashing import work_id

NOW = "2026-07-06T15:30:00+00:00"
LATER = "2026-07-06T20:00:00+00:00"


def test_work_id_is_deterministic_prefixed_and_keyed_on_all_three_parts():
    a = work_id("spec_review", "spec_abc", "0")
    assert re.fullmatch(r"wk_[0-9a-f]{16}", a)
    assert a == work_id("spec_review", "spec_abc", "0")
    assert a != work_id("spec_review", "spec_abc", "1")      # attempts differ
    assert a != work_id("alert_triage", "spec_abc", "0")     # kind differs
    assert a != work_id("spec_review", "spec_xyz", "0")      # subject differs
