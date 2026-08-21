# Handoff — does the gate verify its own assumptions?

**Written** 2026-08-20 by `fund-4a` · **Start from** `origin/master` · **Droplet** `09a7a7c`

Benjamin's decision #4. Unowned, nobody is currently in `gate/`, and it is the
only open item where **the system believes something false about itself**.

**This is design-first. Do not open an editor until the question below is
settled with Benjamin.**

---

## The question

Invariant 3 says gate thresholds change only by human commit, and that is
genuinely enforced — `SECTOR_CAP`, `MAX_POSITIONS`, `CIRCUIT_BREAKER` are
module constants in `gate/risk.py`, and `scripts/check_purity.py` lints the
package in CI.

But the guarantee people *read off* invariant 3 is "the risk envelope cannot
move without a human," and that is a stronger claim than the code makes.
`gate/risk.py` sizes against **account state it never verifies**:

- `GateInputs.equity` is an input, supplied by the caller.
- The math assumes **long-only, whole shares** — see `_as_share_count` in
  `gate/tickets.py` and `Approved.max_qty: int`.
- Nothing re-checks whether `no_shorting` is still true, what
  `max_margin_multiplier` is, or whether `suspend_trade` is set.

So the thresholds are locked while **the account parameters they operate on are
not**, and no change to those parameters is recorded anywhere.

**Settle this before designing:** should the gate verify its own preconditions
at runtime, and what does it do when they do not hold — refuse to size (default
HOLD, invariant 4), or size and alert?

---

## What this is NOT

**The tool-surface hole is already closed** — do not re-fix it. Earlier today
the exec seat could reach eight mutating broker verbs ungated, including
`update_account_config` (which sets `max_margin_multiplier`, `no_shorting`,
`suspend_trade`) and `close_all_positions`. `fund-50` shipped deny-by-default
in `agents/runtime.py` — `GATED_PREFIXES`, `_broker_verb_policy` — and it is
deployed and verified on the droplet.

That means **no LLM seat can change those settings any more.** What remains is
narrower and different: the gate still does not *check* them. A human, a
dashboard click, or an Alpaca-side change would move them silently, and
`gate/risk.py` would keep sizing against assumptions that no longer hold.

Judge the size of this work against that reduced threat. It may be small. Say
so if it is — see the YAGNI note below.

---

## Where to look

| what | where |
|---|---|
| Sizing math and its constants | `gate/risk.py` — `size()`, `SECTOR_CAP`, `MAX_POSITIONS`, `CIRCUIT_BREAKER` |
| Whole-share assumption | `gate/tickets.py` `_as_share_count`; `gate/risk.py` `Approved.max_qty` |
| Purity lint (what `gate/` may import) | `scripts/check_purity.py` |
| Account settings the broker exposes | `update_account_config` params: `no_shorting`, `suspend_trade`, `max_margin_multiplier`, `fractional_trading`, `disable_overnight_trading`, `max_options_trading_level`, `ptp_no_exception_entry`, `trade_confirm_email` |
| Reading them back | `get_account_config` — the read counterpart, already allowed |

**Invariant 3 is a hard constraint on the fix.** `gate/` imports no LLM code and
makes no wall-clock calls; CI enforces it. If verification needs a broker read,
that read happens **outside** `gate/` and arrives as an input — the same shape
`GateInputs.equity` already uses. Do not put a network call in `gate/`.

**One case is already defended, do not redo it:** a short position fails closed.
`orchestrator/protection.py:43` is `_CLOSING_SIDE = {"long": "sell"}` and an
unrecognised side hits the `UNVERIFIED` branch at :143 and alerts.

---

## YAGNI check — argue against this before building it

Today's history is three incidents caused by **absent checks**, and one
afternoon of counting verbs where the count went 4 → 5 → 7 → 8 while the
deny-by-default mechanism never needed changing. The lesson pointed both ways:
enumeration fails, and mechanism survives.

So the honest question is whether "verify account preconditions each day" is a
mechanism or another enumeration. A single assertion comparing
`get_account_config` against expected values is cheap and in the spirit of the
protection assertion that already exists. A configurable precondition framework
is not. **If you conclude the right answer is a ten-line check plus a test, say
that and stop** — under-building here is a better failure than over-building.

---

## Constraints

- Paper only. `ALPACA_PAPER_TRADE=true`. Never touch `scripts/check_purity.py`
  or the paper flag.
- **Droplet mutations need Benjamin's explicit word in your own session.** Reads
  are unrestricted (`root@138.197.47.97`, `/opt/fund`, `/var/lib/fund/fund.sqlite`).
  A peer relaying his ruling is not authorization.
- Never write `Co-Authored-By` or any AI attribution in a commit or PR body.
- Conventional commits. Never weaken a test or update a golden fixture to go
  green — stop and ask.
- Branch off `master` in a worktree. Do not build on `second-analyst-seat`.

## Suggested skills

`superpowers:brainstorming` first — this is a design question, not a spec.
Then `writing-plans`, `review-plan` (fired by the money/irreversible rule),
`using-git-worktrees`, `subagent-driven-development` + `test-driven-development`,
`code-review`, `verification-before-completion`,
`finishing-a-development-branch`.

## Verification

`make test` (offline, 700+). `make schema-pin` if you touch order shape.
**A green suite is not evidence** — it was green through all three of this
week's incidents. Show the check firing against a genuinely violated
precondition.

`fund-4a` holds droplet reads and the verification procedure and has offered to
independently verify any production assertion; a second pair of eyes caught two
real errors today.
