# Alert identity and the alert → issue filer

Status: design, awaiting review (2026-08-24)

Gives every alert a stable machine identity, then files an unmatched alert as a GitHub issue
so it becomes work you can see the next morning.

This is **devops tooling** (`docs/agents/devops.md`). It adds no stage, seat, table, MCP tool,
charter or gate threshold, and it changes no trading behaviour: every production edit is at the
alert-emission layer, and the filer runs outside the trading job entirely.

---

## 0. Why

Detection already works. On 2026-08-21 an unprotected-position alert was raised at 13:38:02Z,
projected to Slack the same second, and the run exited non-zero. It sat because **nothing turned
it into a tracked item**. The same gap explains why #17, #18, #26 and #32 were each independently
re-discovered by four different sessions that day.

It is not hypothetical and it did not stop. The same condition fired again on **2026-08-24**
(`events` id 65) and the run failed a second consecutive trading day.

The broken link is the first arrow of the loop in `docs/agents/devops.md`:

```
alert fires  →  becomes a GitHub issue  →  standup shows it  →  work  →  closed
```

## 1. Why it cannot simply read the alerts that already exist

The obvious implementation — read `events` where `kind='alert'`, call `gh issue create`, dedupe by
label — has no field to key the label on. Every one of the ~25 emission sites writes
`{"text": <interpolated free text>}` and nothing else identifying. Real rows:

```
"pm_timeout AAPL — defaulted to hold"            three alerts, one root cause,
"pm_timeout MSFT — defaulted to hold"            one second apart      → 3 issues
"pm_timeout NVDA — defaulted to hold"

"ticket c0a9ae97 open after exec turn — no order"   id changes every run → 1 issue per run

2026-08-21  "NVDA 80 was ticketed with a stop at 215.0 but the broker covers only 40 of 80 shares…"
2026-08-24  "NVDA 40 was ticketed with a stop at 215.0 but the broker has NO live protective order…"
                                                    same condition, text changed → 2 issues
```

Text-keyed dedupe files duplicates on day one. Deriving a key by parsing that text is forbidden
outright by `CLAUDE.md`: *"Do not parse tickers, actions, or sizes out of free text anywhere."*

So the identity has to be minted where the alert is raised. That is the whole reason this design
is larger than "a small step in an existing path".

### Prior art in this repo, which this design follows

`orchestrator/protection.py::assert_positions_accounted` already solves the same problem one layer
down. It keys a finding on `(symbol, recorded, held, cleared)`, stays silent while a finding is
standing, and emits an explicit **clearing** alert when it resolves. This design generalises that
shape rather than inventing one.

It also proves the filer cannot lean on source-side suppression: `assert_positions_protected`, in
the same module, has no such suppression and re-fires every run. **Some sites self-suppress and
some do not**, so dedupe must live in the filer, keyed on identity, independent of the raiser.

## 2. Non-goals

- **Not a second detector.** It reads alerts the software already raised. It never decides whether
  a condition is true. `docs/agents/devops.md`: *do not build a second checker that can disagree
  with the first.*
- **Not a severity system.** `events` has no severity and this does not add one. Every alert code
  files. A filter here would be a second opinion about importance that can disagree with the code
  that raised the alert. If a code proves noisy, the fix is to stop raising it.
- **Not a cron job.** Attended, per `2026-08-21-day-bookends-design.md` §1. It also cannot run on
  the droplet: `gh` is not installed there, and adding a network dependency to the trading path
  would be wrong even if it were.
- **Not an escalation channel.** Slack alerting already works and is untouched.
- **Nothing in the fund's own loop.** No calibration, scoreboards, charters or strategy gates.

## 3. Components

| Artifact | Location | New? |
|---|---|---|
| `append_alert()` | `slackkit/outbox.py` | new, beside `append_event` |
| alert codes | the ~25 emission sites | new argument at each |
| direct-`append_event`-alert lint | `scripts/check_alert_codes.py`, run in `make test` | new |
| the filer | `scripts/file_alert_issues.py` | new |

### 3.1 `append_alert` — identity at the raise site

```python
def append_alert(conn, code: str, text: str, *, ticker: str | None = None,
                 clears: bool = False, now_iso: str, **payload) -> int:
```

Writes an ordinary `kind='alert'` event whose payload carries `code`, optionally `ticker`,
optionally `clears`, plus whatever structured payload the site already passed. `code` is a
required positional: a site physically cannot emit an alert without one.

Three sites already ship structured payloads (`tickers`, `drift`, `accounting`) and keep them
unchanged — `**payload` passes them through.

It lands beside `append_event` in `slackkit/outbox.py`, which every emission site already imports,
so no new dependency edge is created and `scripts/check_purity.py` is unaffected.

`scripts/run_day.py`'s existing private `_alert` helper is absorbed into it rather than left as a
second way to do the same thing.

### 3.2 The lint — why convention is not enough

A `code` argument that sites are merely *expected* to pass is a convention, and site 26 forgets it.
The alert would still fire, still reach Slack, and be silently invisible to the filer forever — a
signal that changes meaning without changing appearance, the failure shape this repo has been bitten
by repeatedly.

