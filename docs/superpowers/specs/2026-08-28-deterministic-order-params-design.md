# The exec seat stops constructing order parameters

**Date** 2026-08-28 · **Branch** TBD (off `master`) · **Issue** none yet — filed from a
question, not from the board

## The problem

The Execution Trader seat holds no decision authority. `charters/exec.md:13` states it
outright: *"You never decide WHETHER to trade — only HOW to execute what a ticket
authorizes."* The gate has already sized to caps (`specs/design.md:97` — "Resizing to caps
happens **inside the gate** — no LLM round-trip"), so the ticket row fully determines the
order.

What the model still does is **build the `place_stock_order` argument dict from prose**. That
is the entire remaining LLM contribution to the execution stage, and it is where every
recorded failure of this seat has come from. `charters/exec.md:36`:

- **v3** — the stop leg was written as a nested `stop_loss: {stop_price: ...}` object. That
  shape never existed at the broker, so on the first live day every ticket carrying a
  `stop_price` was undeliverable.
- **v4** — `time_in_force` was left to the tool's default of `day`. The 2026-08-17 OTO's stop
  leg expired at that session's close and NVDA sat unprotected for two sessions while
  `decisions.stop_price` asserted a live stop at 215.

Both were fixed the same way: another rule in the charter, and another deny in the gate.
`charters/exec.md:12` is now ten lines of Alpaca API trivia — flat-not-nested, `oto`-not-
`bracket`, `gtc`-not-`day` — carried in a prompt, re-derived by a model on every turn.

Nothing else in the stage is model-driven. State recording is already deterministic: the
PostToolUse hook at `agents/runtime.py:203-260` writes the `orders` row, consumes the ticket,
appends the fill event and transitions the decision. The model contributes none of it.

**The mapping from ticket to order is a total function** on the row
(`state/schema.sql:66-80`: `id`, `ticker`, `side`, `max_qty`, `stop_price`). There is no input
to it that is not in the database. A total function computed by a language model from a prose
rulebook is the defect, and it is the whole defect.

## The change

Four edits. The seam moves; nothing else does.

### 1. `order_params(ticket)` in `gate/tickets.py`

A pure function, ticket row in, `place_stock_order` argument dict out. It sits beside
`validate_order` (`gate/tickets.py:155`) because the two are inverses: one builds the order a
ticket authorizes, the other checks an order against the ticket it claims. Same module, same
contract, one definition of the shape.

| | |
|---|---|
| always | `symbol=ticker`, `side=side`, `qty=max_qty`, `order_type="market"`, `client_order_id=id` |
| `stop_price` NULL | nothing further |
| `stop_price` set | `+ order_class="oto"`, `time_in_force="gtc"`, `stop_loss_stop_price=str(stop_price)` |

`qty` is `max_qty`, not a number the model chooses: the gate already resized, so the ticket
quantity **is** the order quantity.

`stop_loss_stop_price` is a string because the Alpaca MCP place tool sends every numeric as
one — `gate/tickets.py:119-124` documents the string form as the normal case, not the edge
case.

Pure Python, no clock, no LLM import: invariant 3 holds and `scripts/check_purity.py` stays
green.

### 2. `list_open_tickets` returns the params attached to each ticket

The handler at `agents/tools/fund_server.py:439-449` currently returns `open_tickets()` rows
verbatim. It instead returns, per ticket:

```json
{"ticket_id": "...", "expires_at": "...", "order": { ...order_params(row)... }}
```

**`gate.tickets.open_tickets` itself does not change.** `orchestrator/daily.py`'s
`run_execution` calls it too (`gate/tickets.py:40-44`), and that caller wants ticket rows, not
order dicts. The decoration happens in the fund-server handler, which is the only place the
model-facing shape is owed.

No new tool, no new `SEAT_CAPS` entry, no new `contracts.md` §4 row — the existing
`list_open_tickets` row (`specs/contracts.md:281`) keeps its place and its schema block is
rewritten to describe the new return.

This is what makes the mis-pairing failure mode unrepresentable: params arrive already bound
to their ticket, so the model cannot place ticket A's parameters under ticket B's id. The
two-tool alternative (`next_order(ticket_id)` as a separate surface) was considered and
rejected for exactly this, plus 1+N round trips for no gain.

### 3. `charters/exec.md` v5

Rule 6's ten lines of API mechanics come out. They are code now, and a rule that restates what
code guarantees is a rule that can drift from it. What replaces them is one instruction: call
`list_open_tickets`, place each `order` dict **verbatim**, changing nothing.

