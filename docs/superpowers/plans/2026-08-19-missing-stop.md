# Closing the Missing-Stop Class — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A protective stop can no longer silently stop existing — the gate refuses to place one that expires before the position does, and an assertion notices any position that ends up naked anyway.

**Architecture:** Two independent changes. (1) `gate/tickets.py:validate_order` denies a stop-carrying order whose `time_in_force` is not `gtc`. (2) A new `orchestrator/protection.py` runs after reconciliation and alerts when a stop the fund *promised* is not live at the broker. The broker owns what protection exists; the fund's record owns what was promised; comparing the two is the comparison nobody performed on 2026-08-17. The second is an assertion, not a stage: no checkpoint, no CAS, no resumability, and it fails closed on every ambiguity.

**Tech Stack:** Python 3.12, pytest, SQLite, alpaca-py 0.44, `claude-agent-sdk`. No new dependencies.

**Design doc:** `docs/superpowers/specs/2026-08-19-missing-stop-design.md`

## Global Constraints

- **Paper only.** `ALPACA_PAPER_TRADE=true`. Never add a live-trading path, flag, or TODO.
- **Default is HOLD.** Any error, timeout, malformed input, or ambiguity resolves to no action — never a guess.
- **`gate/` imports no LLM code.** Pure Python + SQLite; `scripts/check_purity.py` enforces it in `make test`.
- **No broker order is placed, canceled, or modified by this work.** Not by the assertion, not by a test, not by hand. The currently-held naked NVDA 80 is Benjamin's call and is out of scope.
- **`time_in_force` is touched nowhere outside `validate_order`.** No charter, no seat config, no `agents/` file.
- **Never weaken a test, update a golden fixture, or change an expected value to go green.** Stop and ask.
- **Never write `Co-Authored-By` or any AI attribution** in a commit message or PR body.
- Conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`.
- Fail fast with descriptive errors; never swallow exceptions.
- Production diff budget: **~90 lines, hard stop at 110.** If it grows past that, stop and re-read the design doc's "Out of scope".
- **Never edit an existing file in `tests/recordings/`.** A recording is what a seat actually sent; editing one to make a test pass is the forbidden fixture edit. New recordings are new files.
- Run `make test` before every commit. It must be green.
- The worktree venv is pinned to `mcp==1.28.1`; a fresh `make deps` resolves `mcp 2.0.0` and breaks `tests/test_fund_tools.py`. If those 4 tests fail, the environment drifted — do not "fix" the code.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `gate/tickets.py` | Deterministic order validation | Modify — `_as_time_in_force` + one deny branch |
| `orchestrator/broker.py` | `BrokerPort` protocol | Modify — two read-only methods |
| `market/source_alpaca.py` | Live Alpaca I/O | Modify — implement the two reads |
| `orchestrator/protection.py` | Positions-vs-orders assertion | **Create** — one entry point, ~45 lines |
| `orchestrator/daily.py` | Stage runner | Modify — call the assertion + drain |
| `tests/test_tickets.py` | Gate validation tests | Modify |
| `tests/test_hook_acceptance.py` | Replayed turns through the order gate | Modify |
| `tests/recordings/oto_gtc.jsonl` | A stopped exec turn that complies | **Create** (never edit `oto.jsonl`) |
| `tests/recordings/mvf_pm_stop.jsonl` | A PM decision carrying a stop | **Create** |
| `tests/recordings/mvf_exec_stop_gtc.jsonl` | The golden day's stopped exec turn | **Create** |
| `tests/test_protection.py` | Assertion tests | **Create** |
| `tests/test_source_alpaca_helpers.py` | Offline AlpacaSource wiring tests | Modify |
| `tests/test_daily_stages.py` | Stage-machine tests | Modify |
| `tests/fake_alpaca.py` | Offline broker | Modify — positions + OTO child leg |
| `tests/test_sim_day.py` | Full-day simulations | Modify — the incident, reproduced |
| `tests/test_live_smoke.py` | Live paper checks (never CI) | Modify — schema pin + gtc round-trip |

---

### Task 1: Confirm the broker exposes `time_in_force` (BLOCKING, live)

**Why this is first.** `tests/test_tickets.py:72` records that the real `alpaca-mcp-server` place tool **omits `time_in_force`** (captured live 2026-07-12). Nothing establishes that the tool *exposes* the parameter at all. If it does not, the seat cannot satisfy the Task 2 rule, the gate denies every stopped order forever, and no order is placeable — which is precisely the 2026-08-17 outage, where the gate validated a shape the broker had never offered. **Do not start Task 2 until this passes.**

**Files:**
- Modify: `tests/test_live_smoke.py:178-240`

**Interfaces:**
- Consumes: `_place_stock_order_schema()` (already present, `tests/test_live_smoke.py:182`)
- Produces: nothing code-level; a go/no-go for Tasks 2 and 7

- [ ] **Step 1: Extend the schema pin to cover `time_in_force`**

In `tests/test_live_smoke.py`, inside `test_schema_pin_place_stock_order_takes_a_flat_stop_leg`, after the existing block that pins `client_order_id`, `symbol`, `side`, `qty`, `order_class`, add:

```python
    # The 2026-08-19 rule (gate/tickets.py): a stop-carrying order must be
    # gtc, because a DAY stop leg expires at the close of the session it was
    # placed in. That rule is only satisfiable if the tool actually exposes
    # time_in_force — it omits the field from its OUTPUT unless asked, and a
    # gate rule the seat cannot satisfy is an unplaceable order, not a guard.
    assert "time_in_force" in props, (
        "place_stock_order does not expose time_in_force — validate_order's "
        "gtc rule would deny every stopped order with no way for the seat to "
        f"comply. Present: {sorted(props)}")
    tif_types = props["time_in_force"].get("anyOf") or [props["time_in_force"]]
    assert any(t.get("type") == "string" for t in tif_types), (
        f"time_in_force is not a string: {props['time_in_force']}")
```

- [ ] **Step 2: Run it against the real server**

Run: `make schema-pin`
Expected: PASS. It only does `initialize` + `tools/list` — no order is placed, and the market does not need to be open.

**If it FAILS** because `time_in_force` is absent: stop. Do not implement Task 2. Report to Benjamin — the design's change 1 is not deliverable as written and needs a different mechanism.

The second live pin agreed in review — that a resting OTO stop leg is actually visible to `open_orders()` — lives in **Task 7**, not here. This task only does `initialize` + `tools/list`; a resting leg needs an order to exist, and Task 7 already places and cleans up exactly one.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live_smoke.py
git commit -m "test: pin time_in_force as a real place_stock_order parameter"
```

---

### Task 2: The gate rule — `gtc` required on stop-carrying orders

**Files:**
- Modify: `gate/tickets.py:86-101` (add helper), `gate/tickets.py:166-175` (add deny branch)
- Test: `tests/test_tickets.py:26-30` (helper base), plus new tests at the end of the stop-leg section

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `_as_time_in_force(value) -> str | None` in `gate/tickets.py` (module-private; nothing outside the module calls it)

- [ ] **Step 1: Flip the shared test helper's default to `gtc`**

`tests/test_tickets.py`'s `order()` helper currently hardcodes `"time_in_force": "day"` for every order it builds, including the happy-path stop-carrying ones. Change that base value to `"gtc"`:

```python
def order(**over):
    base = {"client_order_id": TID, "symbol": "NVDA", "side": "buy",
            "qty": 67, "type": "market", "time_in_force": "gtc"}
    base.update(over)
    return base
```

This is input construction, not an expected value — no golden fixture, hash, or assertion changes. Both branches of the new rule get explicit coverage in Step 2, including a test that a stopless ticket still accepts `day`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_tickets.py`, after `test_bool_is_rejected_as_a_type_not_by_numeric_coincidence`:

```python
# ---- time_in_force: the 2026-08-19 missing-stop class ----

def test_deny_day_time_in_force_when_the_ticket_has_a_stop(fund_db):
    """The 2026-08-19 incident, in one assertion. The 08-17 NVDA entry placed
    as an OTO whose stop leg inherited the parent's time_in_force: DAY; the
    leg expired at 16:00 ET the same day and the position sat unprotected for
    two sessions while the DB asserted a live stop at 215. A stop must outlive
    the session that created it."""
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(fund_db, order(
        order_class="oto", stop_loss_stop_price="168.0",
        time_in_force="day"), NOW)
    assert not ok, "a DAY stop leg was accepted"
    assert "gtc" in reason


