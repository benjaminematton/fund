# Spec-Registration Seam (#171) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `submit_strategy_spec` and the `expected_spec_id` binding so a spec can be registered, critiqued, and refused a backtest outside its budget — G1 live end to end.

**Architecture:** Two new MCP tool surfaces in `agents/tools/fund_server.py`, both delegating their write path to code that already exists (`state/specs.py:insert_strategy_spec`, `fundbt/hashing.py`). The binding threads one caller-bound kwarg down the channel `expected_decision_id` already uses, so no new plumbing shape is invented.

**Tech Stack:** Python 3.12, pydantic v2 (`extra="forbid"`), sqlite3, `claude-agent-sdk` `@tool`, pytest.

## Global Constraints

Copied verbatim from `CLAUDE.md` and `specs/strategy-contracts.md`; every task's requirements include these.

- **Paper only.** `ALPACA_PAPER_TRADE=true` everywhere. No live-trading code paths, config flags, or TODOs pointing at live trading.
- **`gate/`, `stratgate/`, and `calibration/` import no LLM code.** Enforced by `scripts/check_purity.py` in `make test`.
- **`fundbt/` stays LLM-free — the MCP handler lives in `agents/tools`, never in `fundbt`.** (#171 acceptance)
- **Default is HOLD.** Any error, timeout, malformed tool input, or ambiguity resolves to no action — never a guess.
- **Agents emit structured data only through MCP tools**, never as free text that code parses.
- **Do not invent fields.** Schemas in `specs/` are canonical.
- **Time comes from an injected `Clock`.** Never `datetime.now()` or `time.sleep()` in business logic.
- **Never put per-run values (timestamps, UUIDs, tmp paths) into prompts**; pass them to tools out-of-band.
- **Never update a golden fixture, expected hash, or expected value to make a test pass.** STOP and ask.
- `fundbt/hashing.py` is the **only** permitted hasher (`strategy-contracts.md` §1).
- One write path: the handler calls `state/specs.py:insert_strategy_spec` rather than writing its own INSERT.

---

## ✅ GATE — SAT BY THE CEO, 2026-08-29

All three ruled. Tasks 1 and 2 are dispatchable; Task 3 stays blocked.

| | Ruling | Effect |
|---|---|---|
| **G-1** | **(A)** — sequence, don't proxy | Task 3 leaves this lane. `strategies` + #181's selector replacement become **one** child of #49. #171 stays open for half two |
| **G-2** | **(iii)**, *not* (i) | **No cap is granted.** Handler written and directly tested; §4 row marked `not served`; **no `@tool` registration** |
| **G-3** | Agreed | Ship it, file the driver as a child of #49, close out saying the seam is callable and undriven |

**On G-1, the CEO corrected his own fence** and it is worth recording, because the fence survived the correction on different grounds. The original justification — *"a lifecycle table is new design"* — was wrong on the facts: §2 carries `strategies`' complete DDL and §4 its full state machine, so it is specified-but-unimplemented, exactly what `trial_registry` was before #172. The fence holds on **size**, not novelty: *"a lane that must grow a canonical table mid-flight has discovered its scope was wrong; the answer is to re-scope, not to expand."* This lane already carries registration, an irreversible-write binding, two docstring rewrites, a hash-identity fix, and a canonical §4 edit — and `strategies` drags in the selector replacement, which touches the same `critic_g1.py` queue semantics this lane is already editing.

**On G-2, the reasoning matters more than the option letter.** Granting `analyst` the cap would widen a *trading* seat's served surface with a tool its charter never mentions, and an LLM holding an unexplained tool is its own hazard class. Since G-3 establishes nothing drives the tool anyway, the cap buys zero function at a cost in surface honesty. The lane that staffs a driving seat grants the cap alongside its charter and its schedule, where all three can be reviewed together.

### How (iii) is expressed — verified, not assumed

The CEO's stop condition was: *"if it can't be expressed, escalate — do not fall back to (i) silently."* It can. `tests/test_tool_surface_canon.py` was read in full and there is already a row doing this: **`submit_critique`** — canon, Phase 3, reaching nobody, and with **no `@tool` decorator anywhere in `fund_server.py`**.

The mechanism, and the trap in it:

- `_canon()` accepts a `status` cell of `served` or `not served — <reason>`; anything else raises (`:113-117`).
- `test_each_tool_reaches_exactly_the_seats_the_table_names:191` — a not-served row's expected seat set is **empty**, whatever the `seats` column names: *"A row that is not served names the seats it WILL reach when it ships; until then the only correct answer is nobody."*
- **The trap:** `test_every_registered_tool_has_a_canonical_entry:178` asserts `_declared() == _canon_served()`, and `_declared()` regexes `@tool("name"` out of the **source**. Registering the tool while marking it not-served therefore **fails that test by design** — its docstring exists to catch exactly this: *"dead surface today and one SEAT_CAPS line from live."*

So (iii) is: **handler + direct tests + a `not served` §4 row + no `@tool` + no cap.** Task 2 Step 7 inverts accordingly.

### G-1. `strategies` does not exist, and §3.1 and §3.2 both require it

**The facts.** `state/schema.sql` declares **14 tables and `strategies` is not among them** (verified by enumerating `CREATE TABLE IF NOT EXISTS` lines, not by grepping for the name). `strategy-contracts.md` §2's preamble already says so: *"`strategies`, `sleeves`, and `shadow_fills` have no implementing code yet."*

But the canonical contracts for both halves of this lane read it:

| Where | What it requires |
|---|---|
| §3.1 | "INSERTs spec **+ `strategies` row in state `SPEC`**" |
| §3.2 step 1 | "spec exists and **`strategies.state ∈ {SPEC, BACKTEST}`**" |
| §3.2 step 7 | "run; INSERT trial row; **UPSERT `strategies.state → BACKTEST`**" |

§3.2's implementation notes make it explicit that this is handler-side work, not something `fundbt/` absorbs: *"State check (step 1's `strategies.state` clause) **lives in the MCP handler**."*

