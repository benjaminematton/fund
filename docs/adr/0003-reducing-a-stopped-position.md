# Reducing a stopped position: amend the stop, never cancel it

Status: proposed (2026-08-20) — the path is chosen; the `specs/contracts.md` change it
requires is a 🔏 human ruling and has not been made.

A full-size protective stop reserves the entire position at the broker, so the fund
cannot sell part of a position it has protected. The fix is to shrink the stop first —
editing the live stop order down to the retained size, under a gate ticket that
authorizes exactly that edit — and then place the reducing sell. The rejected
alternative, cancelling the stop and re-placing a smaller one afterwards, costs a window
in which the position is unprotected and adds a failure mode where a successful cancel
followed by a failed sell leaves the whole position naked.

## The defect

Verified on the live paper account 2026-08-20 (`root@138.197.47.97:/var/lib/fund/fund.sqlite`,
account `PA3F4TS1XLKV`):

- decision `11 NVDA sell 40` reached `approved`, ticket `c0a9ae97` reached `open`, and the
  execution turn placed no order;
- `run_day: ALERT audit 2026-08-20 FAILED: decision NVDA stuck at approved`, `status=1/FAILURE`;
- `get_open_position("NVDA")` → `qty 80, qty_available 0`.

The mechanism is the reservation: an 80-share GTC stop against an 80-share position holds
every share, so `qty_available` is 0 and any sell is refused. **This is not specific to
that stop having been placed by hand.** Any full-size stop does it, and `gtc` on a stopped
order is now required fund-wide (`94e0f6b`, `2f238ab`), so every position the PM opens with
a hard invalidation joins the set of positions that cannot be trimmed. The generality is
the finding; the NVDA ticket is one instance of it.

## What the exec seat can actually do

Three constraints were assumed during the first analysis and turned out to be false. They
are recorded here because each of them, taken at face value, rules out the decision above.

**The seat already holds order-mutation tools.** `alpaca-mcp-server@2.2.1` with
`ALPACA_TOOLSETS=account,trading,stock-data` — the exec seat's configured surface
(`agents/config/exec.yaml`) — registers 38 tools, among them `replace_order_by_id`,
`cancel_order_by_id`, `cancel_all_orders`, `close_position` and `close_all_positions`. The
three `place_*` tools are *overrides* that replace the generated `postOrder`; the rest of
the `trading` toolset is generated from its operation list. Verified by `tools/list`
against the running server, the method `tests/test_live_smoke.py` already uses for its
schema pin. So no vendor fork, no in-process broker tool, and no upstream release is
needed for an amend to be physically possible.

**A stop leg cannot protect a position a sell is reducing.** The obvious shape — sell 40
carrying an OTO stop for the remaining 40 — is not expressible. Alpaca's exit legs take
only prices (`stop_loss_stop_price`, `stop_loss_limit_price`, `take_profit_limit_price`);
they carry no side and no quantity, so the leg's side is derived as the opposite of the
primary. On a `sell` primary the leg is a *buy* stop, which protects nothing about the
retained long shares. `submit_decision` already encodes this — its `stop_price` is
documented "buy only" (`agents/tools/fund_server.py`). This is derived from the parameter
shape rather than from a live rejection; it has not been probed against the broker.

**A standalone protective stop is an ordinary placement.** `place_stock_order` exposes
top-level `type` and `stop_price`, so `side=sell, type=stop, stop_price=…, time_in_force=gtc`
is placeable through the normal gated path, and `orchestrator/protection.py` already counts
`("stop", "stop_limit", "trailing_stop")` as protection. Re-protecting a remainder therefore
needs a second *ticket*, not a new tool — which is why the cancel-based alternative is not
the cheap option it appears to be.

## The decision

The PM's reduce decision mints **two** tickets: the ordinary sell ticket, and an *amend
ticket* naming the protective order and the quantity it may be reduced to. The execution
seat edits the stop under the amend ticket, then places the sell under the sell ticket.

How the amend ticket *attaches* is deliberately left open. Hanging it off the same
`decision_id` as the sell ticket is the obvious reading and appears to be the wrong one —
see the first consequence below, which is the sharpest constraint on the design.

This requires the PreToolUse gate to authorize a second verb. Today `make_order_gate`
inspects only tool names beginning with `mcp__alpaca__place_` (`agents/runtime.py`), so an
edit is invisible to it — the seat could shrink a stop right now with no ticket and nothing
would record or refuse it. An amend that the gate cannot see is not an option; extending
the gate is not an add-on to this decision, it *is* the decision.

## Why

**It is the only sequence with no unprotected window.** Shrinking the stop to the retained
size leaves those shares covered at every instant. The shares that go briefly uncovered are
the ones being sold, which is the exposure the decision already accepted.

**"Leave it alone" is not the conservative option.** Its cost is that the fund permanently
cannot reduce a protected position, and it fails loudly and repeatedly: the PM decides
trim, the gate mints a ticket, the seat cannot fill it, the day's audit reds. That is what
2026-08-20 looked like, and it recurs on every such decision.

**The alternative change is not cheaper.** Cancel-and-re-place needs a protective-stop
ticket kind and its own gate rules, so it is a `specs/contracts.md` change too. It buys no
simplification and pays for that with risk.

## Considered options

- **Cancel the stop, sell, then place a fresh smaller stop.** Rejected. Two windows in
  which the position is unprotected, and a worse failure: if the cancel lands and the sell
  errors, the *entire* position is naked with no stop and nothing notices until the
  end-of-day protection assertion. Invariant 4 says an error resolves to no action; here an
  error resolves to strictly more exposure than before the attempt.