def test_deny_missing_time_in_force_when_the_ticket_has_a_stop(fund_db):
    """The real tool OMITS time_in_force unless the seat passes it
    (tests/fixtures/alpaca/place_stock_order.json), and the broker's default
    is DAY. Absent must therefore deny, not fall through."""
    _seed(fund_db, stop_price=168.0)
    tool_input = order(order_class="oto", stop_loss_stop_price="168.0")
    del tool_input["time_in_force"]
    ok, reason = validate_order(fund_db, tool_input, NOW)
    assert not ok, "a missing time_in_force was accepted"
    assert "gtc" in reason


def test_deny_unreadable_time_in_force_when_the_ticket_has_a_stop(fund_db):
    """Deny-by-default on anything that is not a string: a time_in_force the
    gate cannot read is a stop lifetime the gate cannot verify (invariant 4)."""
    _seed(fund_db, stop_price=168.0)
    for value in (None, 0, 1, True, ["gtc"], {"tif": "gtc"}):
        ok, reason = validate_order(fund_db, order(
            order_class="oto", stop_loss_stop_price="168.0",
            time_in_force=value), NOW)
        assert not ok, f"{value!r} was accepted as a time_in_force"
        assert "gtc" in reason


def test_time_in_force_is_read_case_insensitively(fund_db):
    """Alpaca's enum is lowercase but a seat may well send 'GTC'. Denying that
    would be a false deny on an order that is exactly right."""
    _seed(fund_db, stop_price=168.0)
    for value in ("GTC", "Gtc", " gtc "):
        ok, reason = validate_order(fund_db, order(
            order_class="oto", stop_loss_stop_price="168.0",
            time_in_force=value), NOW)
        assert ok, f"{value!r} was denied: {reason}"


def test_a_stopless_ticket_stays_time_in_force_agnostic(fund_db):
    """The plain path is deliberately untouched: a stopless order has no leg
    to outlive the session, so requiring gtc there would be a false deny on a
    legitimate DAY order."""
    _seed(fund_db)  # stop_price NULL
    ok, reason = validate_order(fund_db, order(time_in_force="day"), NOW)
    assert ok, reason


def test_order_class_is_denied_before_time_in_force(fund_db):
    """A bracket order with a DAY tif violates both rules. order_class is the
    more specific defect and must be the reason reported, so
    test_deny_bracket_order_class_when_stop keeps testing what it names."""
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(fund_db, order(
        order_class="bracket", stop_loss_stop_price="168.0",
        time_in_force="day"), NOW)
    assert not ok
    assert "oto" in reason
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tickets.py -v -k "time_in_force or order_class_is_denied"`
Expected: FAIL. The `deny_*` tests fail with the order being accepted (`assert not ok` → `a DAY stop leg was accepted`); `test_a_stopless_ticket_stays_time_in_force_agnostic` and `test_time_in_force_is_read_case_insensitively` already pass.

- [ ] **Step 4: Add the coercion helper**

In `gate/tickets.py`, immediately after `_as_price` (line 101):

```python
def _as_time_in_force(value):
    """Lowercased time-in-force from a string; None otherwise. The Alpaca MCP
    place tool OMITS this field unless the seat passes it (captured
    2026-07-12) and the broker's default is 'day', so a missing key arrives
    here as None and denies. Case is normalized on input and compared exactly.
    Non-strings (including bool, which is not a str) deny: a lifetime the gate
    cannot read is a stop the gate cannot verify."""
    if isinstance(value, str):
        return value.strip().lower()
    return None
```

- [ ] **Step 5: Add the deny branch**

In `gate/tickets.py:validate_order`, in the `else` branch (the ticket carries a `stop_price`), immediately **after** the existing `order_class != "oto"` check and before `return True, "ok"`:

```python
        # A DAY stop leg dies at the close of the session it was placed in.
        # On 2026-08-17 that left NVDA 80 unprotected for two full sessions
        # while decisions.stop_price still asserted a live stop at 215. The
        # stop must outlive the day that created it, so the whole order goes
        # gtc — Alpaca applies one time_in_force to the parent and its legs.
        if _as_time_in_force(tool_input.get("time_in_force")) != "gtc":
            return False, (
                f"time_in_force {tool_input.get('time_in_force')!r} must be"
                " 'gtc' for a stop exit — a 'day' stop leg expires at the"
                " close of the session it is placed in and leaves the"
                " position unprotected overnight")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tickets.py -v`
Expected: PASS, all of them — including the pre-existing stop-leg tests, which now build `gtc` orders via the helper.

- [ ] **Step 7: Turn the recorded `day` stop into the hook-level regression test**

Running the full suite now fails `tests/test_hook_acceptance.py::test_stop_ticket_yields_oto_order`. That is **correct and expected**: it replays `tests/recordings/oto.jsonl`, a real captured exec turn carrying `"time_in_force": "day"` and a stop leg — the exact shape of the 2026-08-17 order — and asserts it reaches the broker and fills. The new rule denies it.

**Do NOT edit `tests/recordings/oto.jsonl`.** Changing a recording to make a test pass is exactly what CLAUDE.md's test invariants forbid. The recording is the historical truth of what the seat sent; it keeps that job and gains a better one.

In `tests/test_hook_acceptance.py`, replace `test_stop_ticket_yields_oto_order` with:

```python
def test_a_recorded_day_stop_never_reaches_the_broker(fund_db, sim_clock):
    """oto.jsonl is the REAL 2026-08-17 shape: an oto with a matching stop leg
    and time_in_force 'day'. It placed, it filled, and the stop leg died at
    the bell — the position was naked for two sessions. The gate now stops it
    at the hook, so the recording that documents the incident is also the
    regression test for it. The recording is never edited: it is what the seat
    actually sent."""
    _seed(fund_db, stop_price=168.0)
    broker = FakeAlpaca({"NVDA": 180.00}, {"NVDA": 180.14}, mode="instant")
    outcomes = _replay(fund_db, sim_clock, broker, "oto.jsonl")
    assert "gtc" in outcomes[-1]["denied"]
    assert broker.place_attempts == []
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 0


def test_stop_ticket_yields_oto_order(fund_db, sim_clock):
    """The healthy stopped path, unchanged except that the stop now outlives
    the session that placed it."""
    _seed(fund_db, stop_price=168.0)
    broker = FakeAlpaca({"NVDA": 180.00}, {"NVDA": 180.14}, mode="instant")
    outcomes = _replay(fund_db, sim_clock, broker, "oto_gtc.jsonl")
    assert json.loads(outcomes[-1]["result"])["data"]["status"] == "filled"
    placed = broker.place_attempts[0]
    assert placed["order_class"] == "oto"
    assert placed["stop_loss_stop_price"] == "168.0"
    assert placed["time_in_force"] == "gtc"
```

- [ ] **Step 8: Add the new recording**

Create `tests/recordings/oto_gtc.jsonl` — a **new file**, not an edit of an existing one. Two lines, identical to `oto.jsonl` except for `time_in_force`:

```
{"seat": "exec", "tool": "mcp__fund__list_open_tickets", "args": {}}
{"seat": "exec", "tool": "mcp__alpaca__place_stock_order", "args": {"client_order_id": "a3f90000-0000-4000-8000-000000000001", "symbol": "NVDA", "side": "buy", "qty": "67", "type": "market", "time_in_force": "gtc", "order_class": "oto", "stop_loss_stop_price": "168.0"}}
```

- [ ] **Step 9: Run the hook acceptance tests**

Run: `.venv/bin/python -m pytest tests/test_hook_acceptance.py -v`
Expected: PASS, including both tests from Step 7.

- [ ] **Step 10: Run the full suite**

Run: `make test`
Expected: 0 failures, and a test count higher than the 810 baseline.

The sim-day recordings are unaffected: `mvf_exec.jsonl` places a **plain** order (no stop leg), and the stopless path stays time-in-force-agnostic by design.

- [ ] **Step 11: Commit**

```bash
git add gate/tickets.py tests/test_tickets.py tests/test_hook_acceptance.py tests/recordings/oto_gtc.jsonl
git commit -m "fix: a stop-carrying order must be gtc, so the stop outlives the day"
```

---

### Task 3: Two read-only broker methods

**Files:**
- Modify: `orchestrator/broker.py:16-17`, `market/source_alpaca.py:70-88`
- Test: `tests/test_source_alpaca_helpers.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `BrokerPort.open_positions() -> list[dict]` — each dict has keys `symbol: str`, `qty: str`, `side: str`
  - `BrokerPort.open_orders() -> list[dict]` — each dict has keys `symbol: str`, `side: str`, `qty: str`, `type: str`, `status: str`
  - Both **raise** on broker failure. Task 4 depends on that: it converts a raise into an alert.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_source_alpaca_helpers.py`:

```python
# ---- open_positions / open_orders: the protection assertion's broker reads ----

