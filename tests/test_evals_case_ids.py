"""Case ids are unique within a seat directory — checked for EVERY seat.

`load_cases` returns a list ordered by filename. Consumers that key on `c.id`
collapse it to `{c.id: c}`, so last one wins: a new file sorting after an
existing one and reusing its `id:` silently replaces that case's expectation,
editing nothing. That is the "update an expected value to make a test pass"
CLAUDE.md forbids, performed without touching an expected value. Consumers
that keep the list instead (scripts/eval_suite.py:60,
scripts/dry_run_critic.py:72, most well-formedness loops) run and grade the
shadowed case twice.

The per-seat pins cannot see it, in two different disguises:
  - tests/test_evals_cases.py:19 compares a SET of ids, and a duplicate
    cannot change a set;
  - the critic pins all route through `expect["verdict"]` without ever
    pinning the vocabulary that field may take — :60-62 COUNT the canonical
    "objections" and "clear", and :110 and :137 are equality FILTERS. A
    shadow declaring a non-canonical verdict moves neither count and is
    skipped by every filter, so it passes all of them. That directory was
    unguarded, which is what issue #67 demonstrated.

Neither is a check for duplicate ids, and both are per-directory. This
enumerates the directories under evals/cases instead of naming them, so a
third seat's set is pinned the day it lands rather than when someone
remembers. `load_cases` raises as well, which protects call sites CI never
runs — `scripts/critic_gate.py` is a gate invoked by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.cases import load_cases

CASES = Path(__file__).resolve().parents[1] / "evals/cases"


def test_load_cases_accepts_every_shipped_seat_directory():
    """What this actually pins, now that load_cases raises: the shipped
    directories load clean. The raise below fires first, so the uniqueness
    assertion is unreachable in practice — it is kept as the second half of a
    pair, so that removing the raise AND this assertion is still caught by
    test_load_cases_refuses_a_duplicate_id."""
    directories = sorted(p for p in CASES.iterdir() if p.is_dir())
    assert directories, f"no seat case directories under {CASES}"
    for directory in directories:
        cases = load_cases(directory)
        assert cases, f"no cases under {directory}"
        ids = [c.id for c in cases]
        assert len(ids) == len({c.id for c in cases}), \
            f"{directory.name}: duplicated case ids" \
            f" {sorted({i for i in ids if ids.count(i) > 1})}"


def test_load_cases_refuses_a_duplicate_id(tmp_path):
    """An operator acts on this message's text, so it pins both halves: the
    id, and the ORDER of the two paths. The fixture ids deliberately differ
    from their filenames — otherwise `"<id>" in message` would be satisfied
    by the paths alone, and dropping the id from the message would go
    unnoticed. Order matters because the message's own prose calls one file
    "the second"; reversed, it points at the wrong file to delete."""
    body = (CASES / "pm/b01.yaml").read_text().replace(
        "id: b01", "id: shadow-me", 1)
    first, second = tmp_path / "aa.yaml", tmp_path / "zz.yaml"
    first.write_text(body)
    second.write_text(body)

    with pytest.raises(ValueError) as excinfo:
        load_cases(tmp_path)

    message = str(excinfo.value)
    assert "shadow-me" in message, "the message must name the shadowed id"
    assert str(first) in message and str(second) in message
    assert message.index(str(first)) < message.index(str(second)), \
        "the later file is the shadow; a reversed message blames the original"