- **Have deterministic code do the replace via `BrokerPort`.** Rejected, and it should stay
  rejected. `alpaca-py` 0.44.0 exposes `replace_order_by_id`, so this is physically
  reachable from `market/source_alpaca.py`. But `orchestrator/broker.py` states the
  prohibition directly — "this port never places, and must never grow a method that does" —
  and a replacement order is a new order at the broker. It would exist outside the gate
  entirely, which is the substance of invariant 2, not a technicality of it.
- **Block reduce decisions on stopped positions.** Not rejected — held in reserve. If the
  amend work is deferred, the gate should refuse the *decision* rather than mint a ticket
  that cannot be filled. That stops the daily audit failure without touching the tool
  surface, and it is honest about the capability the fund lacks. It is a holding position,
  not a resolution: the PM's decision space silently shrinks as the book gets more
  disciplined.
- **Size protective stops below the position.** Rejected without much deliberation: it
  trades an inability to trim for a permanently partly-unprotected position, which is a
  worse trade at every size.

## Consequences

- **This is the first mutation verb the agents get.** The architecture's clean split —
  agents may only *place*, deterministic code may only *cancel*, every order at the broker
  traces to a ticket that authorized its creation — no longer holds as stated. An amended
  order is one that was never placed. The split is what makes the order path reviewable, so
  the replacement rule has to be stated as plainly as the one it replaces: every order at
  the broker traces to a ticket that authorized either its creation or its amendment.
- **A replacement order carries a new `client_order_id`.** Alpaca's edit does not mutate in
  place: the original goes to `status: replaced` with `replaced_by` naming the new order,
  and `replace_order_by_id` accepts a fresh `client_order_id`. Invariant 5 holds if that id
  is the amend ticket's id — but no order in this codebase has ever been born from anything
  but a placement, so `specs/contracts.md` has to say so explicitly rather than leave it
  inferred.
- **`charters/exec.md` Rule 7 changes.** It currently reads "You never modify, cancel, or
  work an order beyond the ticket's terms" — an absolute the seat can no longer honor. It
  becomes an exception under an amend ticket, and the charter version increments.
- **`orchestrator/protection.py` needs no change for the end state.** It already treats a
  standalone `stop` order as protection and compares promised against actual. It does need
  to survive the transient: between the amend and the sell fill, the promised and actual
  stop quantities disagree with the position.
- **One ticket per decision is assumed in three places, and the third reaches
  calibration.** This is the open question in the design, not a detail of it.
  - `_gate_handle` (`orchestrator/daily.py`) looks up a decision's ticket with
    `SELECT * FROM tickets WHERE decision_id = ?` and `fetchone()`, then reconciles that
    single ticket by UPDATE rather than expire-and-remint — deliberately, since the ticket
    id *is* the `client_order_id` and a remint would break idempotency.
  - `_gate_reject` (same file) closes an existing open ticket with a second, separate
    `fetchone()` before rejecting, and its comment states the hazard it exists to prevent:
    a resumed snapshot that now rejects must "never leave a live ticket behind for
    `validate_order` to still authorize." Against a pair it closes one. A rejected reduce
    would leave a live amend ticket still authorizing a stop edit.
  - `orchestrator/resolve.py`'s `_DUE` query `LEFT JOIN tickets t ON t.decision_id = d.id`,
    then joins `orders` through `t.id` to recover the fill price. A second ticket under the
    same decision fans that row out, and `resolutions.decision_id` is UNIQUE
    (`calibration/rows.py`) — so the pair either collides on insert or silently decides the
    recorded fill by which row the join happens to yield. `orchestrator/daily.py`'s digest
    query and `scripts/audit_day.py` join tickets to decisions the same way.

  The last one runs into analyst scoring and from there into PM weights, which is the path
  CLAUDE.md singles out as corrupting fund-wide when it goes wrong quietly. So **hanging
  the amend ticket off `decision_id` is probably the wrong shape.** Making it reference the
  reduce ticket as its parent, or giving tickets a kind that every `decision_id`-keyed join
  filters on, both avoid the fan-out — but that choice belongs in `specs/contracts.md`
  with the state machine, and it is not made here.
- **A reduce ticket without its amendment must resolve to HOLD.** The pairing is only
  meaningful if an unpaired reduce ticket is unfillable, so the gate should refuse to mint
  one against a stopped position rather than leave the seat to discover the reservation at
  the broker. Tickets predating this change are exactly that shape: `c0a9ae97` (NVDA sell
  40, 2026-08-20) is still `open` and stays that way until a run calls
  `expire_open_tickets`. It will not be offered to the seat — `open_tickets()` filters on
  expiry at read time — but anything querying `tickets.status` directly sees an open sell
  that can never fill.
- **The seat's ungated mutation surface is a separate exposure.** `cancel_all_orders`,
  `close_position`, `close_all_positions` and `update_account_config` are reachable today
  under the `mcp__alpaca__*` glob with nothing but charter text between the seat and the
  book; `tests/test_exec_seat_tool_surface.py` asserts the glob and never the resolved tool
  names. Gating the amend verb does not close that, and closing it is not required by this
  decision. It is recorded here because it was found while verifying this one, and because
  CLAUDE.md's "`tools` governs availability — the real lock" does not currently describe the
  config.
