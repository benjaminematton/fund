# Phase 2b (b) — Reflections Into the PM Journal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close `specs/improvement.md` §8 (b) — the journal half of #57: a reflection written by the nightly reflect turn is projected into the deciding seat's journal (the PM's), through `state/journal.py:append_entry` only, so the next morning's `journal` brief section carries the resolution's frame and prose.

**Architecture:** One pure projection function in `orchestrator/reflect.py`: an **unwindowed** sweep over `resolutions.reflection IS NOT NULL`, idempotent by a **code-built, line-anchored marker** (`— reflection · {decided} · {ticker}`), writing **one aggregated section per sweep** under a header (`## {night} · reflections`) that can never collide with `run_close`'s `## {date}` idempotence check. Called from `scripts/reflect_day.py`'s `finally`, guarded, beside the drain. No DDL, no new tool, no charter change, no config, no new alert. The Slack-thread sink (#57's other half) stays deferred (design.md §3, #204's amendment) and is not here.

Three design choices, each an answer to a demonstrated failure from this plan's first review:

1. **Unwindowed sweep.** The turn loop's `REFLECT_LOOKBACK_DAYS` window exists because *turns cost money*; journal appends are free. A windowed sweep created a silent permanent-loss class (a reflection written, its projection failing, the window moving past it — demonstrated). Unwindowed, any later fire catches up, and the deploy-day backfill is bounded by the fund's whole history (38 decisions ever). The marker check is what bounds work, not a date.
2. **Line-anchored synthetic marker, not a frame substring.** A whole-file substring scan on the frame's first line was demonstrated suppressible by prose that quotes another decision's frame — which seats naturally do. The marker is a string no seat ever sees (frames don't contain it), matched only as a complete line (`re.M`), and unique per decision (`decisions` UNIQUE `(run_date, ticker)`, `state/schema.sql:63`; `resolutions.decision_id` UNIQUE, `:99`). Residual: a seat that emits the exact marker line, character-for-character at line start, inside prose could still suppress — named in the docstring as accepted, since prose never legitimately contains the `— reflection · ` prefix.
3. **Seat prose is defanged before it enters the file.** This lane introduces the journal's first seat-authored free text — every existing writer emits code-built lines — and two consumers treat the file's raw bytes as structure: `_append_entry_once`'s substring key (`"## {date}\n"`, even mid-line) and `recent_entries`' `"\n## "` split. Prose containing `## ` was demonstrated to forge the first and fragment the second. Fix at the single entry point: every run of two-or-more `#` in the reflection text is collapsed to one (`re.sub(r"#{2,}", "#", …)`) as it is journaled. The frame is code-built and never contains `#`, so acceptance (b)'s "frame and prose" is satisfied with this one documented transformation; the DB keeps the untouched original.
4. **One section per sweep, header `## {night} · reflections`.** `orchestrator/daily.py:430-438`'s `_append_entry_once` checks `f"## {run_date}\n" in file` — a bare-dated reflections section written by the 16:35 job before a crashed trading day resumes would make `run_close` skip the PM's decision line for that day, forever (demonstrated mechanism). The ` · reflections` suffix means neither writer's check can match the other's sections. Aggregation also keeps a multi-reflection night from evicting the PM's own daily record out of the brief's 3-section `journal` window (`JOURNAL_ENTRIES = 3`, `fund_server.py`).

**Tech Stack:** Python 3.12, existing `state/journal.py` and `orchestrator/reflect.py`, pytest. No new dependencies.

## Global Constraints

- **Journals are written only through `state/journal.py`** (CLAUDE.md "Do NOT"; `improvement.md` §0.4: append-only, nothing rewrites history).
- **`orchestrator/` is purity-linted**: no LLM imports, no wall clock. (`state.journal` is importable there — `orchestrator/daily.py:29` already does, lint clean.)
- **Default is no-change / the night continues** (invariant 4): a projection failure must not lose the drain, the wrote-nothing rollup, or the night's exit code.
- **No per-run values in prompts**: journals are injected into prompts, so no `decision_id` (or any surrogate id) may enter the journal text. The marker is built from `run_date` and `ticker` — both already in the frame.
- **Acceptance item (b)** (`specs/acceptance.md` Phase 2b): "after resolve + reflect, the next morning's `journal` section for the seat that made the decision (the PM) contains that resolution's frame and prose, appended via `state/journal.py:append_entry` only."
- **PR body: no closing keyword adjacent to any issue number** (PR #210 closed #205 on a negated sentence). #57 and #205 both stay open.
- Baseline at branch point `64e1a0e`: **1808 passed, 1 skipped**. `make test` before every commit; no Co-Authored-By trailer; conventional commits; surgical diffs.

## File structure

| Path | Responsibility | Action |
|---|---|---|
| `orchestrator/reflect.py` | `DECIDING_SEAT`, `REFLECTION_MARKER_PREFIX`, `journal_reflections(conn, journals_root, *, run_date) -> dict` | Modify (append; `from state.journal import append_entry` joins the module imports) |
| `scripts/reflect_day.py` | `journals_root_from(environ)`, `reflect_and_log(..., journals_root=None)` calls the sweep in `finally` (after the rollup append, before the drain), guarded; `main()` threads it | Modify |
| `agents/tools/fund_server.py:481-482` | `handle_submit_reflection` docstring: the journal-projection deferral note becomes true history, not a false present | Modify (docstring only — an orphan of this change) |
| `specs/design.md:104` (Nightly 1 row), `specs/contracts.md:349`, `specs/acceptance.md` item (b) | say the journal projection exists; threads still deferred | Modify |
| `tests/test_reflect.py` | the projection: content, marker idempotence, prose-quote resistance, aggregation, backfill, blank guard | Modify (append) |
| `tests/test_reflect_job.py` | wiring: journaled on a written night, `finally` placement pinned by a propagating frame error, drain preserved under a projection failure (asserted on a queued alert's `posted_at`), unbound root named, morning brief carries it | Modify (append) |

## Scope check

One subsystem, one lane. Deliberately not here: the Slack-thread sink (#57's other half — deferred by `specs/design.md` §3; whether it survives at all is Benjamin's decision, recorded in the PR body); journal-growth control ((c), lessons distillation); any new alert code (the unwindowed sweep removes the aged-out class the first draft would have needed one for).

---

### Task 1: `journal_reflections` — the projection

**Files:**
- Modify: `orchestrator/reflect.py` (append after `store_reflection`; add `import re` and `from state.journal import append_entry` to the module imports — note the module currently imports nothing from `state`, so this is a new import line, lint-clean per `orchestrator/daily.py:29`'s precedent)
- Test: `tests/test_reflect.py` (append)

**Interfaces:**
- Consumes: `state.journal.append_entry(root, seat, run_date, text)`; `resolutions` + `decisions` rows.
- Produces: `DECIDING_SEAT = "pm"`; `REFLECTION_MARKER_PREFIX = "— reflection · "`; `journal_reflections(conn, journals_root, *, run_date: str) -> dict` with keys `journaled`, `already`, `blank` (ints). One `append_entry` call per invocation at most (the aggregated section); zero when nothing is new.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reflect.py` (the file has no bare-connection fixture — its `resolved` fixture builds a full scenario — so the snippet brings its own):

```python
# --- the journal projection (#57's journal half; improvement.md §8 (b)) ------

import re

from orchestrator.reflect import (DECIDING_SEAT, REFLECTION_MARKER_PREFIX,
                                  journal_reflections)

NIGHT = "2026-08-25"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "fund.sqlite")
    yield c
    c.close()


def _reflected(conn, *, ticker="NVDA", run_date="2026-08-18",
               resolved_at="2026-08-25T20:35:05+00:00", prose="cut earlier"):
    """A resolved decision whose reflection is already stored — the state the
    projection consumes. Returns (decision_id, stored_text)."""
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " (?,?,'buy',96,'t','i','executed',?)",
        (run_date, ticker, f"{run_date}T15:00:00+00:00"))
    did = cur.lastrowid
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at)"
        " VALUES (?, 5, 0.0614, 0.0504, 0, ?)", (did, resolved_at))
    conn.commit()
    frame = reflection_frame(conn, did)
    store_reflection(conn, did, frame, prose)
    return did, conn.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (did,)).fetchone()["reflection"]


def _pm_journal(root):
    return (root / f"{DECIDING_SEAT}.md").read_text()


def test_projection_appends_frame_and_prose_under_a_reflections_header(
        conn, tmp_path):
    _, stored = _reflected(conn)
    root = tmp_path / "journals"

    out = journal_reflections(conn, root, run_date=NIGHT)

    assert out == {"journaled": 1, "already": 0, "blank": 0}
    text = _pm_journal(root)
    assert f"## {NIGHT} · reflections" in text
    assert stored in text                       # frame AND prose, verbatim
    assert "cut earlier" in text
    # The header run_close's _append_entry_once checks for must NOT appear:
    # a bare "## {date}\n" here would make a crash-resumed trading day skip
    # the PM's decision line for that date, forever (orchestrator/daily.py
    # _append_entry_once).
    assert f"## {NIGHT}\n" not in text


def test_projection_is_idempotent_by_the_marker_line(conn, tmp_path):
    _reflected(conn)
    root = tmp_path / "journals"
    journal_reflections(conn, root, run_date=NIGHT)
    before = _pm_journal(root)

    out = journal_reflections(conn, root, run_date=NIGHT)

    assert out == {"journaled": 0, "already": 1, "blank": 0}
    assert _pm_journal(root) == before
    marker = f"{REFLECTION_MARKER_PREFIX}2026-08-18 · NVDA"
    assert len(re.findall(rf"^{re.escape(marker)}$", before, re.M)) == 1


def test_prose_quoting_another_frames_header_suppresses_nothing(
        conn, tmp_path):
    """The first draft's marker was the frame's first line, scanned as a
    substring — and a reflection whose PROSE quoted another decision's frame
    verbatim silently suppressed that decision's projection (demonstrated in
    review). The marker is now a synthetic line seats never see; a quoted
    frame header is inert."""
    root = tmp_path / "journals"
    _, stored_a = _reflected(conn, ticker="NVDA")
    journal_reflections(conn, root, run_date=NIGHT)
    # B's prose quotes A's frame header — the exact attack that worked before.
    _, stored_b = _reflected(
        conn, ticker="MSFT", prose=f"unlike {stored_a.splitlines()[0]}, hold")

    out = journal_reflections(conn, root, run_date=NIGHT)

    assert out["journaled"] == 1
    assert stored_b in _pm_journal(root)


def test_one_night_many_reflections_is_one_section(conn, tmp_path):
    """Aggregation is load-bearing twice over: the brief's journal window is
    JOURNAL_ENTRIES = 3 sections, so per-reflection sections would evict the
    PM's own daily record on any 3-reflection night; and one section per
    sweep is one append_entry call."""
    _reflected(conn, ticker="NVDA")
    _reflected(conn, ticker="MSFT", prose="sized too big")
    root = tmp_path / "journals"

    out = journal_reflections(conn, root, run_date=NIGHT)

    text = _pm_journal(root)
    assert out["journaled"] == 2
    assert text.count("## ") == 1
    assert "NVDA" in text and "MSFT" in text and "sized too big" in text


def test_the_sweep_is_unwindowed_so_a_missed_fire_catches_up(conn, tmp_path):
    """The turn loop is windowed because turns cost money; appends are free.
    A reflection written long ago (a projection failure, a deploy onto
    history) is journaled by the NEXT sweep, whenever it runs — there is no
    aged-out class here, which is the point."""
    _reflected(conn, run_date="2026-07-06",
               resolved_at="2026-07-13T20:35:05+00:00")   # weeks old

    out = journal_reflections(conn, tmp_path / "journals", run_date=NIGHT)

    assert out["journaled"] == 1
    assert "2026-07-06" in _pm_journal(tmp_path / "journals")


def test_blank_or_unwritten_reflections_are_not_projected(conn, tmp_path):
    """reflection IS NULL means the seat has not spoken; a whitespace-only
    text (unreachable through store_reflection today, but a DB is writable
    by more than one future path) is counted, not appended and not crashed
    on — projecting a blank would invent a record (invariant 4)."""
    conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-08-18','AMD','buy',10,'t','i','executed','x')")
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, reflection, resolved_at) VALUES"
        " (last_insert_rowid(), 5, 0.01, 0.01, 0, '  ',"
        " '2026-08-25T20:35:05+00:00')")
    conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-08-19','INTC','buy',10,'t','i','executed','x')")
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at) VALUES"
        " (last_insert_rowid(), 5, 0.01, 0.01, 0, '2026-08-25T20:35:06+00:00')")
    conn.commit()

    out = journal_reflections(conn, tmp_path / "journals", run_date=NIGHT)

    assert out == {"journaled": 0, "already": 0, "blank": 1}
    assert not (tmp_path / "journals" / f"{DECIDING_SEAT}.md").exists()


