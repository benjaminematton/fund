# Agent self-verification — tests, typecheck, and a "browser" for a system with no UI

**Date:** 2026-10-13 · **Status:** approved design, not canonical. Defers to
`CLAUDE.md` (invariants, Commands, Test invariants) and `docs/agents/devops.md`.
Where this document edits a canonical file, that edit is listed under
"Canonical edits" and lands by human commit.

Markers: `[M]` measured on this tree on 2026-10-13 · `[R]` read in this repo or
in the Claude Code docs · `[I]` inference, labelled.

## Problem

A coding agent working this repo has three ways to check its own work, and
they are uneven:

| Leg | State today | Measured `[M]` |
|---|---|---|
| Tests | built, tiered, pre-allowed, **unenforced** | `make test`: 1550 passed, 24 s |
| Typecheck | absent | pyright standard mode: 114 errors in 103 files, 2 s |
| "Browser" | sim-day exists but is assertion-only | 8 scenarios, 0.7 s, no readable output |

Two findings matter more than the numbers:

1. **Project settings never reach the fleet.** `.gitignore` line 44 says
   settings belong in `.claude/settings.json` "reviewed like code", but that
   file is untracked, and 0 of the 23 worktrees under `fund-wt/` have it `[M]`.
   Any hook added there governs only the main checkout. Lanes, where most code
   is written, get nothing.
2. **An external gate exists for one caller only.** `devloop/check.sh` is
   "external verification, never the agent's own claim": tamper guard, then
   `make test`, then a second-agent review. It runs inside the Ralph loop
   only. Interactive sessions get `verification-before-completion` as prose,
   which is skippable.

Evidence that the typecheck leg finds real defects, not noise: pyright flags
`orchestrator/reconcile.py:213`, where `cur` is subscripted unguarded inside
the partial-then-dead alert. `cur` is bound at line 169 from a SELECT and line
171 explicitly handles it being `None`, so the author treated a missing order
row as reachable `[R]`. Missing row plus partial fill is a latent `TypeError`
in reconciliation `[I: reachability reasoned from line 171, not demonstrated]`.

## Non-goals

- No change to what the trading seats can do. Nothing here touches
  `agents/runtime.py` hooks, toolsets, or the gate.
- No second-agent review on every commit (approach C). It builds on the hook
  plumbing below and is a later decision.
- No new `make check` target. `make test` stays the one name CLAUDE.md,
  the permission allowlist, and humans already use.
- Not fixing the 114 pyright errors up front. The ratchet admits the baseline
  and pays it down per package.

## Piece 1 — Typecheck ratchet inside `make lint`

**What.** `lint` gains a third step after purity and alert-codes:
`scripts/check_typing.py`. It runs pyright in standard mode over the source
packages, compares per-file error counts to a committed baseline, and fails
on growth.

- **Scope:** `agents orchestrator gate state stratgate calibration fundbt
  scripts evals market slackkit devcheck`. Not `tests/`, not `devloop/`.
  Configured as `[tool.pyright]` in `pyproject.toml` with
  `typeCheckingMode = "standard"`.
- **Interpreter:** the checker passes its own `sys.executable` to pyright as
  the Python path, so the same invocation resolves packages on a laptop
  (`.venv`), in CI (global pip), and on the droplet.
- **Baseline:** `scripts/typing_baseline.json`, a map of file path to error
  count, committed. The checker fails if any file's current count exceeds its
  baseline or any file absent from the baseline has errors. It prints the
  offending diagnostics. If every count is at or below baseline and at least
  one is below, it exits 0 and prints the lower counts with "baseline can
  shrink: commit the regenerated file". `--write-baseline` regenerates it.
- **Only down, by human commit.** The checker cannot see history, so the
  direction rule is a review rule, backed by two mechanisms: the file is
  added to `devloop/tamper_guard.py`'s `PROTECTED_FILES`, so the unattended
  loop cannot raise it, and CI runs the checker so a raised baseline is
  visible in the PR diff.
- **Pin:** `pyright[nodejs]` in the `dev` extra of `pyproject.toml`,
  upper-bounded at the next major, exact version taken by `make deps-relock`
  into `requirements.lock`. `scripts/relock.py` already includes the `dev`
  extra `[R: line 54]`. The `nodejs` extra exists so no host downloads node at
  first run; confirm the extra name against the pyright release notes when
  pinning.