def test_installed_alpaca_py_exposes_the_read_apis_we_call():
    """Same posture as the cancel-wiring pin: a wrong alpaca-py method name
    can otherwise only fail LIVE, and this read is what stands between a naked
    position and nobody noticing."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    assert callable(TradingClient.get_all_positions)
    assert callable(TradingClient.get_orders)
    assert "status" in GetOrdersRequest.model_fields


def test_open_positions_unwraps_alpaca_enums():
    """THE trap. alpaca-py returns (str, Enum) members and str(PositionSide.
    LONG) is 'PositionSide.LONG', not 'long'. A plain str() here makes every
    position unclassifiable and every stop unmatchable, so the protection
    check would alert on a fully protected book every day — and a fake built
    from plain strings would never show it. This fake uses the REAL enums."""
    from alpaca.trading.enums import PositionSide

    class Trading:
        def get_all_positions(self):
            return [_Clock(symbol="NVDA", qty="80", side=PositionSide.LONG)]
    src = _bare_source()
    src._trading = Trading()
    assert src.open_positions() == [
        {"symbol": "NVDA", "qty": "80", "side": "long"}]


def test_open_positions_propagates_broker_errors():
    """Unlike get_order_by_client_order_id (which swallows because its caller
    re-polls), this read has no retry behind it. A swallowed error would read
    as "no positions held", which is a silent pass on exactly the condition
    the check exists to catch."""
    class Trading:
        def get_all_positions(self): raise ConnectionError("down")
    src = _bare_source()
    src._trading = Trading()
    with pytest.raises(ConnectionError):
        src.open_positions()


def test_open_orders_requests_open_status_and_flattens_legs():
    """nested=False (the default) is deliberate: an OTO's stop leg must come
    back as its OWN row, because the leg is the protective order the check is
    looking for. Grouped under the parent it would be invisible. Built from
    the REAL enums for the same reason as the positions test above."""
    from alpaca.trading.enums import OrderSide, OrderStatus, OrderType

    seen = {}
    class Trading:
        def get_orders(self, filter=None):
            seen["status"] = str(filter.status)
            seen["nested"] = filter.nested
            seen["limit"] = filter.limit
            return [_Clock(symbol="NVDA", side=OrderSide.SELL, qty="80",
                           order_type=OrderType.STOP, status=OrderStatus.NEW)]
    src = _bare_source()
    src._trading = Trading()
    assert src.open_orders() == [
        {"symbol": "NVDA", "side": "sell", "qty": "80",
         "type": "stop", "status": "new"}]
    assert "open" in seen["status"].lower()
    assert not seen["nested"]
    assert seen["limit"] == 500


def test_open_orders_raises_rather_than_returning_a_truncated_page():
    """A full page means orders were dropped, and a dropped protective order
    reads as 'nothing is protecting this'. The caller turns this raise into an
    'unverified' alert — the honest answer. No silent caps."""
    from alpaca.trading.enums import OrderSide, OrderStatus, OrderType

    class Trading:
        def get_orders(self, filter=None):
            return [_Clock(symbol="NVDA", side=OrderSide.SELL, qty="1",
                           order_type=OrderType.STOP, status=OrderStatus.NEW)
                    for _ in range(500)]
    src = _bare_source()
    src._trading = Trading()
    with pytest.raises(RuntimeError, match="page limit"):
        src.open_orders()


def test_open_orders_propagates_broker_errors():
    class Trading:
        def get_orders(self, filter=None): raise ConnectionError("down")
    src = _bare_source()
    src._trading = Trading()
    with pytest.raises(ConnectionError):
        src.open_orders()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_source_alpaca_helpers.py -v -k "open_positions or open_orders or read_apis"`
Expected: FAIL with `AttributeError: 'AlpacaSource' object has no attribute 'open_positions'`

- [ ] **Step 3: Extend the protocol**

In `orchestrator/broker.py`, add to `BrokerPort` after `cancel_order`:

```python
    def open_positions(self) -> list[dict]: ...
    def open_orders(self) -> list[dict]: ...
```

And extend the class docstring's first paragraph so the read-only rule stays stated in one place. Replace:

```python
    """Broker access for deterministic code (mirrors SlackPort): read the
    state of an order the fund already placed, and cancel one that is still
    working past the fill-poll's cap. Order PLACEMENT stays agent-side behind
    the PreToolUse gate hook (invariant 2) — this port never places, and must
    never grow a method that does.
```

with:

```python
    """Broker access for deterministic code (mirrors SlackPort): read the
    state of an order the fund already placed, cancel one that is still
    working past the fill-poll's cap, and read what the ACCOUNT actually
    holds — positions and live orders — so a position with no protective
    order cannot go unnoticed. Order PLACEMENT stays agent-side behind
    the PreToolUse gate hook (invariant 2) — this port never places, and must
    never grow a method that does.

    `open_positions` and `open_orders` RAISE on failure rather than returning
    empty. They have no retry behind them, and an empty list would read as
    "nothing held" / "nothing protecting it" — a silent pass on the exact
    condition orchestrator/protection.py exists to catch.
```

- [ ] **Step 4: Implement them on `AlpacaSource`**

In `market/source_alpaca.py`, after `cancel_order` (line 88):

```python
    def open_positions(self) -> list[dict]:
        """Every position the account actually holds. Numbers stay STRINGS,
        exactly as they arrive — orchestrator/protection.py coerces them and
        denies on anything it cannot read, which it cannot do if this method
        has already guessed. Deliberately does NOT swallow (see BrokerPort)."""
        return [{"symbol": p.symbol, "qty": _enum_str(p.qty),
                 "side": _enum_str(p.side)}
                for p in self._trading.get_all_positions()]

    def open_orders(self) -> list[dict]:
        """Every order still working at the broker, legs FLATTENED.

        nested=False (the default) is the point: an OTO's stop leg comes back
        as its own top-level row, and that leg IS the protective order the
        check looks for. Grouped under its parent it would be invisible —
        which is how the 2026-08-17 stop stayed dead for two sessions.

        RAISES if the response fills the page. A truncated list would drop
        protective orders off the end and report covered positions as naked;
        the caller turns this raise into an 'unverified' alert, which is the
        honest answer. No silent caps."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        orders = list(self._trading.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN, nested=False,
            limit=_ORDER_PAGE_LIMIT)))
        if len(orders) >= _ORDER_PAGE_LIMIT:
            raise RuntimeError(
                f"open-orders response hit the {_ORDER_PAGE_LIMIT}-row page"
                " limit — cover cannot be computed from a truncated list")
        return [{"symbol": o.symbol, "side": _enum_str(o.side),
                 "qty": _enum_str(o.qty), "type": _enum_str(o.order_type),
                 "status": _enum_str(o.status)}
                for o in orders]
```

And above the class, next to the other module-level helpers:

```python
# alpaca-py returns (str, Enum) members, and str(OrderSide.SELL) is
# 'OrderSide.SELL', NOT 'sell' — so a plain str() here would make every stop
# order and every long position unmatchable, and orchestrator/protection.py
# would alert on a fully protected book every single day. Tests that build
# fakes out of plain strings cannot catch that, which is why
# tests/test_source_alpaca_helpers.py builds them from the real enums.
def _enum_str(v) -> str:
    return str(getattr(v, "value", v))