So the CEO fence — *"#181/`strategies` stays out; a lifecycle table is new design"* — collides with the canonical spec for **half two of this lane**, not with half one.

**Options.**

- **(A) Ship registration + binding now; hold `run_backtest` exposure until `strategies` lands.** #171's own body already sequences the lane "registration first, exposure second," so this is a sequencing call rather than a scope cut. #171 stays open for half two.
- **(B) Implement steps 1 and 7 against a proxy** — derive state from `trial_registry` presence (no rows = `SPEC`, ≥1 = `BACKTEST`) as `specs_awaiting_critique` derives its queue from the absence of a `strategy_critiques` row.
- **(C) Lift the fence and land `strategies` in this lane.**

**RULED: (A).**

(B) manufactures precisely what the spec warns against. `strategy-contracts.md` §2 already carries one such divergence and says of it: *"the Phase-5 change that creates `strategies` should **replace** the selector rather than add a second one."* Adding a second proxy in the same lane that will have to unpick it trades a visible gap for an invisible one — and a *derived* state has a failure the real column doesn't: a refused run that logs a `budget_exhausted` trial row (§3.2 step 3 logs that rejection) would flip the derived state to `BACKTEST` without any backtest having run.

(C) is a straight reversal of a ruling made twice, on a table whose design is genuinely unstarted.

(A) leaves a coherent, shippable deliverable: a spec can be registered, critiqued under a binding, and the G1 loop closes. That was the stated weekend milestone. What (A) does **not** deliver is budget refusal, which the milestone also named — see G-3.

### G-2. `submit_strategy_spec` has no seat that can call it

`SEAT_CAPS` (`fund_server.py:46-65`) grants caps to exactly six seats: `analyst`, `news`, `pm`, `exec`, `critic`, `reflect`. **None has a spec-registration cap**, and there is no `quant` or researcher seat, though `charters/quant.md` exists and `CLAUDE.md` names it a quality bar.