- **CI:** `.github/workflows/ci.yml` gains two steps, `check_alert_codes.py`
  and `check_typing.py`. CI today runs purity and pytest only, so
  `check_alert_codes.py` never runs there `[R]`. This is an existing drift
  between `make lint` and CI and is fixed by the same commit.

**Pay-down order after the baseline lands** (each its own commit, with a test
where the fix changes behaviour): `gate/` (2 errors), `orchestrator/` (4,
including the reconcile None path), then `fundbt/`, `evals/`, `scripts/`.
`market/` (37) and `slackkit/` (17) are mostly SDK-typing arguments and wait.

**Acceptance.**
- `make test` runs the checker and stays green on the committed baseline.
- Adding one new type error to any in-scope file makes `make test` red with
  the diagnostic in the output.
- Lowering one baseline count below the current error count makes it red.
- A file at a lower count than baseline prints the shrink message and exits 0.
- CI runs the checker and the alert-codes lint.
- `make test` wall time grows by no more than 5 s `[I: measured 2 s standalone]`.

## Piece 2 — Enforcement: two hooks on a committed settings file

Hooks are Claude Code harness hooks, registered in `.claude/settings.json`,
which this piece **commits**. They are a different mechanism from the SDK
seat hooks that live only in `agents/runtime.py`; see "Canonical edits".

### The shared primitive: tree id and the green marker

- **Tree id:** a content hash of the working tree including untracked,
  non-ignored files. Computed in the target checkout as
  `GIT_INDEX_FILE=<tmp> git add -A . && git write-tree` against an empty
  temporary index, then the tmp index is deleted. The real index is never
  touched.
- **Green marker:** `make test` writes the tree id to
  `$(git rev-parse --git-dir)/fund-green-tree` on success, via
  `scripts/hooks/mark_green.py` appended to the `test` recipe. The git dir is
  per-worktree, so each lane has its own marker. "Green" means "the full
  suite passed on exactly this tree".

### Commit gate — `PreToolUse`, matcher `Bash`

`scripts/hooks/commit_gate.py`, stdlib only, runs under any `python3` (no
3.12 syntax: a fresh worktree may have no venv yet).

1. Read stdin JSON. Split `tool_input.command` into segments on `;`, `&`,
   `|`, newline and parentheses, shell-split each segment, drop leading
   `NAME=value` tokens, and skip git global options. The gate applies when
   the git subcommand is `commit`, or the segment begins `gh pr create`. Otherwise exit 0 (pass through).
2. Take the target checkout from the stdin `cwd` field, never from
   `CLAUDE_PROJECT_DIR`, which stays pinned to the starting root after a
   session enters a worktree `[R: Claude Code docs]`.
3. Compute the tree id. If it equals the marker, exit 0.
4. Otherwise run `make test` in that cwd (which bootstraps the venv if
   needed). Green: the recipe wrote the marker; exit 0. Red: emit
   `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
   "permissionDecision": "deny", "permissionDecisionReason": <last 40 lines
   of output>}}` and exit 0, so the model sees what broke.
5. Hook timeout in settings: 300 s.

### Done gate — `Stop`

`scripts/hooks/stop_gate.py`, same constraints. It **never runs the suite**,
so turn ends stay fast.

1. Read stdin JSON. If `stop_hook_active` is true, exit 0: it says its piece
   once. One block rather than the harness cap of eight, because in the
   shared main checkout a dirty tree can be a peer's work and a red suite can
   be outside the lane.
2. "Dirty" means `git status --porcelain --untracked-files=no` is non-empty
   in `cwd`: tracked files modified. Untracked-only changes do not trigger
   this gate; the commit gate covers them through the tree id.
3. Clean tree, or tree id equals the marker: exit 0.
4. Dirty and stale: emit `{"decision": "block", "reason": "tracked files
   changed since the last green `make test` on this tree; run it and report
   the result"}`.

### The committed settings file

- `.claude/settings.json` is committed containing: the two hook
  registrations, and the existing `deny` and `ask` lists, which are repo
  policy. Hook commands are referenced as
  `python3 "$CLAUDE_PROJECT_DIR"/scripts/hooks/<name>.py`: the script copy
  comes from the starting root, the tree it acts on comes from stdin `cwd`.