# One page, requested explicitly. Alpaca's list endpoint paginates, and a
# silently truncated page reads as "nothing is protecting this".
_ORDER_PAGE_LIMIT = 500
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_source_alpaca_helpers.py -v`
Expected: PASS

Note: `_Clock` is a plain attribute bag (`tests/test_source_alpaca_helpers.py:67`), so the fakes above set `order_type=`, matching what the implementation reads. The real `Order` model carries both `type` and `order_type`; `order_type` is the canonical one.

- [ ] **Step 6: Run the full suite and commit**

Run: `make test`
Expected: 0 failures.

```bash
git add orchestrator/broker.py market/source_alpaca.py tests/test_source_alpaca_helpers.py
git commit -m "feat: the broker port can read positions and live orders"
```

---

### Task 4: The assertion

**Files:**
- Create: `orchestrator/protection.py`
- Test: `tests/test_protection.py` (create)

**Interfaces:**
- Consumes: `BrokerPort.open_positions()`, `BrokerPort.open_orders()` from Task 3; `slackkit.outbox.append_event(conn, kind, payload, now_iso)`
- Produces: `assert_positions_protected(conn, *, broker, now_iso) -> int` — returns the number of alerts appended, and **never raises**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_protection.py`:

```python
"""The 2026-08-19 missing-stop assertion. Every test here is a case that must
ALERT rather than pass quietly — a check that can pass while lying is the exact
failure of all three incidents, so 'fails closed' is a test, not a comment."""

import json

import pytest

from orchestrator.protection import assert_positions_protected

NOW = "2026-08-19T20:05:00+00:00"


def _alerts(conn):
    return [json.loads(r["payload"])["text"] for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id").fetchall()]


class Broker:
    """A broker with an exact answer for both reads."""
    def __init__(self, positions=(), orders=()):
        self._positions, self._orders = list(positions), list(orders)
    def open_positions(self): return self._positions
    def open_orders(self): return self._orders


def _long(symbol="NVDA", qty="80"):
    return {"symbol": symbol, "qty": qty, "side": "long"}


def _stop(symbol="NVDA", qty="80", type="stop"):
    return {"symbol": symbol, "side": "sell", "qty": qty, "type": type,
            "status": "new"}


def _promised(conn, *, symbol="NVDA", stop_price=215.0, qty=80,
              tid="a3f90000-0000-4000-8000-000000000001",
              submitted_at="2026-08-17T19:59:00+00:00",
              status="filled", filled_qty=None):
    """A filled buy order and the ticket behind it — the fund's own record of
    what it opened and what protection it promised. `stop_price=None` is the
    charter-sanctioned stopless buy (charters/pm.md:25).

    The ticket lands as 'consumed', the real post-execution state
    (state/transition.py:13 — open -> consumed | expired). There is no
    'filled' ticket state, and a fixture must not invent one."""
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, stop_price, status, created_at) VALUES"
        " ('2026-08-17', ?, 'buy', ?, 't', 'i', ?, 'executed', ?)",
        (symbol, qty, stop_price, submitted_at))
    conn.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " stop_price, expires_at, status, created_at)"
        " VALUES (?, ?, ?, 'buy', ?, ?, ?, 'consumed', ?)",
        (tid, cur.lastrowid, symbol, qty, stop_price, submitted_at,
         submitted_at))
    conn.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " filled_qty, submitted_at) VALUES (?, ?, 'buy', ?, ?, ?, ?)",
        (tid, symbol, qty, status, qty if filled_qty is None else filled_qty,
         submitted_at))
    conn.commit()


def test_a_covered_position_is_silent(fund_db):
    _promised(fund_db)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], [_stop()]), now_iso=NOW)
    assert n == 0
    assert _alerts(fund_db) == []


def test_no_positions_is_silent(fund_db):
    n = assert_positions_protected(fund_db, broker=Broker(), now_iso=NOW)
    assert n == 0
    assert _alerts(fund_db) == []


def test_a_promised_stop_that_is_gone_alerts(fund_db):
    """THE incident. NVDA 80 was ticketed with a stop at 215, the OTO leg
    expired at the bell on 2026-08-17, and for two sessions the database said
    'stop at 215' while the broker held no order at all. Nobody compared them.
    This is that comparison."""
    _promised(fund_db, stop_price=215.0)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW)
    assert n == 1
    assert "NVDA" in _alerts(fund_db)[0]
    assert "80" in _alerts(fund_db)[0]


def test_a_position_that_was_never_promised_a_stop_is_silent(fund_db):
    """charters/pm.md:25 makes a stopless buy sanctioned and normal — the PM
    passes stop_price only for a hard price invalidation. Alerting here would
    red the audit (scripts/audit_day.py:148) every day on a correct day, and
    an alert channel that cries wolf daily protects nothing. Standing exposure
    is state, not a fault; it belongs in the digest (next branch)."""
    _promised(fund_db, stop_price=None)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW)
    assert n == 0
    assert _alerts(fund_db) == []


def test_a_position_the_fund_has_no_record_of_alerts(fund_db):
    """No filled buy order for this symbol: a manual or pre-existing holding.
    Provenance unknown means the promise cannot be read, so it fails closed
    rather than being assumed sanctioned."""
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW)
    assert n == 1


def test_the_most_recent_buy_decides_the_promise(fund_db):
    """A symbol sold and re-bought without a stop is read on its CURRENT
    terms. Inheriting the older promise would alert forever on a position
    deliberately held stopless."""
    _promised(fund_db, stop_price=215.0,
              submitted_at="2026-08-17T19:59:00+00:00")
    _promised(fund_db, stop_price=None,
              tid="b4f90000-0000-4000-8000-000000000002",
              submitted_at="2026-08-19T14:00:00+00:00")
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW)
    assert n == 0


def test_a_partial_fill_that_was_canceled_still_counts_as_opening_it(fund_db):
    """orchestrator/reconcile.py:197-206 records a timed-out partial as
    CANCELED with filled_qty > 0, commenting that filled_qty > 0 is a REAL
    position. Matching only on status='filled' would call that position
    unknown-provenance and print an alert claiming the fund has no record of
    opening it — which is false."""
    _promised(fund_db, stop_price=215.0, status="canceled", filled_qty=30)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long(qty="30")], []), now_iso=NOW)
    assert n == 1
    assert "stop at 215" in _alerts(fund_db)[0]
    assert "no fund record" not in _alerts(fund_db)[0]


def test_a_covered_symbol_and_a_naked_one_alert_exactly_once(fund_db):
    """A mixed book is where cross-symbol matching bugs show up: MSFT's stop
    must not cover NVDA, and NVDA's absence must not implicate MSFT."""
    _promised(fund_db, symbol="NVDA")
    _promised(fund_db, symbol="MSFT",
              tid="c5f90000-0000-4000-8000-000000000003")
    n = assert_positions_protected(
        fund_db,
        broker=Broker([_long("NVDA"), _long("MSFT")], [_stop("MSFT")]),
        now_iso=NOW)
    assert n == 1
    assert "NVDA" in _alerts(fund_db)[0]
    assert "MSFT" not in _alerts(fund_db)[0]


# ---- the re-read: an OTO leg is created 'held' and can lag its parent ----

class SlowLeg:
    """A broker whose protective order only becomes visible on the second
    read — the real shape of an OTO child just after its parent fills."""
    def __init__(self, positions):
        self._positions, self.reads = positions, 0
    def open_positions(self): return self._positions
    def open_orders(self):
        self.reads += 1
        return [_stop()] if self.reads > 1 else []


def test_a_leg_that_appears_on_the_second_read_is_not_an_alert(fund_db):
    """Without this, the fund alerts on every position it correctly protects,
    on every day it actually trades — the alert channel would be dead inside
    a week."""
    _promised(fund_db)
    naps = []
    n = assert_positions_protected(
        fund_db, broker=SlowLeg([_long()]), now_iso=NOW, sleep=naps.append)
    assert n == 0
    assert _alerts(fund_db) == []
    assert naps == [3.0]


def test_a_leg_that_is_still_missing_after_the_re_read_alerts(fund_db):
    _promised(fund_db)
    naps = []
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW, sleep=naps.append)
    assert n == 1
    assert naps == [3.0]


def test_the_re_read_waits_once_per_run_not_once_per_position(fund_db):
    """Three naked positions must not become three sequential waits inside a
    live trading day."""
    for i, sym in enumerate(("NVDA", "MSFT", "AAPL")):
        _promised(fund_db, symbol=sym, tid=f"d{i}f90000-0000-4000-8000-00000000000{i}")
    naps = []
    n = assert_positions_protected(
        fund_db,
        broker=Broker([_long("NVDA"), _long("MSFT"), _long("AAPL")], []),
        now_iso=NOW, sleep=naps.append)
    assert n == 3
    assert naps == [3.0]


def test_a_sell_limit_is_not_protection(fund_db):
    """A take-profit caps the upside, not the loss. Treating it as protection
    would report the position safe while it is entirely exposed downward."""
    _promised(fund_db)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], [_stop(type="limit")]), now_iso=NOW)
    assert n == 1


def test_a_stop_for_fewer_shares_than_held_alerts(fund_db):
    """Partial cover is not cover: 30 of 80 shares stopped leaves 50 naked."""
    _promised(fund_db)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long(qty="80")], [_stop(qty="30")]),
        now_iso=NOW)
    assert n == 1


def test_stops_across_several_orders_sum(fund_db):
    """Two stop orders of 40 do protect 80 — denying that would be a false
    alarm that trains the reader to ignore the channel."""
    _promised(fund_db)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long(qty="80")],
                               [_stop(qty="40"), _stop(qty="40")]),
        now_iso=NOW)
    assert n == 0


def test_a_stop_on_another_symbol_does_not_count(fund_db):
    _promised(fund_db, symbol="NVDA")
    n = assert_positions_protected(
        fund_db, broker=Broker([_long("NVDA")], [_stop("MSFT")]), now_iso=NOW)
    assert n == 1


def test_a_buy_order_does_not_protect_a_long(fund_db):
    """Only the closing side protects. A resting BUY adds risk."""
    _promised(fund_db)
    order = _stop()
    order["side"] = "buy"
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], [order]), now_iso=NOW)
    assert n == 1


def test_stop_limit_and_trailing_stop_both_count(fund_db):
    _promised(fund_db)
    for kind in ("stop_limit", "trailing_stop"):
        before = len(_alerts(fund_db))
        n = assert_positions_protected(
            fund_db, broker=Broker([_long()], [_stop(type=kind)]), now_iso=NOW)
        assert n == 0, f"{kind} was not counted as protection"
        assert len(_alerts(fund_db)) == before


def test_an_unreadable_position_qty_alerts(fund_db):
    """Unreadable is checked BEFORE the promise lookup: a position whose qty
    is gibberish cannot be classified either way."""
    _promised(fund_db, stop_price=None)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long(qty="eighty")], []), now_iso=NOW)
    assert n == 1


def test_a_short_position_alerts(fund_db):
    """The fund is long-only; stops guard new or added longs (state/models.py).
    A short at the broker is a position this check cannot classify, so it
    fails closed rather than deciding what protects it."""
    _promised(fund_db, stop_price=None)
    position = _long()
    position["side"] = "short"
    n = assert_positions_protected(
        fund_db, broker=Broker([position], []), now_iso=NOW)
    assert n == 1


def test_an_unreadable_order_alerts_rather_than_being_skipped(fund_db):
    """An order whose qty will not parse might be the protective one. Skipping
    it would silently downgrade cover; the position is reported unproven. This
    fires even on a never-promised position: 'I could not read the book' is a
    different statement from 'nothing was promised'."""
    _promised(fund_db, stop_price=None)
    bad = _stop(qty="many")
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], [bad]), now_iso=NOW)
    assert n == 1


def test_a_positions_read_that_raises_alerts(fund_db):
    class Down(Broker):
        def open_positions(self): raise ConnectionError("broker down")
    n = assert_positions_protected(fund_db, broker=Down(), now_iso=NOW)
    assert n == 1
    assert "ConnectionError" in _alerts(fund_db)[0]


def test_an_orders_read_that_raises_alerts(fund_db):
    """Positions read fine, orders did not — so cover is UNKNOWN, which must
    never read as covered."""
    class Down(Broker):
        def open_orders(self): raise ConnectionError("broker down")
    n = assert_positions_protected(
        fund_db, broker=Down([_long()]), now_iso=NOW)
    assert n == 1
    assert "ConnectionError" in _alerts(fund_db)[0]


def test_a_missing_broker_alerts(fund_db):
    """A None broker in production is a wiring bug. It must scream, not skip:
    'no broker, so no check, so no alert' is the silent pass this whole module
    exists to make impossible."""
    n = assert_positions_protected(fund_db, broker=None, now_iso=NOW)
    assert n == 1


def test_it_never_raises_no_matter_how_broken_the_broker_is(fund_db):
    """Invariant 4: the assertion runs at the end of a real trading day. It
    reports, it does not take the day down."""
    class Chaos:
        def open_positions(self): raise RuntimeError("boom")
        def open_orders(self): raise RuntimeError("boom")
    assert assert_positions_protected(fund_db, broker=Chaos(), now_iso=NOW) == 1


def test_one_alert_per_unprotected_position(fund_db):
    _promised(fund_db, symbol="NVDA")
    _promised(fund_db, symbol="MSFT",
              tid="c5f90000-0000-4000-8000-000000000003")
    n = assert_positions_protected(
        fund_db, broker=Broker([_long("NVDA"), _long("MSFT")], []), now_iso=NOW)
    assert n == 2
    assert len(_alerts(fund_db)) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_protection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.protection'`

