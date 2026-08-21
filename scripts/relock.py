#!/usr/bin/env python3
"""Regenerate requirements.lock from pyproject.toml's ranges (`make deps-relock`).

Resolves the ranges in a throwaway venv and freezes the result, so relocking
never depends on — or disturbs — whatever .venv happens to hold. The committed
header is carried across, because it is where the regeneration instructions
live. Run `make test` afterwards: the lock is only as good as the suite that
ran against it.
"""

import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "requirements.lock"

# The installer, not dependencies of the fund: freezing them pins the tool
# doing the pinning, and a fresh venv gets its own anyway.
INSTALLER = {"pip", "setuptools", "wheel", "distribute", "pkg-resources", "uv"}

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)$")


def existing_header(lock: Path = LOCK) -> str:
    """The committed comment block at the top of the lock, or '' if absent."""
    if not lock.exists():
        return ""
    header = []
    for line in lock.read_text().splitlines():
        if not line.startswith("#"):
            break
        header.append(line)
    return "".join(f"{line}\n" for line in header)


def render_lock(header: str, freeze: str) -> str:
    """Header plus every exact pin from `pip freeze`, sorted, installer dropped."""
    pins = []
    for raw in freeze.splitlines():
        m = PIN.match(raw.strip())
        if m and m.group(1).lower() not in INSTALLER:
            pins.append(m.group(0))
    pins.sort(key=str.lower)
    return header + "".join(f"{pin}\n" for pin in pins)


def main() -> int:
    project = tomllib.load(PYPROJECT.open("rb"))["project"]
    deps = project["dependencies"] + project["optional-dependencies"]["dev"]

    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
        python = venv / "bin" / "python3"
        subprocess.check_call(
            [str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        subprocess.check_call([str(python), "-m", "pip", "install", "--quiet", *deps])
        freeze = subprocess.check_output([str(python), "-m", "pip", "freeze"], text=True)

    LOCK.write_text(render_lock(existing_header(), freeze))
    print(f"relock: wrote {LOCK.relative_to(ROOT)} "
          f"({sum(1 for line in LOCK.read_text().splitlines() if '==' in line)} pins)")
    print("relock: run `make test`, then commit pyproject.toml and the lock together.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
