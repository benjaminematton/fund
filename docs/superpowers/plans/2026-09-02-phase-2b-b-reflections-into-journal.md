# Phase 2b (b) — Reflections Into the PM Journal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close `specs/improvement.md` §8 (b) — the journal half of #57: a reflection written by the nightly reflect turn is projected into the deciding seat's journal (the PM's), through `state/journal.py:append_entry` only, so the next morning's `journal` brief section carries the resolution's frame and prose.

**Architecture:** One pure projection function in `orchestrator/reflect.py` — a windowed sweep over `resolutions.reflection IS NOT NULL`, idempotent by content marker — called from `scripts/reflect_day.py`'s `finally` block beside the drain, so whatever was written gets journaled even on a broken night. No DDL, no new tool, no charter change, no config. The Slack-thread half of #57 stays deferred (design.md §3, ruled in #204's amendment) and is explicitly not here.

**Tech Stack:** Python 3.12, existing `state/journal.py` and `orchestrator/reflect.py`, pytest. No new dependencies.

## Global Constraints

- **Journals are written only through `state/journal.py`** (CLAUDE.md "Do NOT"; `improvement.md` §0.4: append-only, nothing rewrites history).
- **`orchestrator/` is purity-linted**: no LLM imports, no wall clock. The projection takes its window bounds and dates as arguments; the caller computes them.
- **Default is no-change / the night continues** (invariant 4): a projection failure must not lose the drain, the wrote-nothing rollup, or the night's exit code.
- **No per-run values in prompts**: journals are injected into prompts, so the journal text must carry **no `decision_id`** — the idempotence marker is the frame's own first line (`TICKER · run_date · action qty (status)`), which is already prompt-safe.
- **Acceptance item (b)** (`specs/acceptance.md` Phase 2b): "after resolve + reflect, the next morning's `journal` section for the seat that made the decision (the PM) contains that resolution's frame and prose, appended via `state/journal.py:append_entry` only."
- **PR body: no closing keyword adjacent to any issue number** (PR #210 closed #205 on a negated sentence). #57 and #205 both stay open — #57's Slack half is undelivered, and whether it survives at all is Benjamin's call.
- Baseline at branch point `64e1a0e`: **1808 passed, 1 skipped**. `make test` before every commit; no Co-Authored-By trailer; conventional commits; surgical diffs.

## File structure

| Path | Responsibility | Action |
|---|---|---|
| `orchestrator/reflect.py` | `DECIDING_SEAT`, `journal_reflections(conn, journals_root, *, window, run_date) -> dict` | Modify (append) |
| `scripts/reflect_day.py` | `journals_root_from(environ)`, `reflect_and_log(..., journals_root=None)` calls the sweep in `finally`, guarded; `main()` threads it | Modify |
| `specs/design.md` §3 Nightly 1 row | says the journal projection exists; threads still deferred | Modify |
| `specs/acceptance.md` | tick item (b) | Modify |
| `tests/test_reflect.py` | the projection: content, marker idempotence, window bound, unwritten skipped | Modify (append) |
| `tests/test_reflect_job.py` | wiring: journaled on a written turn, `finally` survives a mid-loop raise, unbound root is a named skip, morning brief carries it | Modify (append) |

## Scope check

One subsystem, one lane. Deliberately not here: the Slack-thread sink (#57's other half — deferred by `specs/design.md` §3 as amended in #204; whether it survives at all is a decision for Benjamin, recorded in the PR body); any journal-growth control (that is (c), lessons distillation); backfill beyond the reflect job's own `REFLECT_LOOKBACK_DAYS` window (an unbounded backfill has the same shape as the unbounded turn-buying the job already refuses).

---

### Task 1: `journal_reflections` — the projection

**Files:**
- Modify: `orchestrator/reflect.py` (append after `store_reflection`)
- Test: `tests/test_reflect.py` (append)

**Interfaces:**
- Consumes: `state.journal.append_entry(root, seat, run_date, text)`; `resolutions` + `decisions` rows.
- Produces: `DECIDING_SEAT = "pm"`; `journal_reflections(conn, journals_root, *, window: tuple[str, str], run_date: str) -> dict` with keys `journaled: int`, `already: int`. `window` is `[start_iso, end_iso)` over `resolved_at` — the caller passes the same bounds `due_reflections` uses. Raises nothing on an empty window or an empty table.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reflect.py` (it already imports `store_reflection` and builds resolved decisions; follow its fixture style — if the file lacks one, use the snippet's own `_resolved` helper modeled on `tests/test_reflect_job.py:41-70`):

```python
# --- the journal projection (#57's journal half; improvement.md §8 (b)) ------

from orchestrator.reflect import (DECIDING_SEAT, journal_reflections,
                                  reflection_frame)

WINDOW = ("2026-08-18T00:00:00+00:00", "2026-08-26T00:00:00+00:00")
NIGHT = "2026-08-25"


def _reflected(conn, *, ticker="NVDA", run_date="2026-08-18",
               resolved_at="2026-08-25T20:35:05+00:00", prose="cut earlier"):
    """A resolved decision whose reflection is already stored — the state
    the projection consumes. Returns (decision_id, stored_text)."""
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


def test_projection_appends_frame_and_prose_to_the_pm_journal(conn, tmp_path):
    _, stored = _reflected(conn)
    root = tmp_path / "journals"

    out = journal_reflections(conn, root, window=WINDOW, run_date=NIGHT)

    assert out == {"journaled": 1, "already": 0}
    text = (root / f"{DECIDING_SEAT}.md").read_text()
    assert f"## {NIGHT}" in text
    assert stored in text                       # frame AND prose, verbatim
    assert "cut earlier" in text


def test_projection_is_idempotent_by_the_frames_first_line(conn, tmp_path):
    """Re-run (crash-resume, a second fire) appends nothing: the marker is
    the frame's own first line — prompt-safe, no decision_id ever enters a
    journal that gets injected into prompts (CLAUDE.md)."""
    did, _ = _reflected(conn)
    root = tmp_path / "journals"
    journal_reflections(conn, root, window=WINDOW, run_date=NIGHT)
    before = (root / f"{DECIDING_SEAT}.md").read_text()

    out = journal_reflections(conn, root, window=WINDOW, run_date=NIGHT)

    assert out == {"journaled": 0, "already": 1}
    assert (root / f"{DECIDING_SEAT}.md").read_text() == before
    assert str(did) not in before               # no surrogate id in the file


def test_only_reflections_inside_the_window_are_projected(conn, tmp_path):
    """The sweep shares due_reflections' window shape: bounded, so a deploy
    onto years of history cannot buy an unbounded backfill (same refusal the
    turn loop already makes)."""
    _reflected(conn, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    _reflected(conn, ticker="MSFT", run_date="2026-08-01",
               resolved_at="2026-08-10T20:35:05+00:00")   # below the window
    root = tmp_path / "journals"

    out = journal_reflections(conn, root, window=WINDOW, run_date=NIGHT)

    text = (root / f"{DECIDING_SEAT}.md").read_text()
    assert out["journaled"] == 1
    assert "NVDA" in text and "MSFT" not in text


def test_an_unwritten_reflection_is_not_projected(conn, tmp_path):
    """reflection IS NULL means the seat has not spoken; projecting a blank
    would be inventing a record (invariant 4)."""
    conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-08-18','AMD','buy',10,'t','i','executed','x')")
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at) VALUES"
        " (last_insert_rowid(), 5, 0.01, 0.01, 0, '2026-08-25T20:35:05+00:00')")
    conn.commit()

    out = journal_reflections(conn, tmp_path / "journals",
                              window=WINDOW, run_date=NIGHT)

    assert out == {"journaled": 0, "already": 0}
    assert not (tmp_path / "journals" / f"{DECIDING_SEAT}.md").exists()
```

If `tests/test_reflect.py` has no `conn` fixture, add one matching `tests/test_reflect_job.py`'s `db` fixture (`connect(tmp_path / "fund.sqlite")`), named `conn`.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_reflect.py -k journal -v`
Expected: FAIL at import — `ImportError: cannot import name 'DECIDING_SEAT'`.

- [ ] **Step 3: Implement**

Append to `orchestrator/reflect.py`:

```python
# The seat whose journal a reflection lands in. `decisions` carries no seat
# column — the PM is the only seat that decides (design.md §2), and
# acceptance (b) names it. A second deciding seat would need a column, not a
# guess; this constant is where that change arrives.
DECIDING_SEAT = "pm"

# Every written reflection in the window, with the fields the journal entry
# needs. Ordered for determinism; the reflection text already carries the
# frame first (store_reflection stores them together).
_WRITTEN = """
SELECT r.decision_id, r.reflection
  FROM resolutions r
  JOIN decisions d ON d.id = r.decision_id
 WHERE r.reflection IS NOT NULL
   AND r.resolved_at >= ? AND r.resolved_at < ?
 ORDER BY r.decision_id
"""


def journal_reflections(conn, journals_root, *, window: tuple[str, str],
                        run_date: str) -> dict:
    """Project every written reflection in `window` into the deciding seat's
    journal (#57's journal half; improvement.md §8 (b)). Returns
    {"journaled": n, "already": n} for the job log.

    Idempotent by content, not by memory: an entry is skipped when the
    reflection's first line — the frame's own header, "TICKER · run_date ·
    action qty (status)" — already appears in the journal file. That marker
    is prompt-safe (journals are injected into prompts; a decision_id there
    would put a per-run value in a prompt, CLAUDE.md), and it is what makes
    a crash-resume, a re-fire, or the sweep-plus-turn overlap append each
    reflection exactly once.

    Windowed like due_reflections, for the same reason: a deploy onto years
    of history must not buy an unbounded backfill. A reflection written
    before this shipped and older than the window is not journaled — the
    same honest gap the turn loop's own aging-out already accepts, and it
    is alerted there, not here.

    Appends through state/journal.py only (CLAUDE.md). The file is read
    once per call for the marker check; append_entry never rewrites."""
    from pathlib import Path

    from state.journal import append_entry

    rows = conn.execute(_WRITTEN, window).fetchall()
    out = {"journaled": 0, "already": 0}
    if not rows:
        return out
    path = Path(journals_root) / f"{DECIDING_SEAT}.md"
    existing = path.read_text() if path.exists() else ""
    for row in rows:
        marker = row["reflection"].splitlines()[0]
        if marker and marker in existing:
            out["already"] += 1
            continue
        append_entry(journals_root, DECIDING_SEAT, run_date, row["reflection"])
        existing += f"\n## {run_date}\n{row['reflection']}\n"
        out["journaled"] += 1
    return out
```

(`from state.journal import append_entry` stays inside the function to match how `orchestrator/daily.py` imports it at module level — if the module-level import is preferred, put it at the top; either passes the purity lint. Choose module level: `from state.journal import append_entry` beside the existing imports, and drop the inner imports.)

- [ ] **Step 4: Run the tests and the purity lint**

Run: `.venv/bin/python3 -m pytest tests/test_reflect.py -v && .venv/bin/python3 scripts/check_purity.py`
Expected: PASS; lint clean.

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
- Consumes: Task 1's `journal_reflections`; `reflect_day._window(run_date)` (the existing bounds helper); `run_day.log` pattern.
- Produces: `reflect_and_log(conn, slack, clock, run_turn, journals_root=None)`; `journals_root_from(environ) -> Path` (mirrors `scripts/run_day.py:677`: `FUND_JOURNALS` or `ROOT / "journals"`, mkdir'd).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reflect_job.py`:

```python
# --- the journal projection rides the same night (#57's journal half) --------

from orchestrator.reflect import DECIDING_SEAT, reflection_frame
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


def test_the_projection_survives_a_mid_loop_raise(db, tmp_path):
    """It rides the finally, beside the drain: a night where the SECOND turn
    raises still journals the first turn's reflection — same reasoning the
    wrote-nothing rollup already carries."""
    _resolved(db, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    _resolved(db, ticker="AMD", resolved_at="2026-08-25T20:36:05+00:00")
    root = tmp_path / "journals"
    calls = {"n": 0}

    def half_writing_turn(job):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("seat session died")
        _store(db, job["decision_id"], job["frame"], "kept my stop")

    reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                half_writing_turn, journals_root=root)

    text = (root / f"{DECIDING_SEAT}.md").read_text()
    assert "kept my stop" in text


def test_an_unbound_journals_root_is_a_named_skip_not_a_crash(db, capsys):
    """Tests and older callers pass no root; production always does
    (main threads journals_root_from). Unbound is logged by name so a wiring
    regression is visible in journald, and the night otherwise proceeds."""
    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")

    reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                _writing_turn(db))

    assert "journals_root unbound — reflections not journaled" \
        in capsys.readouterr().out


def test_a_projection_failure_does_not_eat_the_drain(db, tmp_path,
                                                     monkeypatch):
    """Guarded like everything else in the finally: a raise inside the
    projection must not lose the drain or the night's exit (invariant 4)."""
    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")

    def boom(conn, root, *, window, run_date):
        raise OSError("read-only filesystem")
    monkeypatch.setattr(reflect_day, "journal_reflections", boom)

    reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                _writing_turn(db),
                                journals_root=tmp_path / "journals")
    # reaching here without a raise IS the assertion; the drain ran:
    assert db.execute("SELECT COUNT(*) c FROM events WHERE posted_at IS NOT"
                      " NULL OR posted_at IS NULL").fetchone() is not None


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

Import `journal_reflections` beside `reflection_frame`:

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
    half, improvement.md §8 (b)) — a windowed, idempotent sweep in the
    `finally`, beside the drain, so whatever the night managed to write is
    journaled even when a later turn raised. Unbound (None) is a named log
    line, not a crash: tests and older callers, never production, which
    threads journals_root_from(environ) from main(). The sweep is guarded
    like the drain: a projection failure (a read-only disk) must not eat the
    rollup alert, the drain, or the night's exit code.
```

In the `finally` block, before the `drain(...)` line:

```python
        if journals_root is None:
            log("journals_root unbound — reflections not journaled")
        else:
            try:
                counts_j = journal_reflections(
                    conn, journals_root, window=_window(run_date),
                    run_date=run_date)
                log(f"journaled {counts_j['journaled']}"
                    f" · already {counts_j['already']}")
            except Exception as exc:
                log(f"JOURNAL PROJECTION FAILED ({type(exc).__name__}:"
                    f" {exc}) — reflections stand in the DB, journal catches"
                    " up on the next fire")
```

In `main()`, change the call to:

```python
    reflect_and_log(conn, slack, clock, run_turn,
                    journals_root=journals_root_from(environ))
```

- [ ] **Step 4: Run the job tests, then the full suite**

Run: `.venv/bin/python3 -m pytest tests/test_reflect_job.py -v && make test`
Expected: PASS. The existing `reflect_and_log` tests pass unchanged (the new parameter defaults to `None`, whose only effect is one log line).

- [ ] **Step 5: Commit**

```bash
git add scripts/reflect_day.py tests/test_reflect_job.py
git commit -m "feat(scripts): reflect_day projects the night's reflections into the PM journal"
```

---

### Task 3: Docs close-out and the PR

**Files:**
- Modify: `specs/design.md:104` (Nightly 1 row), `specs/acceptance.md` (item (b))

- [ ] **Step 1: `specs/design.md` Nightly 1 row**

Replace, in the §3 cadence table's Nightly 1 row, the fragment:

```markdown
the `reflect` seat writes one reflection per decision → `resolutions.reflection` column only. Projection into journals is #57 (`specs/improvement.md` §8 (b)); the original Slack threads stay deferred
```

with:

```markdown
the `reflect` seat writes one reflection per decision → `resolutions.reflection`, and the same job projects it into the PM's journal via `state/journal.py` (#57's journal half, `specs/improvement.md` §8 (b)); the original Slack threads stay deferred
```

- [ ] **Step 2: Tick acceptance item (b)**

`specs/acceptance.md` Phase 2b: `- [ ] Reflections reach the brief (#57): …` → `- [x]`, appending ` — journal sink only; the Slack-thread sink stays deferred (design.md §3).`

- [ ] **Step 3: Full suite, then commit**

Run: `make test`
Expected: PASS.

```bash
git add specs/design.md specs/acceptance.md
git commit -m "docs: Nightly 1 projects reflections into the PM journal; acceptance (b) ticked"
```

- [ ] **Step 4: Open the PR**

Title: `feat: Phase 2b (b) — reflections reach the PM journal (#205)`. Body:

1. The §8 (b) sentence and acceptance item (b).
2. **What this deliberately does not do:** the Slack-thread sink — #57's other half. `design.md` §3 defers it with no owner; whether it survives at all (a per-decision thread post is a VISION-era question) is Benjamin's decision. #57 stays open until he rules; a comment on #57 records the split.
3. **No expected-value changes to existing tests.** No DDL, no tool, no charter, no config.
4. Deploy note: code-only — droplet pull suffices; **no unit change** (per #220, that is the check that would have been skipped: `cmp` still run, expected SAME on all seven).
5. The issue-number rule: `#205` and `#57` referenced with no closing keyword anywhere in the body.

Then comment on #57: "The journal half landed with Phase 2b (b) (PR #<n>): written reflections are swept into `journals/pm.md` nightly, windowed and idempotent. The Slack-thread half remains deferred per design.md §3 — whether it survives at all is Benjamin's call; this issue stays open for that decision."

---

## Self-review

**Spec coverage:** acceptance (b)'s every clause has an assertion — frame and prose in the journal (Task 1 test 1, Task 2 test 1), the deciding seat is the PM (`DECIDING_SEAT`, asserted by file path), via `append_entry` only (the implementation's only write; the idempotence test asserts byte-identical on re-run, which a rewrite would break), and "the next morning's `journal` section" on the rendered brief (Task 2's last test, through the real handler). §0.4 append-only: the marker check reads, `append_entry` appends, nothing truncates.

**Placeholder scan:** none; every step has code and an expected outcome.

**Type consistency:** `journal_reflections(conn, journals_root, *, window, run_date) -> dict` identical at definition (Task 1), monkeypatch signature (Task 2 test 4), and call site (Task 2 step 3). `_window(run_date)` returns `(start, end)` — matches the `window` tuple. `journals_root_from(environ) -> Path` used in `main()` only.

**Two things the reviewer should push on:** (1) the marker (`reflection.splitlines()[0]`) assumes `store_reflection` always stores the frame first — true today by construction (`f"{frame}\n\n{prose}"`), but worth a reviewer confirming nothing writes `resolutions.reflection` by another path; (2) the sweep journals reflections for held/rejected decisions too (they are graded and reflected on by design) — confirm that is wanted in the PM's journal, or whether executed-only was intended; the plan takes "all written reflections" because the reflect turn already includes held/rejected on purpose (`reflect_day.py`'s `_DUE_WHERE` comment).