- [ ] **Step 3: Write the implementation**

Create `orchestrator/protection.py`:

```python
"""Assertion: a promised stop still exists at the broker (2026-08-19).

Not a stage — no checkpoint, no CAS, no resumability. It re-checks on a
resumed day rather than being skipped as 'done', and a duplicate alert is the
safe direction.

The BROKER owns what protection exists; the fund's own record owns what
protection was PROMISED. Comparing those two is exactly the comparison nobody
performed on 2026-08-17, when the database said 'stop at 215' for two sessions
while the broker held no order at all. Note the direction: the database is
never allowed to assert that a stop EXISTS — only what was intended.

So a promised stop that is gone is a fault and alerts. A position the PM
deliberately opened without one (charters/pm.md:25 — stop_price is passed only
for a hard price invalidation) is standing exposure, not a fault: alerting on
it would red the audit (scripts/audit_day.py:148) every day on a correct day,
and a channel that cries wolf daily protects nothing. That exposure is
reported in the EOD digest instead (follow-up branch).

Every ambiguity alerts. Broker unreachable, a number that will not parse, a
position with no provenance in our own records — none of them may pass
quietly, because a check that can pass while lying is worse than no check at
all (invariant 4)."""
from __future__ import annotations

import sqlite3
from typing import Callable

from slackkit.outbox import append_event

# One short wait before calling a position naked. Matches reconcile_orders'
# poll_s default. Deliberately NOT max_wait_s (90s): this sits on the critical
# path of a live trading day, just before the digest posts.
_RETRY_S = 3.0

# Order types that actually cap a loss. A sell LIMIT is a take-profit: it caps
# the upside and leaves the downside fully exposed, so it is not protection.
_STOP_TYPES = ("stop", "stop_limit", "trailing_stop")

# The closing side for a position. A short is absent on purpose: this fund is
# long-only (state/models.py — stops guard new or added longs), so a short at
# the broker is unclassifiable and must fail closed.
_CLOSING_SIDE = {"long": "sell"}

# "The fund has no record of opening this position" — distinct from "it was
# opened with no stop on purpose". The first fails closed, the second is fine.
_UNKNOWN = object()


def _qty(value) -> int | None:
    """Whole-share count from a string or int; None if unreadable. Broker
    numerics arrive as strings. Fractional, negative, bool and unparseable all
    return None and therefore alert.

    Twin of gate/tickets.py:_as_share_count (which coerces adversarial AGENT
    input) and a cousin of reconcile.py:_parse_fill. Kept separate on purpose:
    this one coerces BROKER output and the two may legitimately diverge.
    Unifying all three into a shared helper is a follow-up — it would mean
    editing reconcile's fill parsing, which does not belong in this diff."""
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != int(n) or n <= 0:
        return None
    return int(n)


def _covering_qty(orders: list, symbol: str, closing_side: str) -> int | None:
    """Shares of `symbol` protected by live stop orders. None if ANY order is
    unreadable — an order that will not parse might be the protective one, so
    the answer is 'unknown', never a smaller number."""
    total = 0
    for o in orders:
        if not isinstance(o, dict):
            return None
        if str(o.get("symbol") or "") != symbol:
            continue
        if str(o.get("side") or "").lower() != closing_side:
            continue
        if str(o.get("type") or "").lower() not in _STOP_TYPES:
            continue
        n = _qty(o.get("qty"))
        if n is None:
            return None
        total += n
    return total


def _promised_stop(conn: sqlite3.Connection, symbol: str):
    """The stop the fund promised when it LAST opened this symbol: the price,
    None if that buy deliberately carried no stop, or _UNKNOWN if there is no
    filled buy order for it at all.

    Most-recent rather than any: a symbol sold and re-bought without a stop is
    read on its current terms instead of inheriting an old promise forever."""
    row = conn.execute(
        "SELECT t.stop_price AS stop_price FROM orders o"
        " JOIN tickets t ON t.id = o.client_order_id"
        " WHERE o.symbol = ? AND o.side = 'buy'"
        "   AND (o.status = 'filled' OR o.filled_qty > 0)"
        " ORDER BY o.submitted_at DESC LIMIT 1",
        (symbol,)).fetchone()
    if row is None:
        return _UNKNOWN
    return row["stop_price"]


def _evaluate(conn: sqlite3.Connection, positions: list,
              orders: list) -> list[str]:
    """Alert texts for ONE snapshot of the account. Reads only; appends
    nothing, so the caller can evaluate a snapshot, wait, and evaluate a
    fresher one without having written anything it must retract."""
    out: list[str] = []
    for raw in positions:
        p = raw if isinstance(raw, dict) else {}
        symbol = str(p.get("symbol") or "?")
        side = str(p.get("side") or "").lower()
        held = _qty(p.get("qty"))
        closing_side = _CLOSING_SIDE.get(side)
        if held is None or closing_side is None:
            out.append(f"{symbol} position UNVERIFIED — cannot read"
                       f" side={p.get('side')!r} qty={p.get('qty')!r}, so"
                       " whether it is protected is unknown")
            continue
        covered = _covering_qty(orders, symbol, closing_side)
        if covered is None:
            out.append(f"{symbol} {held} UNVERIFIED — a live order for it"
                       " could not be read, so its cover cannot be confirmed")
            continue
        if covered >= held:
            continue
        promised = _promised_stop(conn, symbol)
        if promised is _UNKNOWN:
            out.append(f"{symbol} {held} is held with NO live protective order"
                       " and no fund record of opening it — provenance"
                       " unknown, so whether it should be protected cannot be"
                       " established")
        elif promised is not None:
            # "NO live protective order (30 of 80 stopped)" contradicts
            # itself. Say which of the two situations this actually is.
            shortfall = ("the broker has NO live protective order" if not covered
                         else f"the broker covers only {covered} of {held}"
                              " shares")
            out.append(f"{symbol} {held} was ticketed with a stop at"
                       f" {promised} but {shortfall} — the position is exposed"
                       " and no code path will protect it; place or restore a"
                       " stop manually")
        # promised is None: the PM opened this without a stop on purpose
        # (charters/pm.md:25). Standing exposure, not a fault — reported in
        # the EOD digest, not as an alert.
    return out


def assert_positions_protected(conn: sqlite3.Connection, *, broker,
                               now_iso: str,
                               sleep: Callable[[float], None] | None = None
                               ) -> int:
    """Alert on every open position whose promised stop is not live at the
    broker. Returns the number of alerts appended. Never raises."""
    nap = sleep or (lambda _s: None)

    def alert(text: str) -> None:
        append_event(conn, "alert", {"text": text}, now_iso)

    def why(e: Exception) -> str:
        # The type alone ("ConnectionError") is not actionable at 16:05 on a
        # day nobody is reading logs; "401 unauthorized" is.
        return f"{type(e).__name__}: {str(e)[:120]}"

    def read_orders() -> list:
        return list(broker.open_orders())

    if broker is None:
        alert("position protection UNVERIFIED — no broker wired into the run;"
              " a held position could be unprotected and nothing would say so")
        return 1
    try:
        positions = list(broker.open_positions())
    except Exception as e:
        alert("position protection UNVERIFIED — could not read positions"
              f" ({why(e)}); a held position could be unprotected and nothing"
              " would say so")
        return 1
    if not positions:
        return 0
    def unread(how: str, e: Exception) -> str:
        return (f"position protection UNVERIFIED — holding {len(positions)}"
                f" position(s) but could not {how} live orders ({why(e)});"
                " cover is unknown, not confirmed")

    try:
        problems = _evaluate(conn, positions, read_orders())
    except Exception as e:
        alert(unread("read", e))
        return 1
    if problems:
        # An OTO stop leg is created 'held' and can lag its parent in the API
        # by moments — and this runs immediately after reconciliation, which
        # is exactly when a fill just happened. Without one short wait and a
        # re-read, the fund would alert on every position it correctly
        # protects, on every day it actually trades. Once per run, never once
        # per position.
        nap(_RETRY_S)
        try:
            problems = _evaluate(conn, positions, read_orders())
        except Exception as e:
            alert(unread("re-read", e))
            return 1
    for text in problems:
        alert(text)
    return len(problems)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_protection.py -v`