`scripts/check_alert_codes.py` is an AST lint in the style of `scripts/check_purity.py`: it fails if
any `append_event(...)` call anywhere passes the literal `"alert"` as its `kind`. It runs in
`make test`, so the invariant is tested rather than remembered.

### 3.3 The codes

**Twenty-two codes are minted across the 26 emission sites; twenty-one of them file.** Five
`reconcile.py` sites share one code, and `audit_failed` is minted but never files (see below). Most
promote a token already sitting at the front of the text.

Line numbers below are the sites as they stood on `4685579`, before the `protection_unverified`
split added a closure; read the parenthetical, not the number, to identify a row.

| Site (on `4685579`) | Code | `ticker` |
|---|---|---|
| `orchestrator/daily.py:139` | `gate_error` | yes |
| `orchestrator/daily.py:216` | `pm_timeout` | no |
| `orchestrator/daily.py:337` | `ticket_open_after_exec` | no |
| `orchestrator/preconditions.py:56` | `account_precondition_drift` | no |
| `orchestrator/protection.py:199` (no broker wired / positions unreadable / orders unreadable / re-read failed) | `protection_unverified` | no |
| `orchestrator/protection.py:255` (per-position exposure) | `unprotected_position` | yes |
| `orchestrator/protection.py:353` | `accounting_shortfall` | yes |
| `orchestrator/protection.py:360` | `accounting_unverified` | no |
| `orchestrator/reconcile.py:77` | `fill_on_unapproved_decision` | no |
| `orchestrator/reconcile.py:90` | `partial_fill_manual_review` | no |
| `orchestrator/reconcile.py:149,156,161,169,176` | `order_unreconciled` | no |
| `orchestrator/reconcile.py:193` | `order_unfilled_at_cap` | no |
| `orchestrator/reconcile.py:207` | `order_partial_then_dead` | no |
| `orchestrator/reconcile.py:268` | `order_unresolved_at_cap` | no |
| `scripts/run_day.py:282` | `seat_turn_failed` | no |
| `scripts/run_day.py:297` | `exec_turn_violation` | no |
| `scripts/run_day.py:366` | `missing_price_history` | no |
| `scripts/run_day.py:383` | `unmapped_sector` | no |
| `scripts/run_day.py:458` | `run_day_failed` | no |
| `agents/runtime.py:114` | `order_gate_denied` | no |
| `agents/runtime.py:387` | `cost_unavailable` | no |

**Keying rules, which are what keep the issue count bounded:**

- `ticker` is a ticker symbol or absent. **Never** an order id, quantity, seat, timestamp or
  exception type. Those are unbounded and would file an issue per run.
- `ticker` is set only where the condition is genuinely per-position — where fixing NVDA does not
  fix MSFT. `pm_timeout` fires per ticker but has one root cause (the PM did not answer), so it
  carries no ticker and files one issue, not three.
- The five `reconcile.py` sites sharing one `stale.format(...)` template share one code. The five
  distinct reasons stay in the text, where a human reads them.
- A multi-ticker alert (`missing_price_history`, `unmapped_sector`) carries no `ticker`; its list
  is already in the existing `tickers` payload.

Upper bound on simultaneously-open issues: 18 unkeyed codes (the split added `protection_unverified`,
unkeyed by construction — no code path that mints it also knows which position, if any, is at
fault), plus 3 ticker-keyed codes (`gate_error`, `unprotected_position`, `accounting_shortfall`)
times a 3-name watchlist — **27**. Without the keying rules, `ticket_open_after_exec` alone would
file one issue per trading day forever.

**`audit_failed` (`run_day.py:428`) is deliberately absent and never files.** It is a rollup that
restates the day's other alerts — filing it would double every issue. It already carries the
`audit_report` marker that `scripts/audit_day.py` uses to avoid poisoning itself; the filer imports
`audit_day.SELF_ALERT_KEY` and skips on the same marker rather than inventing a second one.

### 3.4 `scripts/file_alert_issues.py`

Follows `scripts/audit_day.py`'s precedent exactly: stdlib only, argv-driven, so it runs against a
live or backed-up DB with nothing installed.

```
python3 scripts/file_alert_issues.py <db> --since YYYY-MM-DD          # dry run: prints what it would file
python3 scripts/file_alert_issues.py <db> --since YYYY-MM-DD --apply  # files
```

**Dry run is the default.** Filing is the only irreversible act here — an issue can be closed but
not un-filed — so producing issues requires an explicit `--apply`.

**It scans a date range, not a day.** Run on Monday it backfills Friday. Missing a day delays the
issue; it never loses it. `--since` is an ET calendar date resolved against `events.created_at` the
same way `audit_day.py` does it, importing `audit_day` rather than duplicating the window helper.

**Per alert, in order:**

