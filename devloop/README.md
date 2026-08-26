# devloop — unattended build loop (dev tooling, not fund runtime)

Ralph-style loop that builds Phase 1 against `specs/acceptance.md`. Nothing here
is imported by fund code; `scripts/check_purity.py` does not scan it.

## Anatomy → this repo

| Loop part            | Implementation                                              |
| -------------------- | ----------------------------------------------------------- |
| Goal contract        | `specs/acceptance.md` checkboxes (non-`@live`, per phase)   |
| Work discovery       | worker picks the most important unchecked item (PROMPT.md)  |
| Action               | fresh `claude -p` per iteration, one item per pass          |
| Verification         | `check.sh` = tamper guard + `make test`, then a second-agent invariant review (REVIEWER.md) before commit |
| State                | acceptance checkboxes, git commits, `devloop/NOTES.md`      |
| Stopping             | plan complete / 3 consecutive reds / iter cap / cost cap    |

## Run

```bash
git add devloop && git commit -m "devloop harness"   # once; loop needs a clean tree
./devloop/loop.sh                                    # knobs: MAX_ITER MAX_COST_USD FAIL_LIMIT PHASE
```

Red iterations are discarded (`git reset --hard` + clean, `devloop/` excluded),
so every retry starts from the last green commit. Rerunning resumes from the
checkboxes — there is no other loop state to manage.

## What the loop cannot touch (tamper guard)

Any change → rejected: `specs/` (except ticking `- [ ]`→`- [x]` in
acceptance.md), `fixtures/`, `charters/`, `scripts/`, `research/`, `fundbt/`,
`stratgate/`, `calibration/`, `devloop/`, `.github/`, `evals/seats/`,
`CLAUDE.md`, `KICKOFF.md`, `Makefile`, `pyproject.toml`, `.gitignore`,
`evals/traces/recorded-expected.json`, and the root pytest configuration —
`pytest.ini`, `setup.cfg`, `tox.ini`, `conftest.py`. None of those four exists
in the repo, which is the point: the guard reads a creation as a change to a
protected path. pytest takes its ini options from exactly one file, and the
order is `pytest.ini` > `pyproject.toml` > `tox.ini` > `setup.cfg` — so of the
three ini spellings only a root `pytest.ini`, even a bare `[pytest]`,
displaces `pyproject.toml`'s `[tool.pytest.ini_options]` wholesale.
`setup.cfg` and `tox.ini` lose to it today and are guarded as defence in
depth: they are inert only while `pyproject.toml` keeps that table, so
guarding them is what stops the attack moving sideways to another filename.
What the displacement costs first is not coverage but the filter: `addopts = -m
'not live and not eval'` stops applying, so `tests/test_live_smoke.py` (a real
Alpaca paper round trip, account state, surface and schema pins), the live
canary in `tests/test_markers.py` and the `eval`-marked PM suite in
`tests/test_evals_live.py` all join the default run. The damage is graded.
`make test` goes red first and unconditionally: the canary raises
`AssertionError` whenever it runs, keys or no keys. Real network is
unconditional too — the two pin tests carry no credential guard and spawn the
broker MCP server over `uvx`. Only the money is conditional: the order-placing
and account-reading smokes skip unless the Alpaca keys (plus `ANTHROPIC_API_KEY`
for the two that place) are in the environment, so a keyless machine spends
nothing. The scoped `filterwarnings` (`error::DeprecationWarning:fund.*`) is
lost on the same line. Shrinking the suite is possible too, but it takes a
deliberate line: an `--ignore-glob` in that file, or `collect_ignore_glob` in a
root `conftest.py`.

Tests, eval cases (`evals/cases/`) and traces (`evals/traces/`) existing at
the baseline commit (`devloop/.baseline`) are immutable; new ones are welcome,
so a run may still append a whole new label under `evals/traces/`. The whole
trace directory is guarded, not just `recorded/`. `recorded/` because
`tests/test_evals_recorded.py` regrades those files and asserts the result
equals `evals/traces/recorded-expected.json`, so editing the inputs restores
equality just as well as editing the expectation. Every other committed run
label — eleven of them — because they are archived measurement evidence,
what `make eval-report RUN=… BASELINE=…` diffs a new run against. Which label
is the current comparison point is recorded per seat, in whichever
`evals/seats/*.yaml` records one: today only `evals/seats/pm.yaml` does, naming
`postfix3`, while `evals/seats/critic.yaml` names no baseline. Not here; it
moves as charters change, and a superseded label is still history worth
keeping. No test reads them; you do. Editing one moves the comparison
point rather than the result.

`evals/seats/` is frozen against additions too, which makes switching on a new
invariant (its `invariants:` list) a human-commit step, alongside the measured
I5 ceilings that sit in the same file. That is intended.

A rename is not a way around any of this: the guard diffs with `--no-renames`,
so `git mv specs/design.md attic/` reads as a deletion of a protected path.
Nor is a typechange: replacing a baseline file with a symlink empties it
without editing a line, and the baseline rule rejects every status but an
addition rather than a named list of them.

If the worker needs a protected change (e.g. a new dependency in
`pyproject.toml`, a fix to a `fixtures/` vector), it stops and writes the
request to `devloop/NOTES.md`. Make the edit yourself, commit, rerun — a
protected path is rejected whatever the baseline says, so there is no
`.baseline` step for one.

Delete `.baseline` only after committing a change to the baseline-guarded set
— `tests/`, `evals/cases/`, `evals/traces/`. It re-pins the baseline to the new
HEAD, which is what makes a test or eval case you just added immutable for the
next run; leave it and the loop may edit that new file freely.

## When it halts

Read `devloop/logs/iter-N-*.{json,log}` and `devloop/NOTES.md`. Typical causes:
a genuinely ambiguous spec item (fix the spec, human commit), the same item
failing repeatedly (do that one interactively), or reviewer blocks (reasons are
in `iter-N-review.json`).

`@live` items are always yours to run manually. So is reading the diffs: the
gate proves the tests pass; it does not prove the code is what you meant.
