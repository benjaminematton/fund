---
paths:
  - state/
  - tests/test_state*.py
---
# state — standing

SQLite DDL + helpers; SQLite is the source of truth — Slack is never where a
decision comes from. Every workflow table is a state machine: apply
transitions only through `state/transition()`; allowed transitions defined in
`specs/contracts.md` (do not invent fields); an illegal transition must raise,
never overwrite. Journals are written only through `state/journal.py`.
Outbound delivery goes through the `events` outbox so a crash or retry can
neither lose nor duplicate a post. Tests: `tests/test_state*.py`.

# Journal

## 2026-08-31 · #205 · fund-e2
- `CREATE TABLE IF NOT EXISTS` no-ops on an existing table, and
  `migrations.py` can only express `ALTER TABLE … ADD COLUMN` — a `CHECK`
  added to an existing table never reaches production, and `make preflight`
  (compares table/column *names* only, not types/CHECK/UNIQUE) stays green
  regardless. Tracked in #154; `weights.weight`'s CHECK survives only
  because the table is new.
- `NO_SCHEMA_HOME` (`test_schema_contract.py`) runs backwards from
  intuition: listing a spec table there means it's *skipped* from the
  schema comparison — removing it is what turns the check on.
- The DDL-diff tokenizer (`_tokenize`, not the separate `_SQL_COMMENT`
  regex) strips `--`/`/* */` comments before building the `Column`/`Table`
  structs `test_schema_matches_spec` compares, so a column's `schema.sql`
  comment and its spec twin are hand-matched, not enforced. `coverage` and
  `abstention_rate` both rely on that today.

## 2026-09-25 · #206 #238 #140 · fund-fe (overseer)
- `StrategySpec.predicted` (PR #249) requires exactly `{net_sharpe, max_dd, hit_rate}`, each
  a finite non-bool number, **checked never coerced** — it is `spec_id` hash input, so an int
  `1` rewritten as `1.0` would move every id. The pre-existing frozen id pin kept its literal
  by restoring `predicted={}` on the dump before hashing; a second pin recorded on the bare
  dict model guards `predicted`'s own serialisation.
- `state/transition.py` (PR #254) has the `strategies` machine: §4's 13 edges verbatim,
  `STATE_COLUMN["strategies"] = "state"`, `VERSIONED` tables require keyword-only
  `expected_state_version` (refused on every other table) and bump it in the same UPDATE.
  `advance_to_backtest` is a 5-line wrapper. No `stale_transition` event kind exists — adding
  one is a `contracts.md` human commit and forces a slackkit renderer — so a lost CAS is a
  tool error (`StaleTransition` OR `IllegalTransition`, both caught in `handle_run_backtest`).
  `specs/strategy-contracts.md:25` still says `EDGES` carries no strategies entry — now
  false; canonical, awaiting a human commit.
- `state/protection.py:qty_of` (PR #248) returns `None` for non-finite values; before, a
  `nan` qty raised out of `dev_status` and broke its EXIT-0 contract.