def test_prose_markdown_headings_cannot_forge_headers_or_split_sections(
        conn, tmp_path):
    """This file's first free text meets two consumers that read raw bytes:
    _append_entry_once's substring key ("## {date}\\n", matched even
    mid-line — a forged FUTURE date would make run_close silently skip that
    day's PM record forever) and recent_entries' "\\n## " split (a prose
    heading fragments the aggregated section and eats the brief's 3-section
    window). Both demonstrated in review; both die at the defang."""
    from state.journal import recent_entries

    _reflected(conn, prose="lesson:\n## 2026-12-01\nand mid-line ## 2026-12-02\nhold")
    root = tmp_path / "journals"

    journal_reflections(conn, root, run_date=NIGHT)

    text = _pm_journal(root)
    assert "## 2026-12-01" not in text and "## 2026-12-02" not in text
    assert "# 2026-12-01" in text                    # prose kept, defanged
    assert text.count("\n## ") == 1                  # one real section
    assert "hold" in recent_entries(root, DECIDING_SEAT, 1)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_reflect.py -k "projection or reflections or sweep or blank or prose" -v`
Expected: FAIL at import — `ImportError: cannot import name 'DECIDING_SEAT'`.

- [ ] **Step 3: Implement**

In `orchestrator/reflect.py`, add to the module imports:

```python
import re
from pathlib import Path