Expected: PASS (all 21 tests)

- [ ] **Step 5: Check purity and the full suite**

Run: `make test`
Expected: 0 failures. `scripts/check_purity.py` runs inside it — `orchestrator/protection.py` imports no LLM code and calls no wall clock (`now_iso` is injected), so it must pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/protection.py tests/test_protection.py
git commit -m "feat: assert every open position has a live protective order"
```

---

### Task 5: Wire the assertion into the day

**Files:**
- Modify: `orchestrator/daily.py:22-27` (import), `orchestrator/daily.py:449-466` (`run_day`)
- Test: `tests/test_daily_stages.py:363,525,546,563` (four `run_day` call sites) + new tests

**Interfaces:**
- Consumes: `assert_positions_protected(conn, *, broker, now_iso)` from Task 4
- Produces: no new symbols; `run_day`'s signature is unchanged

- [ ] **Step 1: Give the existing stage tests a broker**

The four `run_day(...)` calls in `tests/test_daily_stages.py` pass `broker=None`, which now alerts. Those tests are about the stage machine, not about protection, so give them a broker that holds nothing — their assertions (including `events == 2` and "nothing new on re-run") then stay exactly as written and keep testing what they name.

Add near the top of `tests/test_daily_stages.py`, after the imports:

```python
class _FlatBroker:
    """Holds nothing, so the protection assertion is silent. Stage tests are
    about the stage machine; protection has its own file."""
    def open_positions(self): return []
    def open_orders(self): return []


class _NakedBroker:
    """Holds NVDA 80 with nothing protecting it — the 2026-08-19 account."""
    def open_positions(self):
        return [{"symbol": "NVDA", "qty": "80", "side": "long"}]
    def open_orders(self): return []
```

Then replace `broker=None` with `broker=_FlatBroker()` at all four call sites (lines 363, 525, 546, 563).

- [ ] **Step 2: Write the failing tests**

Append to the `run_day` section of `tests/test_daily_stages.py`:

```python
def test_run_day_alerts_when_a_position_has_no_protective_order(fund_db,
                                                                sim_clock,
                                                                tmp_path):
    """A full-HOLD day with a naked position must not be a quiet day. This is
    the 2026-08-19 shape exactly: holds, no tickets, no orders, AUDIT CLEAN —
    and NVDA 80 sitting unprotected the whole time.

    This exercises the wiring (the assertion runs at all, and its alert
    reaches Slack), via the unknown-provenance path: the DB here has no filled
    buy for NVDA, which fails closed. The promise logic itself is covered in
    tests/test_protection.py."""
    slack = FakeSlack()
    ctx = StageCtx(conn=fund_db, run_date=RUN, clock=sim_clock, slack=slack,
                   market_inputs={"NVDA": _nvda_inputs()}, run_turn={},
                   id_factory=lambda: TID, journals_root=tmp_path / "journals")
    run_day(ctx, execution_turn=None, broker=_NakedBroker(), sleep=lambda s: None)

    texts = _alert_texts(fund_db)
    assert any("NVDA" in t and "NO live protective order" in t for t in texts)
    # and it REACHED Slack the same day, not just the database
    assert any("NO live protective order" in p["text"]
               for p in slack.posts["#risk"])
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE posted_at IS NULL"
                           ).fetchone()["c"] == 0