§3.1 says the tool belongs to "any analyst/researcher seat."

**Options:** (i) grant the cap to `analyst`; (ii) add a `quant` seat; (iii) grant it to no seat this lane and let the tool ship callable only by a future one.

**RULED: (iii) — grant no cap.** (The overseer had recommended (i); overruled, and rightly. See the gate table above for the reasoning and the paragraph above for how (iii) is expressed against the canon test.)

### G-3. Registration ships a tool with no caller — the same shape as #182

Nothing in `orchestrator/` or `scripts/` assigns a turn that calls `submit_strategy_spec`. Per invariant 6 the orchestrator assigns every workflow-critical turn, so a tool no job invokes is inert: the queue stays empty and G1 still never runs on a real spec.

This is not a defect in the plan — it is the honest boundary of the lane. **Name it at the gate so "G1 live end to end" is not claimed on a seam that nothing drives.** Whether a driving turn belongs in this lane, in a follow-up, or in a hand-run script for the first live night is a scheduling decision that belongs with the same person who ruled on the nightly job's shape.

**RULED: agreed.** Ship it, file the driver as a child of #49, and state in #171's close-out that the seam is callable and undriven. The honest post-lane status, in the CEO's words: *"G1 enforced and binding-safe, specs registrable once a driver exists, budget refusal awaiting `strategies`."*

### F-1. `StrategySpec` does not forbid extra fields today — §3.1 requires it

Not a fork; a defect Task 2 must fix, recorded here because it changes what Task 2 touches.

§3.1 mandates `extra="forbid"`. **`state/models.py` sets no `model_config` at all**, so `StrategySpec` runs on pydantic v2's default, `extra="ignore"`. Demonstrated rather than inferred:

```
>>> StrategySpec.model_config.get("extra")   # unset -> pydantic default "ignore"
>>> StrategySpec(**valid_fields, sharpe_target=2.0)   # accepted; field silently dropped
```

The consequence is worse than a lax schema. `StrategySpec`'s own docstring says *"these fields ARE the hash input: `fundbt.hashing.spec_id(model_dump())` is the spec's identity."* A silently-ignored field never enters `model_dump()`, so **two semantically different specs collide on one `spec_id`** and the second is discarded by `INSERT OR IGNORE` with no error — a spec the researcher believes was registered, was not.

Adding `model_config = ConfigDict(extra="forbid")` cannot change any existing id, because ignored fields were never in the hash. It can only turn previously-silent acceptances into errors, which is the point.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `agents/tools/fund_server.py` | MCP tool surface + handlers | Modify: add `handle_submit_strategy_spec` (**handler only — no `@tool`, no cap**), add `expected_spec_id` to `handle_submit_spec_critique` and `build_fund_server` |
| `agents/seats.py` | Per-seat options assembly | Modify: thread `expected_spec_id` (mirrors `expected_decision_id` at `:200`, `:246`) |
| `scripts/run_day.py` | Turn construction | Modify: thread `expected_spec_id` through `make_turn` (`:367`) and `_seat_session` (`:252`) |
| `scripts/critic_g1.py` | Nightly G1 composition root | Modify: bind `expected_spec_id=job["spec_id"]` at `:389`; rewrite the two docstrings that assert no binding exists |
| `tests/conftest.py` | Replay executor | Modify: bind `expected_spec_id` in `make_executor` (`:68-73`) |
| `state/models.py` | Pydantic models | Modify: `StrategySpec` gains `model_config = ConfigDict(extra="forbid")` — see F-1 |
| `state/specs.py` | `strategy_specs` write path | **Unchanged** — `insert_strategy_spec` already exists and is already idempotent |
| `specs/contracts.md` §4 | Served-tool enumeration | Modify: add a `submit_strategy_spec` row with status **`not served`** |
| `tests/test_tool_surface_canon.py` | Pins the served set to §4 | **Unchanged** — a not-served row needs no test edit. If it does, something is wrong |