from state.journal import append_entry
```

Append after `store_reflection`:

```python
# The seat whose journal a reflection lands in. `decisions` carries no seat
# column — the PM is the only seat that decides (design.md §2), and
# acceptance (b) names it. A second deciding seat would need a column, not a
# guess; this constant is where that change arrives.
DECIDING_SEAT = "pm"

# The idempotence marker's prefix. A SYNTHETIC line, deliberately not the
# frame's first line: seats quote frames in prose (they are handed one every
# reflect turn), and a substring scan on a frame header was demonstrated
# suppressible by exactly such a quote. No seat whose text reaches this
# journal ever sees the prefix before writing (the PM reads it in later
# briefs, but the PM's own journal writes are code-built), and the check
# below matches it only as a complete line — the residual (prose emitting
# the exact marker line, character-for-character at line start) is accepted
# and would take deliberate construction, not a natural quote.
REFLECTION_MARKER_PREFIX = "— reflection · "

# Every written reflection, with the decision fields the marker is built
# from. Unwindowed on purpose — see journal_reflections. Ordered for a
# deterministic section body.
_WRITTEN = """
SELECT r.decision_id, r.reflection, d.run_date AS decided, d.ticker
  FROM resolutions r
  JOIN decisions d ON d.id = r.decision_id
 WHERE r.reflection IS NOT NULL
 ORDER BY r.decision_id
"""