def test_the_protection_alert_reaches_slack_even_when_close_is_done(fund_db,
                                                                    sim_clock,
                                                                    tmp_path):
    """The assertion is not a stage, so a resumed day re-checks — but the
    close stage IS a stage, and a 'done' close returns before draining. Without
    an explicit drain the alert would sit in the outbox until the next run."""
    def day():
        ctx = StageCtx(conn=fund_db, run_date=RUN, clock=sim_clock,
                       slack=FakeSlack(),
                       market_inputs={"NVDA": _nvda_inputs()}, run_turn={},
                       id_factory=lambda: TID,
                       journals_root=tmp_path / "journals")
        run_day(ctx, execution_turn=None, broker=_NakedBroker(), sleep=lambda s: None)
        return ctx

    day()
    second = day()          # every stage 'done'; only the assertion re-runs
    assert any("NO live protective order" in p["text"]
               for p in second.slack.posts["#risk"])
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE posted_at IS NULL"
                           ).fetchone()["c"] == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_daily_stages.py -v -k "protective or close_is_done"`
Expected: FAIL — no alert is appended, because `run_day` does not call the assertion yet.

- [ ] **Step 4: Wire it in**

In `orchestrator/daily.py`, add to the imports (after the `orchestrator.reconcile` import on line 23):

```python
from orchestrator.protection import assert_positions_protected
```

Then in `run_day`, between the reconciliation stage and the close stage:

```python
    run_stage(ctx, "reconciliation",
              lambda: reconcile_orders(ctx.conn, clock=ctx.clock, broker=broker,
                                       sleep=sleep or (lambda _s: None)))
    # NOT a stage: an assertion must re-check on a resumed day, never be
    # skipped as 'done'. Drained explicitly because a resumed day can find
    # close already 'done', and run_stage returns before draining — which
    # would leave a naked-position alert sitting in the outbox until the
    # next run. `sleep` is threaded through for the one short re-read a
    # just-created OTO leg needs to become visible.
    now = iso(ctx.clock.now())
    if assert_positions_protected(ctx.conn, broker=broker, now_iso=now,
                                  sleep=sleep):
        drain(ctx.conn, ctx.slack, now)
    run_stage(ctx, "close", lambda: run_close(ctx))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_daily_stages.py -v`
Expected: PASS, all of them — including `test_full_hold_day_is_rerunnable`, whose `events == 2` assertion is preserved by the `_FlatBroker` from Step 1.

- [ ] **Step 6: Run the full suite and commit**

Run: `make test`
Expected: 0 failures.

```bash
git add orchestrator/daily.py tests/test_daily_stages.py
git commit -m "feat: the day asserts every position is protected before it closes"
```

---

### Task 6: Reproduce the incident offline

**Files:**
- Modify: `tests/fake_alpaca.py:52-60` (`__init__`), `:62-95` (`place_order`), `:122-144` (`tick`), plus two new methods
- Modify: `tests/test_sim_day.py` (append one test)

**Interfaces:**
- Consumes: `FakeAlpaca` from `tests/fake_alpaca.py`; `run_day` from Task 5
- Produces: `FakeAlpaca.open_positions()`, `FakeAlpaca.open_orders()` matching the Task 3 dict shapes; `mode="stop_expires_at_the_bell"`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sim_day.py`:

```python
def test_the_stop_that_expired_at_the_bell_is_caught(tmp_path):
    """2026-08-19, reproduced. A stopped NVDA entry fills, its OTO stop leg
    then EXPIRES at the close exactly as the real one did on 08-17, and the
    day must not end quietly. Before this assertion existed the run was clean
    — three holds, AUDIT CLEAN, a digest posted — while the position sat
    naked. A green suite proved nothing; this is what proving looks like."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  pm_recs=("mvf_pm_stop.jsonl",),
                  exec_recs=("mvf_exec_stop_gtc.jsonl",),
                  broker_mode="stop_expires_at_the_bell")
    texts = [json.loads(r["payload"])["text"] for r in sim.conn.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id").fetchall()]
    assert any("NVDA" in t and "stop at 168" in t for t in texts), (
        f"the naked position was not reported; alerts were {texts}")
    assert any("NO live protective order" in p["text"]
               for p in sim.slack.posts["#risk"])
```

`sim_day` is defined at `tests/test_sim_day.py:87` and returns a `SimResult`
dataclass (`:76`) with `.conn`, `.slack`, `.broker`, `.clock`, `.run_date`.
`_nvda()` is the golden-day market fixture at `:57`.

- [ ] **Step 1a: Add the two recordings this day needs**

The golden-day recordings cannot express a stopped entry: `mvf_pm.jsonl` passes
no `stop_price` (its invalidation names "close below 168" in prose only), and
`mvf_exec.jsonl` places a plain order. `oto_gtc.jsonl` from Task 2 is sized for
the hook test's `max_qty=67` and would be denied here, where the gate sizes the
golden day to **66**. Two new files — never edits of existing recordings:

`tests/recordings/mvf_pm_stop.jsonl` (one line, `mvf_pm.jsonl` plus the stop the
prose already implies):

```
{"seat": "pm", "tool": "mcp__fund__submit_decision", "args": {"ticker": "NVDA", "action": "buy", "qty": 80, "thesis": "Capex re-acceleration confirmed by two prints; bear case reduced to timing.", "invalidation": "Top-2 hyperscaler guides capex flat-or-down QoQ, or close below 168.", "stop_price": 168.0}}
```

