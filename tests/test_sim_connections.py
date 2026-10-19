"""Issue #263: every SimResult a sim test opens must be closed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sim_day_tests_leak_no_database_connection():
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_sim_day.py", "-q",
         "-p", "no:cacheprovider",
         "-W", "error::ResourceWarning",
         "-W", "error::pytest.PytestUnraisableExceptionWarning"],
        cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-3000:]
