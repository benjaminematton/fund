"""Append-only per-seat journals — the sole journal writer (CLAUDE.md:
"Do not write journals except through state/journal.py"). Plain markdown
files under `root`, one per seat, entries separated by `## {run_date}`
headers. No rotation, no locking, no index, no delete."""

from __future__ import annotations

from pathlib import Path


def append_entry(root: Path | str, seat: str, run_date: str, text: str) -> None:
    """Append `## {run_date}\\n{text}\\n` to `<root>/<seat>.md`. Never rewrites
    or truncates an existing file."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{seat}.md"
    with path.open("a") as f:
        f.write(f"\n## {run_date}\n{text}\n")


def recent_entries(root: Path | str, seat: str, limit: int = 5) -> str:
    """Return the last `limit` `## `-delimited sections of `<seat>.md`, most
    recent last. Empty string if the journal doesn't exist yet or has no
    entries."""
    path = Path(root) / f"{seat}.md"
    if not path.exists():
        return ""
    sections = [s for s in path.read_text().split("\n## ") if s.strip()]
    return "".join(f"\n## {s}" for s in sections[-limit:])