def journal_reflections(conn, journals_root, *, run_date: str) -> dict:
    """Project every written, not-yet-journaled reflection into the deciding
    seat's journal (#57's journal half; improvement.md §8 (b)). Returns
    {"journaled": n, "already": n, "blank": n} for the job log.

    UNWINDOWED, unlike the turn loop above it: the lookback window exists
    because turns cost money, and appends cost nothing. A windowed sweep
    would mint a silent-loss class — a reflection written, its projection
    failing once, the window moving past it — with no alarm anywhere. The
    marker check is what bounds the work instead: only unjournaled rows
    append, and the whole history is a few hundred rows a year.

    Idempotent by a code-built marker line, "— reflection · {decided} ·
    {ticker}", matched only as a complete line (re.M). (decided, ticker) is
    unique per decision — decisions UNIQUE (run_date, ticker), resolutions
    one per decision — and prompt-safe: both values already appear in the
    frame, and no decision_id enters the journal (CLAUDE.md: journals are
    injected into prompts).

    One aggregated section per call, under "## {run_date} · reflections".
    The suffix is load-bearing: orchestrator/daily.py's _append_entry_once
    keys the trading day's PM journal write on the BARE "## {date}\\n"
    header, so a bare-dated section written here (the 16:35 job can run
    before a crashed trading day resumes) would make run_close skip that
    day's decision line forever. Neither writer's check can match the
    other's sections. Aggregation also keeps a multi-reflection night from
    evicting the PM's own daily record out of the brief's JOURNAL_ENTRIES-
    section window.

    Appends through state/journal.py only; the file is read once for the
    marker scan; append_entry never rewrites (improvement.md §0.4).
    """
    rows = conn.execute(_WRITTEN).fetchall()
    out = {"journaled": 0, "already": 0, "blank": 0}
    if not rows:
        return out
    path = Path(journals_root) / f"{DECIDING_SEAT}.md"
    existing = path.read_text() if path.exists() else ""
    pieces = []
    for row in rows:
        if not (row["reflection"] or "").strip():
            out["blank"] += 1
            continue
        marker = f"{REFLECTION_MARKER_PREFIX}{row['decided']} · {row['ticker']}"
        if re.search(rf"^{re.escape(marker)}$", existing, re.M):
            out["already"] += 1
            continue
        # Defang before the file's structural consumers can see it: prose is
        # the first free text this file has ever held, and both
        # _append_entry_once (substring "## {date}\n", even mid-line) and
        # recent_entries ("\n## " split) read raw bytes. Collapsing every
        # #-run to one "#" kills both triggers everywhere while keeping the
        # prose readable; the frame is code-built and never contains "#",
        # and the DB keeps the untouched original.
        defanged = re.sub(r"#{2,}", "#", row["reflection"])
        pieces.append(f"{marker}\n{defanged}")
        out["journaled"] += 1
    if pieces:
        append_entry(journals_root, DECIDING_SEAT,
                     f"{run_date} · reflections", "\n\n".join(pieces))
    return out