1. Skip if the payload carries `audit_day.SELF_ALERT_KEY`.
2. Report and skip if the payload has no `code` (a malformed row is surfaced, never guessed at).
3. Build labels: `alert:<code>`, plus `ticker:<SYM>` when `ticker` is present.
4. `gh issue list --state open --label …` for that exact label set (`--label` repeated per label;
   `gh` requires an issue to carry **all** of them to match).
5. Alerts are grouped by their label tuple, and a clearing alert never contributes text. If the
   group has **only** clearing alerts: comment on the matching open issue if there is one, otherwise
   do nothing at all. Never file an issue to announce that something resolved.
6. If the group has non-clearing text and an open issue matches: do nothing.
7. Otherwise ensure each label exists, then `gh issue create`. **A condition that both fired and
   cleared inside the window still files** — the symptom clearing is not the defect being fixed
   (§3.5), and the body records that it later cleared.

**Labels must be created, not assumed.** `gh issue create --label` errors on a label the repo does
not have, and all 8 currently-open issues carry zero labels — there is no scheme to inherit.

**Only open issues are matched.** A condition that recurs after its issue was closed files a fresh
one, carrying none of the closed issue's history. That is intended, and it mirrors the
clear-and-recur semantics `assert_positions_accounted` already has.

**Issue body** carries the code, the ticker if any, every distinct alert text seen in the window
with its `created_at`, the occurrence count, and which DB file it was read from. The title is the
first alert text, truncated.

**The `gh` invocation is injected**, in the same spirit as the `Clock` protocol, so tests exercise
the whole path against a fake with no network and no tracker writes.

### 3.5 Closing is a human act

The filer **never closes an issue.** A clearing alert adds a comment and nothing more.

The reason is the field brief's symptom/cause distinction. If a stop is placed on NVDA by hand, the
`unprotected_position` symptom clears — but *"no code path will protect it"* is still true and still
needs work. Auto-closing on the clearing alert would erase precisely the tracked item this design
exists to create, and the recurrence next week would arrive with no history.

## 4. Data flow

```
raise site ──append_alert(code, text, ticker=…)──> events row (payload carries code)
                                                          │
                                                          │  read, attended
                                                          ▼
                                        scripts/file_alert_issues.py --since
                                                          │
                              ┌───────────────────────────┼──────────────────────┐
                       clears=True                  open issue exists        neither
                              │                            │                     │
                          comment                     do nothing          gh issue create
                                                                        (labels ensured first)
```

## 5. Error handling

Default is **report, never act** — the tooling twin of invariant 4.

| Failure | Behaviour |
|---|---|
| `gh` missing or unauthenticated | render as a finding, exit non-zero, file nothing |
| `gh issue list` fails for one alert | that alert is reported unfiled; the remaining alerts still process |
| `gh issue create` fails | reported; no retry, no second attempt at a new id |
| alert payload has no `code` | reported as malformed; never guessed at from text |
| DB unreadable | exit non-zero having filed nothing |
| no alerts in the window | print so, exit 0 |

Dry run exits 0 whether or not it found work; only errors are non-zero.

**Accepted limitation:** two concurrent `--apply` runs could both pass the `gh issue list` check and
double-file. The tool is attended and single-operator, so this is accepted rather than locked
against.

## 6. Testing

Runs in `make test` — offline, no network, no keys, `gh` faked throughout.

**Every check's negative control must fail.** This repo has three documented instances of a test
whose negative control also passed, two caught by luck.

Required cases:

- The two real NVDA texts from 2026-08-21 and 2026-08-24, verbatim, file **one** issue. Negative
  control: with dedupe disabled they file two.
- `pm_timeout` on three tickers files one issue.
- `ticket_open_after_exec` with a different order id on two days files one issue.
- An alert whose labels match an open issue files nothing.
- An alert whose labels match a **closed** issue files a new one.
- `clears=True` with a matching open issue comments and does not close; with no matching issue it
  does nothing at all.
- An `audit_report`-marked alert never files.
- A payload with no `code` is reported, and does not abort the remaining alerts.
- Dry run performs no `gh` mutation whatsoever — asserted against the fake, not assumed.
- A label that does not exist is created before the issue that needs it.
- The lint fails on a planted `append_event(conn, "alert", …)` call and passes on the real tree.

## 7. Verification beyond the suite

A green suite cannot show the dedupe key survives real interpolated text, so completion requires a
dry run against `backups-from-vm/fund-2026-08-20.sqlite` and a copy of the droplet DB, printing the
exact issues it would file — showing the NVDA condition present on both 08-21 and 08-24 resolving to
one issue.

## 8. Out of scope, recorded because they were found

- **`ops/fund-daily.service`'s heartbeat pings only on success**, so watchdog silence cannot
  distinguish a dead box from a failed run. Fix agreed separately (`ExecStopPost` carrying
  `${EXIT_STATUS}`); it ships on its own branch because it lands by droplet deploy, not by merge.
- **The droplet is 25 commits behind `origin/master`** and has never run PR #31's account-precondition
  drift check.
- **NVDA holds 40 shares with no protective order** going into 2026-08-25's open. Benjamin's
  decision alone; recorded here, not acted on.
