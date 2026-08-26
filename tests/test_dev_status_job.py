"""Offline tests for the dev-status job's seams.

scripts/dev_status.py is a composition root like scripts/resolve_day.py, so
main() is never called here — it opens ssh connections and a broker client.
What is pinned is what the job DEPENDS on: every dependency it declares is a
way for the job to go silent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev_status.py"


def _load():
    spec = importlib.util.spec_from_file_location("dev_status", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_exists():
    assert SCRIPT.exists()


def test_exposes_build_snapshot_and_main():
    m = _load()
    assert callable(m.build_snapshot)
    assert callable(m.main)


def test_reads_suppression_from_health_descriptor(tmp_path):
    """The descriptor's front matter is the only source of suppression."""
    m = _load()
    health = tmp_path / "health.md"
    health.write_text(
        "---\n"
        "health_command: make dev-status\n"
        "suppress:\n"
        "  - degradations\n"
        "---\n\n"
        "# prose\n"
    )
    assert m.read_suppressed(health) == frozenset({"degradations"})


def test_missing_descriptor_suppresses_nothing(tmp_path):
    """Negative control: no file means no suppression, never a crash."""
    m = _load()
    assert m.read_suppressed(tmp_path / "absent.md") == frozenset()