**Not touched:** `fundbt/` (purity), `state/schema.sql`, `get_spec_brief`'s signature, `submit_spec_critique`'s JSON schema.

---

## Task 1: Bind the G1 verdict to the spec the turn was shown

Closes #182's binding half. Sanctioned by `specs/strategy-contracts.md` §3.4 as amended in PR #194 (`f7bc7ce`).

**Files:**
- Modify: `agents/tools/fund_server.py` (`handle_submit_spec_critique`, `build_fund_server`)
- Modify: `agents/seats.py:200`, `:246`
- Modify: `scripts/run_day.py:252`, `:268`, `:367`, `:391`
- Modify: `scripts/critic_g1.py:283-291`, `:375-381`, `:388-390`
- Modify: `tests/conftest.py:68-73`
- Test: `tests/test_spec_critique_binding.py` (create), `tests/test_run_day.py`, `tests/test_fund_tools.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `handle_submit_spec_critique(conn, *, seat, args, now_iso, charter_version, model_id, expected_spec_id: str | None = None) -> dict`. `build_fund_server(..., expected_spec_id: str | None = None)`. `run_day.make_turn(..., expected_spec_id=None)`, `run_day._seat_session(..., expected_spec_id=None)`, `seats.build_seat_options(..., expected_spec_id=None)`.

**The pattern to copy, exactly:** `expected_decision_id`. Read `fund_server.py:275-323` first — `:279` states the contract ("bound by the CALLER, never by the seat"), `:316` refuses when `None`, `:323` is the use site. Then read `strategy-contracts.md` §3.4's `expected_spec_id` paragraphs, which state the **one deliberate divergence**: reflect *replaces* the id because `submit_reflection`'s schema carries no `decision_id`; here `spec_id` is a REQUIRED schema field, so **refuse on mismatch** instead. A required field the handler ignores is a trap for the next reader.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_spec_critique_binding.py`:

```python
"""The verdict a G1 turn writes is bound to the spec it was SHOWN.

submit_spec_critique is write-once, so a verdict written for the wrong
spec makes that spec permanently unreviewable through any shipped path.
Detection after an irreversible write is not a mitigation, which is why
this is a binding (strategy-contracts.md §3.4) and not a post-hoc check.
"""
import pytest

from agents.tools.fund_server import handle_submit_spec_critique
from tests.synthetic import make_spec, seed_spec_row


def _submit(fund_db, spec_id, expected_spec_id):
    return handle_submit_spec_critique(
        fund_db, seat="critic",
        args={"spec_id": spec_id, "verdict": "clear", "objections": []},
        now_iso="2026-08-29T16:35:00Z",
        charter_version="v1", model_id="claude-opus-5",
        expected_spec_id=expected_spec_id)


def test_an_unbound_turn_refuses_to_write(fund_db):
    """None is the default, so an un-threaded caller must fail closed."""
    sid = seed_spec_row(fund_db)
    r = _submit(fund_db, sid, expected_spec_id=None)
    assert r["ok"] is False
    assert "not bound" in r["error"]
    assert fund_db.execute("SELECT count(*) FROM strategy_critiques").fetchone()[0] == 0


def test_a_verdict_for_a_spec_the_turn_was_not_shown_is_refused(fund_db):
    """The defect this binding exists for: A shown, B written."""
    shown = seed_spec_row(db, hypothesis="A")
    other = seed_spec_row(db, hypothesis="B")
    assert shown != other
    r = _submit(fund_db, other, expected_spec_id=shown)
    assert r["ok"] is False
    assert other in r["error"] and shown in r["error"]
    assert fund_db.execute("SELECT count(*) FROM strategy_critiques").fetchone()[0] == 0


def test_the_matching_verdict_is_written(fund_db):
    sid = seed_spec_row(fund_db)
    assert _submit(fund_db, sid, expected_spec_id=sid)["ok"] is True
    assert fund_db.execute(
        "SELECT spec_id FROM strategy_critiques").fetchone()["spec_id"] == sid
```