- The current `allow` block, which carries absolute paths from one machine,
  moves to `.claude/settings.local.json` (already ignored).
- Hook commands are not subject to the permission allow/deny lists `[R]`.

### Reach and limits

- A session loads project hooks from its primary working directory, so a
  committed file is live in every lane checkout once the lane contains the
  commit `[R]`. Lanes branched earlier get it on rebase. Documented, not
  solved.
- A session that starts in a subdirectory would compute `cwd` there; the
  scripts call `git rev-parse --show-toplevel` on `cwd` first.
- `disableAllHooks` turns both off; there is no per-hook switch `[R]`.

**Acceptance** (tests against a temporary git repo; the suite command is
injected so tests never recurse into `make test`):
- Tree id is stable across two computations, changes when an untracked file
  is added, and does not change the real index.
- Commit gate: pass-through on `git status`; on `git commit` with a matching
  marker exits 0 without running the suite command; with a stale marker runs
  the suite command, exits 0 on success and writes the marker, emits the deny
  JSON with the output tail on failure.
- Stop gate: exits 0 on `stop_hook_active`, on a clean tree, and on a
  matching marker; emits the block JSON on a dirty stale tree; never invokes
  the suite command.
- `make test` on a green run leaves the marker equal to the current tree id.
- `.claude/settings.json` is tracked, parses, names both hooks, and contains
  no `allow` entry with an absolute path.

## Piece 3 — `make sim-report`, the agent's browser

sim-day proves the day with assertions; an agent changing the gate or a stage
cannot *see* the day it produced. The scenarios are deterministic (injected
clock, fixed ticket id), so a rendered transcript is diffable between master
and a branch. For a system whose UI is Slack and a database, that is what
"open it and look" means.

- **Helper move first.** `sim_day`, `SimResult`, and the per-scenario inputs
  move from `tests/test_sim_day.py` into `tests/simday.py`; the test module
  imports them. Scenarios are a dict of name to sim arguments:
  `golden`, `all_hold`, `mixed`, `gate_reject`, `circuit_breaker`, plus any
  further scenario the test file defines at move time, one entry each.
- **`scripts/sim_report.py`**, run as `make sim-report SCENARIO=golden`
  (default golden). Runs one scenario into a temporary directory and prints,
  in fixed order:
  1. the Slack transcript as `FakeSlack` recorded it, by channel, in posting
     order;
  2. the day's rows from `signals`, `decisions`, `tickets`, `orders`,
     `events`, `alerts`, in stable (primary key) order, as aligned text;
  3. the scorecard, by running `scripts/score_day.py <db> <run_date>` as a
     subprocess, unchanged; it already takes both on argv `[R]`, and the
     sim's `run_date` is on `SimResult`.
  Every line comes from the DB, the fake Slack, or the fake broker. Nothing
  is re-derived.
- **Not a golden fixture.** stdout only. The suite already pins the numbers;
  a committed report would be a second golden drifting against the first.
  The workflow is: run on master, run on the branch, diff in a scratch dir.
- An unknown scenario name exits non-zero listing the valid names.

**Acceptance.**
- `make sim-day` still passes after the helper move, with the same 8 tests.
- `make sim-report` on golden prints the golden ticket id and at least one
  post per channel the golden day posts to, and exits 0.
- Unknown scenario exits non-zero with the list.
- The script imports sim logic only from `tests/simday.py`; it defines no
  stage, gate, or replay behaviour of its own.

## Canonical edits (human commit)

- `CLAUDE.md` Conventions, one sentence after the hooks line: "Claude Code
  harness hooks (`.claude/settings.json`, committed) are a separate mechanism
  from these SDK seat hooks and never touch a seat."
- `CLAUDE.md` Commands: `make sim-report` added after `make sim-day`.
- `.gitignore` line 44 comment already states the policy; no change.

## Order and delivery

Three PRs, in this order, each independently mergeable:

1. Piece 2 (enforcement). Smallest diff, largest coverage gain, and it makes
   PRs 2 and 3 subject to itself.
2. Piece 1 (typecheck ratchet). Includes the CI drift fix.
3. Piece 3 (sim-report). Includes the helper move.

Pay-down commits for pyright errors follow PR 2 as separate small PRs.

## Open details for the plan

- Exact pyright version range and whether the `nodejs` extra is the right
  spelling for the pinned release.
