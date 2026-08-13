from pathlib import Path
from state.journal import append_entry, recent_entries

def test_append_and_recent(tmp_path):
    root = tmp_path / "journals"
    append_entry(root, "pm", "2026-07-06", "NVDA buy 67 — capex thesis.")
    append_entry(root, "pm", "2026-07-07", "Held; thesis intact.")
    text = recent_entries(root, "pm", limit=1)
    assert "2026-07-07" in text and "2026-07-06" not in text
    assert (root / "pm.md").exists()

def test_append_only_no_rewrites(tmp_path):
    root = tmp_path / "journals"
    append_entry(root, "pm", "2026-07-06", "first")
    before = (root / "pm.md").read_text()
    append_entry(root, "pm", "2026-07-06", "second")
    assert (root / "pm.md").read_text().startswith(before)

def test_recent_entries_zero_limit(tmp_path):
    root = tmp_path / "journals"
    append_entry(root, "pm", "2026-07-06", "NVDA buy 67 — capex thesis.")
    append_entry(root, "pm", "2026-07-07", "Held; thesis intact.")
    text = recent_entries(root, "pm", limit=0)
    assert text == ""

def test_recent_entries_negative_limit(tmp_path):
    root = tmp_path / "journals"
    append_entry(root, "pm", "2026-07-06", "NVDA buy 67 — capex thesis.")
    append_entry(root, "pm", "2026-07-07", "Held; thesis intact.")
    text = recent_entries(root, "pm", limit=-1)
    assert text == ""