- [ ] **Step 2: Run them and read the whole failure list**

Run: `python3 -m pytest tests/test_spec_critique_binding.py -v`

Expected: all three FAIL with `TypeError: handle_submit_spec_critique() got an unexpected keyword argument 'expected_spec_id'`.

**Do not proceed on a partial red.** If `test_the_matching_verdict_is_written` fails for a different reason than the other two, the fixture is wrong, not the code.

- [ ] **Step 3: Add the parameter and the two refusals**

In `agents/tools/fund_server.py`, add to `handle_submit_spec_critique`'s signature: `expected_spec_id: str | None = None`. Immediately after the `_can` check and **before** the `SpecCritique` construction:

```python
    if expected_spec_id is None:
        return {"ok": False,
                "error": "this turn was not bound to a spec —"
                         " refusing to write a G1 verdict blind"}
    if args.get("spec_id") != expected_spec_id:
        return {"ok": False,
                "error": f"this turn was shown spec {expected_spec_id!r} but the"
                         f" verdict names {args.get('spec_id')!r} — refused."
                         " submit_spec_critique is write-once, so a verdict for"
                         " the wrong spec would make it permanently unreviewable"}
```

Extend the docstring to state the caller-bound contract in the same terms `handle_submit_reflection`'s does, and cite **§3.4** — the section, not a line number.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_spec_critique_binding.py -v`
Expected: 3 passed.

- [ ] **Step 5: Thread the kwarg through all four hops**

Each hop mirrors `expected_decision_id` on the adjacent line. Add `expected_spec_id: str | None = None` to the signature and forward it at the call site in:

1. `agents/tools/fund_server.py` `build_fund_server` — forward into the `submit_spec_critique` closure
2. `agents/seats.py` `build_seat_options` — forward into `build_fund_server`
3. `scripts/run_day.py` `_seat_session` — forward into `build_seat_options`
4. `scripts/run_day.py` `make_turn` — forward into `_seat_session`

- [ ] **Step 6: Bind it at the only caller that has a spec id**

`scripts/critic_g1.py:388-390` — `run_turn(job)` already receives `job["spec_id"]`:

```python
    def run_turn(job: dict) -> None:
        turn = run_day.make_turn(seat, cfg, db_path, clock, conn, run_date,
                                 G1_PROMPT, tools=G1_TOOLS,
                                 expected_spec_id=job["spec_id"])
        turn()
```

- [ ] **Step 7: Rewrite the two docstrings that now assert a falsehood**

`critic_g1.py:283-291` (in `critique_and_log`) and `:375-381` (in `_make_run_turn`) currently argue at length that **no binding exists** and that adding one is "a fund_server change, out of region, escalated." After Step 6 those are confident false statements in the file that most needs to be trustworthy.

**Rewrite, do not delete.** The detection-versus-prevention reasoning is *why* the binding exists and is the most valuable prose in the file. Say what the binding is, cite §3.4, and keep the reasoning as its justification. The `has_verdict` re-read stays and stays load-bearing — it now catches a turn that wrote *nothing*, which the binding does not address.

- [ ] **Step 8: Bind it in the replay executor**

`tests/conftest.py:68-73`'s `make_executor` calls `handle_submit_spec_critique` **directly**, so Step 3's `None` refusal turns every replay red. Bind it from the recording's own args. `tests/recordings/critic_g1_clear.jsonl` already carries `spec_id: spec_985a0a8db6b84ce8`, so this is provable rather than assumed.

- [ ] **Step 9: Manufacture a red against the passing code**

A test that goes green on first run pins nothing. Before committing, delete the `expected_spec_id=job["spec_id"]` kwarg from Step 6 and run the **full** suite. Read the entire failure list — if only `tests/test_critic_g1_job.py` reddens and no replay test does, Step 8 bound something that is not on the replay path. Restore, and record the failure list in the task report as the evidence.

