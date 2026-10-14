#!/usr/bin/env python3
"""Typecheck ratchet, run by `make lint` (CLAUDE.md Commands) and CI.

Runs pyright over the packages in pyproject's [tool.pyright] include list and
compares per-file error counts to scripts/typing_baseline.json. Any file
above its baseline, or any file with errors the baseline does not list,
fails with the offending diagnostics. Counts below baseline pass and print a
reminder to commit the lower file. The baseline only moves down, by human
commit: `--write-baseline` is the one writer, and scripts/ is a tamper-guard
protected dir so the unattended loop cannot raise it.

The interpreter is handed to pyright explicitly, so one invocation resolves
the installed packages on a laptop (.venv), in CI (global pip) and on the
droplet.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "typing_baseline.json"


def include_dirs(root: Path) -> list[str]:
    """[tool.pyright].include from root/pyproject.toml; raises if any entry
    is not an existing directory, so a renamed package cannot silently fall
    out of the ratchet (pyright itself only warns and exits 0)."""
    with (root / "pyproject.toml").open("rb") as fh:
        include = tomllib.load(fh)["tool"]["pyright"]["include"]
    missing = [d for d in include if not (root / d).is_dir()]
    if missing:
        raise RuntimeError(
            f"[tool.pyright].include names missing directories: {missing}"
            " — fix pyproject.toml before trusting the baseline")
    return include


def run_pyright(root: Path = ROOT, python: str = sys.executable) -> dict[str, list[str]]:
    """Error diagnostics per file, repo-relative posix paths. Files with no
    errors are absent. Raises if pyright produced no report at all."""
    include_dirs(root)
    proc = subprocess.run(
        [python, "-m", "pyright", "--outputjson", "--pythonpath", python],
        cwd=str(root), capture_output=True, text=True)
    if not proc.stdout.strip():
        raise RuntimeError(
            f"pyright produced no JSON (exit {proc.returncode}):\n{proc.stderr}")
    report = json.loads(proc.stdout)
    if report["summary"]["filesAnalyzed"] == 0:
        raise RuntimeError("pyright analyzed 0 files: check [tool.pyright].include")
    diags: dict[str, list[str]] = {}
    for d in report["generalDiagnostics"]:
        if d["severity"] != "error":
            continue
        rel = Path(d["file"]).resolve().relative_to(root.resolve()).as_posix()
        line = d["range"]["start"]["line"] + 1
        diags.setdefault(rel, []).append(f"{line}: {d['message'].splitlines()[0]}")
    return diags


def counts(diags: dict[str, list[str]]) -> dict[str, int]:
    return {path: len(lines) for path, lines in diags.items()}


def compare(current: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    """(regressions, improvements), one line per file, sorted by path."""
    regressions: list[str] = []
    improvements: list[str] = []
    for path in sorted(set(current) | set(baseline)):
        now, was = current.get(path, 0), baseline.get(path, 0)
        line = f"{path}: {now} error(s), baseline {was}"
        if now > was:
            regressions.append(line)
        elif now < was:
            improvements.append(line)
    return regressions, improvements


def main(argv: list[str], run=run_pyright, baseline_path: Path = BASELINE) -> int:
    diags = run()
    current = counts(diags)
    if "--write-baseline" in argv:
        baseline_path.write_text(
            json.dumps(dict(sorted(current.items())), indent=2) + "\n")
        print(f"check_typing: wrote {baseline_path.name}: "
              f"{sum(current.values())} error(s) in {len(current)} file(s)")
        return 0
    baseline = json.loads(baseline_path.read_text())
    regressions, improvements = compare(current, baseline)
    if regressions:
        print("check_typing: type errors above baseline:", file=sys.stderr)
        regressed = [p for p in sorted(set(current) | set(baseline))
                     if current.get(p, 0) > baseline.get(p, 0)]
        for path, line in zip(regressed, regressions, strict=True):
            print("  " + line, file=sys.stderr)
            for diag in diags.get(path, []):
                print("      " + diag, file=sys.stderr)
        return 1
    if improvements:
        print("check_typing: baseline can shrink — commit the regenerated file"
              " (`.venv/bin/python3 scripts/check_typing.py --write-baseline`):")
        for line in improvements:
            print("  " + line)
    print(f"check_typing: ok ({sum(current.values())} known error(s),"
          f" baseline {sum(baseline.values())})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