Rules 4, 5 and 7 stand unchanged — the ticket is still the entire mandate, the
`client_order_id` is still always the ticket id, the 422-means-it-landed reconciliation is
still the seat's, and it still never decides whether to trade.

### 4. `order_type == "market"` in `validate_order`

Three lines in the `stop_price`-set branch's sibling position, beside the existing
`order_class == 'oto'` and `time_in_force == 'gtc'` checks.

Today `order_type` is constrained **nowhere** on the placement side. It appears only in read
paths (`orchestrator/protection.py:67`, `market/source_alpaca.py:147`, the fakes);
`validate_order` has no `order_type` branch, and `charters/exec.md:30` says "Market orders" as
prose without ever naming the parameter. A limit order passes the gate today.

`order_params` will always emit `market`, so this check only bites when the model deviates
from the passthrough — which is precisely the thing that should be verified rather than
trusted.

**It stays a key/enum check.** `validate_order` checks an order's shape against its ticket and
performs no cash or quantity arithmetic; that arithmetic is `gate/risk.py:89-93`'s job — the
cash cap at 89, the sector headroom at 90-91, `no_headroom` at 93 — and is the subject of
issue #38. Keeping the two apart is what keeps this change clear of #38's
region, and it should stay a stated constraint on `validate_order`, not an accident of the
current diff.

## Tests

Written first, per `specs/acceptance.md`'s standing rule.

**The property, which is the point of the whole change:** for every ticket shape,

```python
assert validate_order(conn, order_params(ticket), now) == (True, "ok")
```

The tool's output is gate-legal by construction. The v3 nested-stop and v4 DAY-`tif`
incidents become instances of this property rather than two more prompt rules — and per
`docs/agents/regression-ratchet.md` both are eligible as permanent cases, each having been a
real live failure.

**Manufacture the red before trusting any of it.** A property test that passes on first run
pins nothing. Break `order_params` three ways — emit the nested `stop_loss` object, drop
`time_in_force`, emit `order_type="limit"` — and read the whole failure list each time. If
dropping `time_in_force` does not fail, the property is not wired to the branch that matters.

Unit cases on `order_params`: stop-carrying ticket, unstopped ticket (asserting **no** stop-leg
key is present at all, not merely that the stop price is absent — `_STOP_LEG_KEYS` names three
and an unstopped ticket must carry none), and `stop_price` formatting as a string.

Handler cases on `list_open_tickets`: shape of the returned envelope, expired tickets still
excluded, wrong-seat guard still fires. `tests/test_tool_surface_canon.py` must stay green
against the rewritten §4 schema block, and its parse-the-table discipline means the block is
edited **before** the handler.

`recordings/` holds only `.gitkeep`, so there is no replay migration: no recorded exec turn
exists to go stale.

## Non-goals

Stated because the pressure to widen this is real and the fences are deliberate.

- **The seat is not removed.** Full removal is a strictly better end state and this change
  makes it small — all the logic lands in Python either way — but it is an invariant-2 edit
  and Benjamin's alone.
- **Invariant 2 is untouched.** The exec seat keeps the `trading` toolset; CLAUDE.md and
  `specs/design.md:73` need no edit.
- **The fund server gains no broker handle.** It stays pure SQLite
  (`agents/tools/fund_server.py:18-25`). This is what keeps `orchestrator/broker.py:11-13` —
  *"this port never places, and must never grow a method that does"* — untouched rather than
  merely unmentioned.
- **The PreToolUse hook stays the choke point.** Placement remains agent-side behind
  `make_order_gate`; this change alters who fills in the dict, not who checks it.
- **`gate/risk.py` is not touched.** Cap arithmetic is #38's region.

## Considered and rejected

- **A separate `next_order(ticket_id)` tool.** Cleaner conceptual seam, but a new MCP surface,
  a new `SEAT_CAPS` cap, a new §4 row, 1+N round trips, and it leaves ticket-A-params-under-
  ticket-B-id representable. Folding into `list_open_tickets` is strictly smaller.
- **The tool submits the order itself.** Fully deterministic — no transcription step at all —
  but it requires a broker handle inside the fund server, moves placement out from behind the
  PreToolUse hook, and makes invariant 2's wording stale. Larger blast radius on the most
  safety-critical path in the repo, and the residual transcription risk it removes is already
  contained: a mis-copy is denied by the gate, resolves to HOLD, and raises
  `_alert_unexecuted_tickets`. A missed trade with an alert, never a wrong order.
- **Harden the charter again.** What v3 and v4 already did. Two incidents in, the evidence is
  that prose does not hold this.

## Open

Nothing blocking. The branch name is unset because no issue exists yet; if this should go on
the board before implementation, the issue is filed first and the branch named after it.