- [ ] **Step 10: Full suite, then commit**

Run: `make test`
Expected: green, **output pristine** — no warnings.

```bash
git add agents/tools/fund_server.py agents/seats.py scripts/run_day.py \
        scripts/critic_g1.py tests/conftest.py tests/test_spec_critique_binding.py
git commit -m "feat: bind a G1 verdict to the spec its turn was shown (#182)"
```

---

## Task 2: `submit_strategy_spec`

**Gate passed.** Written against ruling (A) + (iii): **no cap, no `@tool` registration**, no `strategies` row.

**Files:**
- Modify: `agents/tools/fund_server.py` — **the handler only.** No `@tool`, no `SEAT_CAPS` edit (G-2 ruling (iii))
- Modify: `state/models.py` — `StrategySpec` gains `extra="forbid"` (F-1)
- Modify: `specs/contracts.md` §4 — one row, status **`not served`**
- Modify: `slackkit/render.py` — a renderer for the `#research` projection's event kind. `tests/test_slackkit.py` AST-scans every `append_event` kind and requires one, so this is forced, not optional; an unrendered kind dead-letters into a `projection_error` that fails the day's audit
- Test: `tests/test_submit_strategy_spec.py` (create), `tests/synthetic.py` (add `spec_payload`)

**Interfaces:**
- Consumes: `state.specs.insert_strategy_spec(conn, spec: StrategySpec, now_iso: str) -> str`; `state.models.StrategySpec`.
- Produces: `handle_submit_strategy_spec(conn, *, seat, args, now_iso) -> dict` returning `{"ok": True, "spec_id": str, "duplicate": bool}`.

**Why this task is small.** `state/specs.py:insert_strategy_spec` already exists, already computes the content-addressed id through the only permitted hasher, and is **already idempotent by construction** — `INSERT OR IGNORE` against a primary key that *is* the content hash. Its docstring anticipates this exact caller. §3.1's "duplicate `spec_id` → return existing id" therefore needs no new logic, only a truthful return value. Do not write a second INSERT.

- [ ] **Step 1: Write the failing tests**

```python
"""submit_strategy_spec — strategy-contracts.md §3.1.

extra="forbid" and no partial specs: a malformed payload writes nothing.
"""
from agents.tools.fund_server import handle_submit_strategy_spec
from tests.synthetic import spec_payload   # returns a valid §2 field dict


def _submit(fund_db, payload, seat="analyst"):
    return handle_submit_strategy_spec(
        fund_db, seat=seat, args=payload, now_iso="2026-08-29T14:00:00Z")


def test_a_registered_spec_gets_a_content_addressed_id(fund_db):
    r = _submit(fund_db, spec_payload())
    assert r["ok"] is True and r["spec_id"].startswith("spec_")
    assert fund_db.execute("SELECT count(*) FROM strategy_specs").fetchone()[0] == 1


def test_registering_the_same_content_twice_returns_the_same_id(fund_db):
    first = _submit(fund_db, spec_payload())
    second = _submit(fund_db, spec_payload())
    assert second["spec_id"] == first["spec_id"]
    assert second["duplicate"] is True
    assert fund_db.execute("SELECT count(*) FROM strategy_specs").fetchone()[0] == 1


def test_an_unknown_field_is_refused_and_writes_nothing(fund_db):
    r = _submit(fund_db, spec_payload() | {"sharpe_target": 2.0})
    assert r["ok"] is False
    assert fund_db.execute("SELECT count(*) FROM strategy_specs").fetchone()[0] == 0


def test_a_seat_without_the_cap_is_refused(fund_db):
    r = _submit(fund_db, spec_payload(), seat="exec")
    assert r["ok"] is False and "not granted" in r["error"]
    assert fund_db.execute("SELECT count(*) FROM strategy_specs").fetchone()[0] == 0


def test_changing_a_field_produces_a_different_spec_id(fund_db):
    """Spec immutability (acceptance Phase 5): a change is a new spec."""
    a = _submit(fund_db, spec_payload())
    b = _submit(fund_db, spec_payload(hypothesis="a different mechanism"))
    assert a["spec_id"] != b["spec_id"]
```