`tests/recordings/mvf_exec_stop_gtc.jsonl` (two lines, the golden exec turn
carrying the ticket's stop, at the golden size of 66):

```
{"seat": "exec", "tool": "mcp__fund__list_open_tickets", "args": {}}
{"seat": "exec", "tool": "mcp__alpaca__place_stock_order", "args": {"client_order_id": "a3f90000-0000-4000-8000-000000000001", "symbol": "NVDA", "side": "buy", "qty": "66", "type": "market", "time_in_force": "gtc", "order_class": "oto", "stop_loss_stop_price": "168.0"}}
```

The gate caps the PM's 80 to 66 and mints a ticket with `stop_price` 168.0, so
the exec turn's qty, stop and `gtc` all match and the order is approved.

- [ ] **Step 1b: Give `sim_day` a broker mode**

`sim_day` hardcodes its broker at `tests/test_sim_day.py:105`. Add a keyword
argument, defaulted so every existing caller is untouched:

```python
def sim_day(tmp_path, *, market: dict,
            analyst_recs=("mvf_analyst.jsonl",),
            pm_recs=("mvf_pm.jsonl",),
            exec_recs=("mvf_exec.jsonl",),
            feed_break: dict | None = None,
            slack=None,
            id_factory=None,
            broker_mode: str = "fill") -> SimResult:
```

and at line 105:

```python
    broker = FakeAlpaca(PRICES, FILL_PRICES, mode=broker_mode)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sim_day.py -v -k "expired_at_the_bell"`
Expected: FAIL — `FakeAlpaca` has no `open_positions`, and no mode by that name.

- [ ] **Step 3: Teach `FakeAlpaca` about positions and the child stop leg**

In `tests/fake_alpaca.py`, in `place_order`, after `self.orders[coid] = order` and before `return dict(order)`, create the OTO's child leg as its own order — which is how the real broker returns it with `nested=False`:

```python
        # An OTO's stop leg is a SEPARATE order at the broker, with its own
        # lifetime. Modelling it as one indivisible parent is what let the
        # 2026-08-17 leg die unnoticed: nothing in this fake could express
        # "the parent filled and the protection is gone".
        if args.get("stop_loss_stop_price") is not None:
            leg_id = f"{coid}-stop"
            self.orders[leg_id] = {
                "id": f"alp-{len(self.orders) + 1:04d}",
                "client_order_id": leg_id,
                "symbol": symbol,
                "side": "sell" if args["side"] == "buy" else "buy",
                "qty": args["qty"],
                "status": "held",          # activates when the parent fills
                "filled_qty": 0,
                "filled_avg_price": None,
                "order_class": "",
                "order_type": "stop",
                "stop_loss_stop_price": None,
                "stop_loss_limit_price": None,
                "take_profit_limit_price": None,
                "parent": coid,
            }
```

In `__init__`, add `"stop_expires_at_the_bell"` handling by recording nothing new — the mode string is already stored on `self.mode`.

In `tick`, add the leg's lifecycle. Replace the early-return guard:

```python
        if self.mode in ("instant", "never_fill", "fill_during_cancel"):
            return
```

with:

```python
        if self.mode in ("instant", "never_fill", "fill_during_cancel"):
            return
        if self.mode == "stop_expires_at_the_bell":
            # The 2026-08-17 defect: the parent fills, and the stop leg dies
            # at the close of the same session because it inherited tif DAY.
            for order in self.orders.values():
                if order.get("parent") is None and order["status"] == "accepted":
                    order["status"] = "filled"
                    order["filled_qty"] = order["qty"]
                    order["filled_avg_price"] = self.fill_prices.get(
                        order["symbol"], self.prices[order["symbol"]])
                elif order.get("parent") is not None:
                    order["status"] = "expired"
            return
```

Then in the existing `mode == "fill"` branch, activate the leg when its parent fills so the healthy path stays protected:

```python
            if self.mode == "fill" and order["status"] == "accepted":
                order["status"] = "filled"
                order["filled_qty"] = order["qty"]
                order["filled_avg_price"] = px
            elif self.mode == "fill" and order["status"] == "held":
                order["status"] = "new"          # the stop is now working
```

Finally add the two read methods at the end of the class:

```python
    def open_positions(self) -> list[dict]:
        """Positions implied by filled orders, in AlpacaSource's dict shape
        (numbers as STRINGS — the caller must do its own coercion)."""
        held: dict[str, int] = {}
        for o in self.orders.values():
            if o["status"] != "filled":
                continue
            n = int(o["filled_qty"] or 0)
            held[o["symbol"]] = held.get(o["symbol"], 0) + (
                n if o["side"] == "buy" else -n)
        return [{"symbol": s, "qty": str(q), "side": "long"}
                for s, q in sorted(held.items()) if q > 0]

    def open_orders(self) -> list[dict]:
        """Every order still working, legs flattened — matching
        AlpacaSource.open_orders (nested=False)."""
        return [{"symbol": o["symbol"], "side": o["side"], "qty": str(o["qty"]),
                 "type": o.get("order_type", "market"), "status": o["status"]}
                for o in self.orders.values()
                if o["status"] in ("new", "accepted", "partially_filled",
                                   "held")]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sim_day.py -v`
Expected: PASS — the new test, and every pre-existing sim day still green. If a pre-existing sim day now alerts about an unprotected position, that is a real finding about the fake's fill model: read it, do not silence it.

- [ ] **Step 5: Run the full suite and commit**

Run: `make test`
Expected: 0 failures.

```bash
git add tests/fake_alpaca.py tests/test_sim_day.py
git commit -m "test: the stop that expired at the bell, reproduced offline"
```

---

### Task 7: Prove it against the real broker (live, pre-merge)

**Files:**
- Modify: `tests/test_live_smoke.py` — `test_a_stopped_ticket_places_with_a_flat_stop_leg` (line 243)

**Interfaces:**
- Consumes: the gate rule from Task 2
- Produces: nothing code-level; the merge gate

- [ ] **Step 1: Assert the placed order and its leg are both `gtc`**

The test at `tests/test_live_smoke.py:243` already places a real 1-share paper AAPL order with a resting stop 30% below the market and cleans up after itself. It binds the resolved broker order to `order`, and polls (up to 10 times, 2s apart) because an OTO child can lag the parent in the API. Reuse that same polled `order` — do not re-query.

Immediately after the existing `assert stop_price in leg_stops, (...)` block, add:

```python
    # The 2026-08-19 rule, proven at the broker. The gate now denies anything
    # but gtc on a stop-carrying order (gate/tickets.py) — this is the other
    # half: that Alpaca ACCEPTS gtc on an OTO market parent and hands the
    # lifetime down to the leg. If the leg comes back 'day', the stop dies at
    # the bell and the whole rule bought nothing. That is not a hypothetical:
    # it is what happened to NVDA on 2026-08-17.
    assert str(order.get("time_in_force")).lower() == "gtc", order
    for leg in (order.get("legs") or []):
        assert str(leg.get("time_in_force")).lower() == "gtc", leg

    # And the leg is VISIBLE to the protection check. open_orders() filters
    # QueryOrderStatus.OPEN; a resting OTO child sits in 'held', and if
    # Alpaca's OPEN filter excluded it, orchestrator/protection.py would
    # report every correctly-stopped position as naked. Offline tests cannot
    # settle this — it is server-side semantics, the same class of assumption
    # that caused 2026-08-17.
    from market.source_alpaca import AlpacaSource
    live = [o for o in AlpacaSource().open_orders() if o["symbol"] == "AAPL"]
    assert any(o["type"].startswith("stop") and o["side"] == "sell"
               for o in live), (
        "the resting stop leg is invisible to open_orders() — protection.py "
        f"would call this position naked. Saw: {live}")
```

- [ ] **Step 2: Run it against paper**

Run: `set -a; source .env; set +a; .venv/bin/python -m pytest -m live tests/test_live_smoke.py -k stopped_ticket -v`
Expected: PASS. It places a real paper order and cancels it.

**Three distinct failures are possible here. They mean different things:**

- **The leg comes back `day` while the parent is `gtc`.** The gate rule is necessary but not sufficient — Alpaca is not handing the lifetime down. Stop and report; the design needs a second mechanism.
- **Alpaca rejects `gtc` on an OTO market parent (422).** The rule is not deliverable as written. Stop and report; Task 2 must be reconsidered.
- **The resting leg does not come back from `open_orders()`.** Alpaca's `OPEN` status filter excludes `held`, so the protection check cannot see any OTO stop leg and would report every correctly-stopped position as naked. Fix is to widen the filter (or drop it and filter client-side); stop and report before shipping the assertion.
- **The seat never sends `gtc` and the gate denies it.** This is the one to watch. No charter, seat config, or spec sets `time_in_force` — by design, the denial message is the only thing teaching the seat, and it must learn within the turn. If the seat cannot get there, the fix is one line in `charters/exec.md`, **which contradicts a stated non-goal of this work and therefore needs Benjamin's explicit sign-off before it is written.** Do not add it unilaterally.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live_smoke.py
git commit -m "test: a stopped order and its leg both place gtc at the broker"
```

---

### Task 8: Record what changed

**Files:**
- Modify: `PROGRESS.md` — the `2026-08-19 — the stop that expired at the bell` section (line 24)

- [ ] **Step 1: Add the resolution**

Append to that section, after the "Three layers each had a reason not to look" list, a short paragraph naming what now closes each layer: the gate denies a non-`gtc` stop-carrying order, and `orchestrator/protection.py` checks after every run — including on a full-HOLD day, which is the shape this incident had — that a stop the fund promised is still live at the broker. Say which side owns which fact: the broker owns what protection exists, the fund's record owns what was promised, and the 08-17 failure was that nobody compared them.

State plainly that the held NVDA 80 still has no stop, that no code path will place one, and that the assertion now alerts on it every run until it is resolved by hand. Note the one thing still uncovered — a position nobody ever promised to protect is silent here by design, and gets a protection line in the EOD digest in a follow-up branch.

Do not update the `Tests` row's count by guessing — copy the number from the actual `make test` output.

- [ ] **Step 2: Commit**

```bash
git add PROGRESS.md
git commit -m "docs: what closes the missing-stop class, and what is still open"
```

---

## Verification before completion

A green suite is **not** evidence here: the suite was green through all three incidents. Before claiming this is done, produce:

1. `make test` — full offline suite, 0 failures, and the test count is higher than the 810 baseline.
2. `make schema-pin` — passes, including the new `time_in_force` pin (Task 1).
3. The live stopped-ticket round-trip (Task 7) — passes against paper.
4. `.venv/bin/python -m pytest tests/test_sim_day.py -k expired_at_the_bell -v` — the assertion firing against a genuinely unprotected position, with the alert text quoted in the completion report.
5. The production diff, measured: `git diff master --stat -- gate/ orchestrator/ market/` — must be under 110 lines.
6. `tests/test_audit_day.py::test_golden_day_audits_clean` still green, and `git status` showing **no modification** to any pre-existing file under `tests/recordings/`. The golden day opens NVDA from a stopless ticket, so promise-aware alerting leaves it silent — if that test goes red, the promise logic is wrong, not the fixture.
