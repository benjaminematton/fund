# One seat capability table, not four parallel lists

Status: accepted (2026-08-18)

Registering a seat in `agents/tools/fund_server.py` meant editing **four** structures
that had to agree: `SIGNAL_SEATS`, `DECISION_SEATS`, `BRIEF_SEATS`, and `tools_by_seat`.
Miss one and the seat is half-wired — it can signal but gets no brief, or it is granted
tools it has no capability entry for. These are replaced by a single `SEAT_CAPS` dict
mapping each seat to a frozenset of capability strings, with a `_can(seat, cap)`
predicate that every guard reads.

## Why

**The hazard is scheduled, not hypothetical.** `specs/design.md` §2 commits to 11 seats.
Phase 3 alone adds Bull, Bear, Critic, Macro, and Ops. Four edits per seat across five
new seats is twenty chances to half-wire one, and the failure is quiet: a seat that
silently gets no tools is, in the words of the existing guard, "the analyst never
recording a signal all day."

**It also closed a real inconsistency.** `handle_get_stage_brief` filled `cash` and
`positions` for *every* brief seat, but the News/Sentiment row in `specs/design.md` §2 is
`news,stock-data` with no `account` toolset. Without a per-section capability the new
seat's brief would have contradicted the toolset the seat table grants it. `read_account`
is a genuinely new gate — nothing gated those fields before.

## The naming rule

A capability that grants a **tool** is named exactly after that tool. A capability that
grants a **brief section** is named `read_*`.

    "analyst": frozenset({"get_stage_brief", "submit_signal", "read_account"}),
    "news":    frozenset({"get_stage_brief", "submit_signal"}),
    "pm":      frozenset({"get_stage_brief", "submit_decision", "read_account",
                          "read_signals", "read_allowed_actions"}),
    "exec":    frozenset({"list_open_tickets"}),

Two properties follow. The *kind* of a grant is readable from its name, so tool
availability and brief content can't be confused in a dict whose whole purpose is that a
seat's surface is legible from its entry. And every capability not starting with `read_`
must be a registered tool name — which is asserted against servers built from the table,
not merely intended, so a typo'd capability fails at test time rather than at seat-build
time on a live host.

An earlier draft used short names (`signal`, `signals`, `account`, `brief`). It was
rejected: `signal` (may call `submit_signal`, a write) and `signals` (brief carries the
signal table, a read) differed by one character while granting unrelated things, so a
transposition would silently add or remove a **write** capability.

## `read_signals` and `read_allowed_actions` are separate on purpose

`handle_get_stage_brief` gated two sections on `DECISION_SEATS`. Folding both under one
capability preserves today's behavior exactly — only the PM holds either — so this is a
precision decision, not a bug fix. It is worth the extra string because `specs/design.md`
§2 commits to Bull and Bear Researchers, both read-only and both plausibly wanting the
day's signals **without** the gate's share budget. Under a single capability that is
inexpressible without re-splitting the dict later.

Splitting them also exposed a latent wrong dependency: the snapshot is needed for
`read_account` and `read_allowed_actions`, not for `read_signals` — signal rows come from
SQLite, not from the account snapshot.

## Considered options

- **Keep the four lists, add a fifth for the account gate.** Rejected: it is the
  anti-pattern this ADR exists to remove, and it was the first proposal made here.
- **Move the grants into `agents/config/*.yaml`.** Rejected. These capabilities are what
  stop a seat writing state it should not; a config typo must never be able to widen a
  write surface. `tools` in the seat yaml already governs *availability* of MCP tools —
  this table governs what the in-process fund server will *accept*, and that belongs next
  to the handlers that enforce it.
- **A closed enum or `Literal` for capability names.** Rejected: it would force a second
  edit on anyone adding a seat with new capabilities, defeating the one-entry benefit.
  The set is deliberately open strings, disciplined by the naming rule and its test.

## Consequences

- Registering a seat is one dict entry. The unknown-seat `ValueError` in
  `build_fund_server` is unchanged and still the hard stop it always was; `_can()`
  returning `False` for an unrecognized seat matches the pre-existing handler behavior,
  which returned `{"ok": False, ...}` and never raised.
- Refusal messages are standardized to `f"{tool} is not granted to seat {seat!r}"`, so
  `is not granted to seat` greps every refusal in the file. The previous per-handler
  wording (`"submit_signal is analyst-seat-only"`) became false once a second seat could
  signal.
- A test asserts `set(configs) <= set(SEAT_CAPS)` — every seat with a config yaml has
  capabilities. The reverse is deliberately **not** asserted: a config without
  capabilities is an outage when something builds that seat, while capabilities without a
  config is a dead dict entry nothing can build. Equality would also couple this file's
  commit boundaries to unrelated branches for no safety gain.
- This lands before the Critic seat's registration (see the coordination note in
  `docs/superpowers/plans/2026-08-18-second-analyst-seat.md`), so that seat registers as
  one entry rather than four edits.