- [ ] **Step 2: Run and confirm the red**

Run: `python3 -m pytest tests/test_submit_strategy_spec.py -v`
Expected: all FAIL on `ImportError: cannot import name 'handle_submit_strategy_spec'`.

If `spec_payload` does not yet exist in `tests/synthetic.py`, add it there **first** as a sibling of the existing `seed_spec_row` — one builder, so a spec the fixture builds is a spec production builds.

- [ ] **Step 3: Make `StrategySpec` forbid extra fields (F-1)**

In `state/models.py`, add to `StrategySpec`:

```python
    model_config = ConfigDict(extra="forbid")
```

Import `ConfigDict` from `pydantic`. Then run the **full** suite before going further: this tightens a model used beyond this handler, and the suite is the only thing that can tell you whether any existing caller was relying on a field being ignored.

Run: `make test`
Expected: green. **If anything reddens, stop and report it** — a caller passing a field the model ignores is a second instance of the same defect, not something to route around.

- [ ] **Step 4: Grant NO cap — this step is deliberately empty**

Per G-2 (iii), `SEAT_CAPS` is **not** edited. No seat may call this tool. Do not add a cap "for testing" — the tests call the handler directly.

If you find yourself needing a cap to make a test pass, stop and report it: that is a sign the test is exercising the MCP surface rather than the handler, which is not what this task ships.

- [ ] **Step 5: Write the handler**

```python
def handle_submit_strategy_spec(conn: sqlite3.Connection, *, seat: str,
                                args: dict, now_iso: str) -> dict:
    """Validate + INSERT one immutable strategy spec (§3.1).

    The write path is state/specs.py:insert_strategy_spec — one INSERT in
    the tree, so a spec the fixture builds is a spec production builds.
    Idempotence is not implemented here: the id IS the content hash, so a
    re-register collides on the primary key and is ignored. This handler
    only has to report which of the two happened.

    Malformed payload: pydantic extra="forbid" rejects before any write.
    There are no partial specs.
    """
    if not _can(seat, "submit_strategy_spec"):
        return {"ok": False,
                "error": f"submit_strategy_spec is not granted to seat {seat!r}"}
    try:
        spec = StrategySpec(**args, seat=seat)
    except (ValidationError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    before = conn.execute(
        "SELECT count(*) FROM strategy_specs").fetchone()[0]
    sid = insert_strategy_spec(conn, spec, now_iso)
    after = conn.execute(
        "SELECT count(*) FROM strategy_specs").fetchone()[0]
    return {"ok": True, "spec_id": sid, "duplicate": after == before}
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_submit_strategy_spec.py -v`
Expected: 5 passed.

- [ ] **Step 7: Do NOT add an `@tool`. Add the `#research` projection.**

**No `@tool` decorator.** `test_every_registered_tool_has_a_canonical_entry` asserts `_declared() == _canon_served()` and `_declared()` regexes the source, so a decorator here reddens that test **by design**. `submit_critique` is the precedent: a §4 row with no decorator anywhere in `fund_server.py`.

Do add the `#research` projection inside the handler, through the `events` outbox, the same way the neighbouring handlers do — **never** by writing to Slack directly (invariant 6).

- [ ] **Step 8: Add the §4 row with status `not served`**

Add one row to `specs/contracts.md` §4:

| tool | seats | schema | status |
|---|---|---|---|
| `submit_strategy_spec` | `analyst` | `strategy-contracts.md` §3.1 | `not served — no driving seat (#171)` |

The `seats` column names the seat it **will** reach when a driver ships; the not-served status makes its expected served set empty today. Three constraints, all already true of `submit_critique` — verify each rather than assuming:

