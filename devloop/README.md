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
`stratgate/`, `calibration/`, `devloop/`, `.github/`, `CLAUDE.md`, `KICKOFF.md`,
`Makefile`, `pyproject.toml`. Tests existing at the baseline commit
(`devloop/.baseline`) are immutable; new tests are welcome.

If the worker needs a protected change (e.g. a new dependency in
`pyproject.toml`), it stops and writes the request to `devloop/NOTES.md`. Make
the edit yourself, commit, delete `.baseline` if tests/fixtures legitimately
changed, rerun.

## When it halts

Read `devloop/logs/iter-N-*.{json,log}` and `devloop/NOTES.md`. Typical causes:
a genuinely ambiguous spec item (fix the spec, human commit), the same item
failing repeatedly (do that one interactively), or reviewer blocks (reasons are
in `iter-N-review.json`).

`@live` items are always yours to run manually. So is reading the diffs: the
gate proves the tests pass; it does not prove the code is what you meant.