```

- [ ] **Step 4: Run the tests and the purity lint**

Run: `.venv/bin/python3 -m pytest tests/test_reflect.py -v && .venv/bin/python3 scripts/check_purity.py`
Expected: PASS, all (the file's existing 13 too); lint clean.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/reflect.py tests/test_reflect.py
git commit -m "feat(orchestrator): journal_reflections projects written reflections into the PM journal"
```

---

### Task 2: Wire it into the nightly job

**Files:**
- Modify: `scripts/reflect_day.py` (`reflect_and_log` signature + `finally`; new `journals_root_from`; `main()`)
- Test: `tests/test_reflect_job.py` (append)

**Interfaces:**
- Consumes: Task 1's `journal_reflections`.
- Produces: `reflect_and_log(conn, slack, clock, run_turn, journals_root=None)`; `journals_root_from(environ) -> Path` (mirrors `scripts/run_day.py:677-678`: `FUND_JOURNALS` or `ROOT / "journals"`, mkdir'd).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reflect_job.py`:

```python
# --- the journal projection rides the same night (#57's journal half) --------

from orchestrator.reflect import DECIDING_SEAT
from orchestrator.reflect import store_reflection as _store


def _writing_turn(conn):
    """A run_turn that behaves: stores frame + prose for the decision it was
    handed, the way a real seat turn does through submit_reflection."""
    def run_turn(job):
        _store(conn, job["decision_id"], job["frame"], "trim next time")
    return run_turn


def test_a_written_reflection_is_journaled_the_same_night(db, tmp_path):
    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")
    root = tmp_path / "journals"

    reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                _writing_turn(db), journals_root=root)

    text = (root / f"{DECIDING_SEAT}.md").read_text()
    assert "trim next time" in text
    assert "NVDA" in text and "+6.14%" in text          # the frame's numbers


def test_the_projection_runs_in_the_finally_a_frame_error_proves_it(
        db, tmp_path, monkeypatch):
    """A run_turn raise is caught inside the loop and never reaches the
    finally, so it cannot pin placement (first-draft mistake, demonstrated
    in review). What DOES abort the loop is a later decision's
    reflection_frame raising — the same abort test_the_wrote_nothing_rollup_
    survives_a_later_frame_error models. The first decision's written
    reflection must still be journaled on the way out."""
    _resolved(db, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    _resolved(db, ticker="AMD", resolved_at="2026-08-25T20:35:06+00:00")
    root = tmp_path / "journals"

    real_frame = reflect_day.reflection_frame

    def flaky_frame(conn, decision_id):
        row = conn.execute("SELECT ticker FROM decisions WHERE id = ?",
                           (decision_id,)).fetchone()
        if row["ticker"] == "AMD":
            raise sqlite3.OperationalError("database is locked")
        return real_frame(conn, decision_id)
    monkeypatch.setattr(reflect_day, "reflection_frame", flaky_frame)

    with pytest.raises(sqlite3.OperationalError):
        reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                    _writing_turn(db), journals_root=root)

    text = (root / f"{DECIDING_SEAT}.md").read_text()
    assert "trim next time" in text and "NVDA" in text


def test_a_projection_failure_does_not_eat_the_rollup_or_the_drain(
        db, tmp_path, monkeypatch):
    """Guarded like everything else in the finally. The assertion is on a
    QUEUED alert actually reaching posted_at NOT NULL — the first draft's
    always-true COUNT(*) assertion survived deleting the drain entirely
    (demonstrated in review). A turn that writes nothing queues the
    wrote-nothing rollup in the same finally; the projection raising after
    it must not stop the drain posting it."""
    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")

    def boom(conn, root, *, run_date):
        raise OSError("read-only filesystem")
    monkeypatch.setattr(reflect_day, "journal_reflections", boom)

    reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                lambda job: None,        # writes nothing
                                journals_root=tmp_path / "journals")

    drained = [r["payload"] for r in db.execute(
        "SELECT payload FROM events WHERE kind = 'alert'"
        " AND posted_at IS NOT NULL")]
    assert any("reflect_turn_wrote_nothing" in t for t in drained)
    assert db.execute("SELECT COUNT(*) c FROM events"
                      " WHERE posted_at IS NULL").fetchone()["c"] == 0


def test_an_unbound_journals_root_is_a_named_skip_not_a_crash(db, capsys):
    """Tests and older callers pass no root; production always does (main
    threads journals_root_from). Unbound is logged by name so a wiring
    regression is visible in journald, and the night otherwise proceeds."""
    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")

    reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                _writing_turn(db))

    assert "journals_root unbound — reflections not journaled" \
        in capsys.readouterr().out


def test_journals_root_from_env_and_default(tmp_path):
    root = reflect_day.journals_root_from({"FUND_JOURNALS": str(tmp_path / "j")})
    assert root == tmp_path / "j" and root.is_dir()
    assert reflect_day.journals_root_from({}) == reflect_day.ROOT / "journals"


def test_next_morning_the_pm_brief_carries_the_reflection(db, tmp_path):
    """Acceptance (b), end to end on the rendered brief: reflect at night,
    read the PM's journal section in the morning through the real handler."""
    from agents.tools.fund_server import handle_get_stage_brief

    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")
    root = tmp_path / "journals"
    reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                _writing_turn(db), journals_root=root)

    brief = handle_get_stage_brief(db, seat="pm", run_date="2026-08-26",
                                   snapshot=lambda: {"cash": 0, "positions": {},
                                                     "allowed_actions": {}},
                                   journals_root=root)["brief"]
    assert "trim next time" in brief["journal"]
    assert "+6.14%" in brief["journal"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_reflect_job.py -k "journal or morning or projection" -v`
Expected: FAIL — `TypeError: reflect_and_log() got an unexpected keyword argument 'journals_root'` and `AttributeError: ... no attribute 'journals_root_from'`.

- [ ] **Step 3: Implement the wiring**

In `scripts/reflect_day.py`:

Import beside `reflection_frame`:

```python
from orchestrator.reflect import journal_reflections, reflection_frame  # noqa: E402
```

Add after `_window`:

```python
def journals_root_from(environ: dict) -> Path:
    """Same resolution as scripts/run_day.py's composition root: FUND_JOURNALS
    or the repo-local default. mkdir so a fresh host's first night works."""
    root = Path(environ.get("FUND_JOURNALS") or (ROOT / "journals"))
    root.mkdir(parents=True, exist_ok=True)
    return root
```

Change `reflect_and_log`'s signature to:

```python
def reflect_and_log(conn, slack, clock, run_turn, journals_root=None) -> dict:
```

and add to its docstring:

```
    `journals_root` is where written reflections are projected (#57's journal
    half, improvement.md §8 (b)) — an unwindowed, marker-idempotent sweep in
    the `finally`, after the rollup append and before the drain, so whatever
    the night managed to write is journaled even when a later frame raised.
    Unbound (None) is a named log line, not a crash: tests and older callers,
    never production, which threads journals_root_from(environ) from main().
    Guarded like the drain: a projection failure (a read-only disk) loses
    neither the rollup alert nor the drain nor the night's exit code — and
    loses no reflection either, because the unwindowed sweep catches up on
    any later fire.
```

In the `finally` block, **after** the `wrote_nothing` rollup append and **before** the `drain(...)` line:

```python
        if journals_root is None:
            log("journals_root unbound — reflections not journaled")
        else:
            try:
                counts_j = journal_reflections(conn, journals_root,
                                               run_date=run_date)
                log(f"journaled {counts_j['journaled']}"
                    f" · already {counts_j['already']}"
                    f" · blank {counts_j['blank']}")
            except Exception as exc:
                log(f"JOURNAL PROJECTION FAILED ({type(exc).__name__}:"
                    f" {exc}) — reflections stand in the DB; the unwindowed"
                    " sweep catches up on the next fire")
```

In `main()`, change the call to:

```python
    reflect_and_log(conn, slack, clock, run_turn,
                    journals_root=journals_root_from(environ))
```

- [ ] **Step 4: Run the job tests, then the full suite**

Run: `.venv/bin/python3 -m pytest tests/test_reflect_job.py -v && make test`
Expected: PASS. The existing `reflect_and_log` tests pass unchanged — the new parameter defaults to `None`, whose only effect is one log line, and no existing test asserts exact stdout.

- [ ] **Step 5: Commit**

```bash
git add scripts/reflect_day.py tests/test_reflect_job.py
git commit -m "feat(scripts): reflect_day projects the night's reflections into the PM journal"
```

---

### Task 3: Docs close-out and the PR

**Files:**
- Modify: `agents/tools/fund_server.py:481-482`, `specs/design.md:104`, `specs/contracts.md:349`, `specs/acceptance.md` item (b)

- [ ] **Step 1: Retire the two stale deferral notes this change falsifies**

`agents/tools/fund_server.py:481-482` (`handle_submit_reflection`'s docstring) — replace the sentence deferring "a journal or Slack-thread projection of a reflection … to issue #57" with:

```
    The journal projection now rides the nightly job's sweep
    (orchestrator/reflect.py:journal_reflections, improvement.md §8 (b));
    the Slack-thread projection remains deferred with #57.
```

`specs/contracts.md:349` — append to the "scoped to the `resolutions.reflection` column only" sentence: ` (the journal projection landed later, from the same night's job — `specs/improvement.md` §8 (b); the tool itself still writes the column only)`.

- [ ] **Step 2: `specs/design.md` Nightly 1 row**

Replace, in the §3 cadence table's Nightly 1 row, the fragment:

```markdown
the `reflect` seat writes one reflection per decision → `resolutions.reflection` column only. Projection into journals is #57 (`specs/improvement.md` §8 (b)); the original Slack threads stay deferred
```

with:

```markdown
the `reflect` seat writes one reflection per decision → `resolutions.reflection`, and the same job's sweep projects it into the PM's journal via `state/journal.py` (#57's journal half, `specs/improvement.md` §8 (b)); the original Slack threads stay deferred
```

- [ ] **Step 3: Tick acceptance item (b)**

`specs/acceptance.md` Phase 2b: `- [ ] Reflections reach the brief (#57): …` → `- [x]`, appending ` — journal sink only; the Slack-thread sink stays deferred (design.md §3).`

- [ ] **Step 4: Full suite, then commit**

Run: `make test`
Expected: PASS.

```bash
git add agents/tools/fund_server.py specs/design.md specs/contracts.md specs/acceptance.md
git commit -m "docs: the nightly sweep projects reflections into the PM journal; acceptance (b) ticked"
```

- [ ] **Step 5: Open the PR**

Title: `feat: Phase 2b (b) — reflections reach the PM journal (#205)`. Body:

1. The §8 (b) sentence and acceptance item (b).
2. **What this deliberately does not do:** the Slack-thread sink — #57's other half. `design.md` §3 defers it with no owner; whether it survives at all (a per-decision thread post is a VISION-era question) is Benjamin's decision. #57 stays open until he rules; a comment on #57 records the split.
3. **Design notes a reviewer should check:** the four review-driven choices from the plan header (unwindowed sweep; synthetic line-anchored marker; prose defang — the journal's first free text meets two raw-byte consumers; ` · reflections` header suffix vs `_append_entry_once`), each with its demonstrated failure. Name the accepted residual (a deliberately constructed marker line in prose) and the defang's one visible effect (a `##`-run in prose renders as `#`; the DB keeps the original).
4. **No expected-value changes to existing tests.** No DDL, no tool, no charter, no config, no new alert code.
5. Deploy note: code-only — droplet pull suffices; **no unit change** (run #220's `cmp` drift check anyway, expected SAME on all seven). First post-deploy 16:35 fire backfills every historical written reflection into `journals/pm.md` in one section — bounded by the fund's whole history, and the next morning's PM brief will show it.
6. The issue-number rule: `#205` and `#57` referenced with no closing keyword anywhere in the body.

Then comment on #57: "The journal half landed with Phase 2b (b) (PR #<n>): written reflections are swept into `journals/pm.md` nightly — unwindowed, idempotent by a code-built marker. The Slack-thread half remains deferred per design.md §3 — whether it survives at all is Benjamin's call; this issue stays open for that decision."

---

## Self-review

**Spec coverage:** acceptance (b)'s every clause — frame and prose (Task 1 test 1, Task 2 test 1, both on the stored text verbatim), the PM (`DECIDING_SEAT`, asserted by file path), via `append_entry` only (sole write; byte-identical re-run pins append-only), "next morning's `journal` section" on the rendered brief through the real handler (Task 2's last test). §0.4 append-only holds; invariant 4 holds (guarded finally, drain pinned on a real queued alert); invariant 6 untouched.

**Review round 1 findings, each addressed:** blocker (`str(did)` assertion) — gone, replaced by a marker-count assertion; major 1 (`_append_entry_once` collision) — header suffix, pinned by Task 1 test 1's `## {NIGHT}\n not in text`; major 2 (prose suppression) — synthetic marker, pinned by Task 1 test 3 replaying the demonstrated attack; major 3 (window loss + false docstring claims) — window removed, pinned by Task 1 test 5; major 4 (finally placement untested) — Task 2 test 2 uses the frame-error abort that actually reaches the finally; major 5 (drain assertion theater) — Task 2 test 3 asserts a queued rollup reaches `posted_at NOT NULL` and zero rows stay unposted; minors — blank guard (Task 1 test 6), import placement (one module-level block, stated once), dead JOIN (now live: the marker is built from `d.run_date`, `d.ticker`), `fund_server.py:481` + `contracts.md:349` stale notes (Task 3 step 1), `JOURNAL_ENTRIES` dilution (aggregation, Task 1 test 4).

**Placeholder scan:** none. **Type consistency:** `journal_reflections(conn, journals_root, *, run_date) -> dict` identical at definition, monkeypatch (`boom(conn, root, *, run_date)`), and call site; `journals_root_from(environ) -> Path` used in `main()` and its test.

**Round-2 findings, addressed:** the prose-forge/fragmentation major → the defang (design note 3, Task 1's forge test replaying both demonstrated attacks); the `-k` filter minor → `prose` added; the "no seat ever sees" overclaim → docstring now scoped to seats whose text reaches the journal. Declined as YAGNI: pre-scanning the file into a marker set (an optimization the reviewer itself called not required); the cosmetic ever-growing `already`/`blank` log counts (log noise, no loss, and the counts are the honest state).