- `ARGS` in `test_tool_surface_canon.py` must **not** gain an entry (`test_the_wrong_seat_payloads_cover_every_canonical_tool` asserts `set(ARGS) == _canon_served()`).
- The `schema` cell points at `strategy-contracts.md`, so `test_a_row_pointing_its_schema_elsewhere_points_somewhere_real` will check that the file exists **and** contains the string `submit_strategy_spec`. §3.1 does.
- `test_a_wrong_seat_caller_gets_a_tool_error` will call this tool from **every** seat with `{}` and require an error and zero rows written. With no registration the MCP layer answers "Tool not found", which is a tool error.

Edit §4 first, then run the canon tests. Editing a test to make canon fit is backwards.

- [ ] **Step 9: Full suite, then commit**

Run: `make test`
Expected: green, output pristine.

```bash
git add agents/tools/fund_server.py specs/contracts.md tests/synthetic.py \
        tests/test_submit_strategy_spec.py tests/test_tool_surface_canon.py
git commit -m "feat: submit_strategy_spec — the spec-registration seam (#171)"
```

---

## Task 3: `run_backtest` exposure

**BLOCKED — and it leaves this lane.** Do not start. Per G-1(A) it belongs to the `strategies` + selector-replacement child of #49, and #171 stays open for it.

Under recommendation (A) this task does not belong to this lane: §3.2's steps 1 and 7 read and write `strategies.state`, and that table does not exist. Under (B) or (C) this task is written against a different substrate and its tests differ throughout, so writing it now would be writing a plan for a decision nobody has made.

What is already known and should carry into whichever plan takes it:

- `fundbt/run_backtest.py` ships the engine and `evaluate_holdout` already logs its own trial row (`6c8abfe`). The wrapper adds enforcement, not computation.
- `BacktestRequest` / `BacktestResult` are fully specified in §3.2 — do not invent fields; `BacktestResult` "matches the dict returned by `fundbt/run_backtest.py::run_backtest` exactly."
- The **DSR cold-start prior** (`0.5 · max(SR, 0.01)²` under 2 prior family trials) is a **gate threshold**: human commit only.
- Step 3's budget rejection **is logged** — it is the one enforcement failure that writes a trial row.
- `per_period_sharpe` is absent from the registry's enricher; `fundbt/registry.py` flags the exact line where a future enricher would trip it.
- Evaluators stay orchestrator-only — the acceptance criterion is that the seat toolbelt contains **no** evaluator tools.

---

## Self-Review

**Spec coverage.** §3.1 → Task 2. §3.4's `expected_spec_id` → Task 1. §3.2 → Task 3, blocked and stated as blocked. `specs/acceptance.md` Phase 5: *spec immutability* → Task 2 Step 1's fifth test; *spec enforcement*, *trial registry*, *cost floors* → all belong to §3.2 and are therefore **not delivered by this plan** — this is the substantive coverage gap and it is G-1's consequence, named rather than papered over.

**Placeholder scan.** No TBDs. Task 3 carries no steps *because* it is gated, which is stated in its heading rather than left as an empty section for an implementer to fill in.

**Type consistency.** `expected_spec_id: str | None` is spelled identically at all five hops and in the handler. `handle_submit_strategy_spec` returns `{"ok", "spec_id", "duplicate"}` in Task 2 Step 4 and every Task 2 test asserts against exactly those keys. `insert_strategy_spec(conn, spec, now_iso) -> str` is quoted from `state/specs.py:29` rather than recalled.

**One known weakness.** Task 2 Step 5 detects duplication by counting rows either side of the INSERT. That is honest but coarse — it would misreport under a concurrent writer. The fund is single-writer per turn today, so this is correct now and would not survive concurrency; a `SELECT 1 WHERE spec_id = ?` before the insert is the alternative and costs one query. Flagged for the reviewer rather than decided here.
