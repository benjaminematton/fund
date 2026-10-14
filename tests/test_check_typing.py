"""The typecheck ratchet (docs/superpowers/specs/
2026-10-13-agent-self-verification-design.md, piece 1). The pyright run is
injected in every test but one, so the ratchet's rules are pinned without
spending two seconds of pyright each."""

from __future__ import annotations

import json
from pathlib import Path

import scripts.check_typing as ct

ROOT = Path(__file__).resolve().parents[1]


def test_counts_collapses_diagnostics_to_per_file_totals():
    diags = {"a.py": ["1: x", "7: y"], "b/c.py": ["3: z"]}
    assert ct.counts(diags) == {"a.py": 2, "b/c.py": 1}


def test_compare_reports_regressions_and_improvements_separately():
    current = {"a.py": 3, "b.py": 1, "new.py": 1}
    baseline = {"a.py": 2, "b.py": 2, "gone.py": 1}
    regressions, improvements = ct.compare(current, baseline)
    assert regressions == ["a.py: 3 error(s), baseline 2",
                           "new.py: 1 error(s), baseline 0"]
    assert improvements == ["b.py: 1 error(s), baseline 2",
                            "gone.py: 0 error(s), baseline 1"]


def test_compare_is_quiet_when_equal():
    assert ct.compare({"a.py": 1}, {"a.py": 1}) == ([], [])


def _baseline(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "typing_baseline.json"
    p.write_text(json.dumps(data))
    return p


def test_main_fails_on_a_new_error_and_prints_the_diagnostic(tmp_path, capsys):
    base = _baseline(tmp_path, {"a.py": 1})
    run = lambda: {"a.py": ["1: old", "9: brand new error"]}
    assert ct.main([], run=run, baseline_path=base) == 1
    err = capsys.readouterr().err
    assert "a.py: 2 error(s), baseline 1" in err
    assert "9: brand new error" in err


def test_main_fails_when_the_baseline_was_lowered_below_reality(tmp_path):
    base = _baseline(tmp_path, {"a.py": 0})
    assert ct.main([], run=lambda: {"a.py": ["1: x"]}, baseline_path=base) == 1


def test_main_passes_and_nags_when_a_file_improved(tmp_path, capsys):
    base = _baseline(tmp_path, {"a.py": 2})
    assert ct.main([], run=lambda: {"a.py": ["1: x"]}, baseline_path=base) == 0
    out = capsys.readouterr().out
    assert "baseline can shrink" in out
    assert "a.py: 1 error(s), baseline 2" in out


def test_main_passes_quietly_at_baseline(tmp_path, capsys):
    base = _baseline(tmp_path, {"a.py": 1})
    assert ct.main([], run=lambda: {"a.py": ["1: x"]}, baseline_path=base) == 0
    assert "baseline can shrink" not in capsys.readouterr().out


def test_write_baseline_records_sorted_counts(tmp_path):
    base = tmp_path / "typing_baseline.json"
    run = lambda: {"z.py": ["1: a"], "a.py": ["1: b", "2: c"]}
    assert ct.main(["--write-baseline"], run=run, baseline_path=base) == 0
    assert base.read_text() == '{\n  "a.py": 2,\n  "z.py": 1\n}\n'


def test_run_pyright_finds_a_real_error_in_a_tmp_package(tmp_path):
    """One real pyright run, on a package of one file, so the JSON parsing
    and the --pythonpath handoff are exercised against the pinned binary."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\ninclude = ["pkg"]\ntypeCheckingMode = "standard"\n')
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.py").write_text('x: int = "not an int"\n')
    (tmp_path / "pkg" / "good.py").write_text("y: int = 1\n")
    diags = ct.run_pyright(root=tmp_path)
    assert list(diags) == ["pkg/bad.py"]
    assert len(diags["pkg/bad.py"]) == 1
    assert diags["pkg/bad.py"][0].startswith("1: ")


def test_committed_baseline_matches_the_tree():
    """The ratchet's own precondition: the baseline is never above reality.
    Below is allowed (someone fixed errors and forgot to shrink it)."""
    baseline = json.loads(ct.BASELINE.read_text())
    current = ct.counts(ct.run_pyright())
    regressions, _ = ct.compare(current, baseline)
    assert regressions == []
