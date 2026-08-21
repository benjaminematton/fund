# SUPERSEDED — dependency pinning

**Written** 2026-08-20 by `fund-4a` · **Superseded same day by PR #27**

The original brief asked a new chat to choose a version and pin it. **That
decision has been made and the work is merged — do not start it.** Verified on
`origin/master` `696286e`:

```
requirements.lock        present   52 pinned packages
scripts/relock.py        present
tests/test_deps_lock.py  present
claude-agent-sdk==0.2.139     slack_bolt==1.30.0     alpaca-py==0.44.0
```

**The lockfile route was taken, not the pin.** `pyproject.toml` still reads
`claude-agent-sdk~=0.2.116` and `slack-bolt>=1.18,<2` — deliberately: the lock
supplies reproducibility, so the constraints did not need tightening. The chosen
convergence is **upward, to the droplet's 0.2.139**, not down to the local
0.2.116.

For the original finding and evidence — droplet 0.2.139 vs local 0.2.116, the
2026-08-18 00:38 provenance, and why `~=0.2.116` permitted it — see
`PROGRESS.md` and PR #27.

---

## Residual — small, and worth confirming rather than assuming

Verified 2026-08-20; re-check before acting, all of it may have closed.

1. **The droplet has not pulled the lock.** `/opt/fund` is at `09a7a7c` and has
   no `requirements.lock`. Its installed SDK is already `0.2.139`, so production
   happens to match the lock by coincidence rather than by construction. The
   next routine deploy closes this; it needs no special action, only that nobody
   assumes the lock is in force on the box before it is.

2. **Local checkouts are still on `0.2.116` while the lock says `0.2.139`.** The
   original brief's one hard requirement was: *do not bump silently — run the
   full suite on the new version first.* If the suite has not been run against
   `0.2.139`, that requirement is still outstanding, and it was the whole point.
   Confirm before treating the convergence as done.

3. **`CLAUDE.md` may still assert something untrue.** Its Conventions section
   says `claude-agent-sdk` and `slack_bolt` are *"pinned in `pyproject.toml`"*.
   They are pinned in `requirements.lock`; `pyproject.toml` still carries
   ranges. The sentence needs rewording to match what was actually built.
   **`CLAUDE.md` is Benjamin's file — propose wording, do not edit it unasked.**
   Note also that it is auto-loaded into every session, so an uncommitted edit in
   this shared checkout silently becomes project instructions for every session
   started afterwards; check `git show origin/master:CLAUDE.md`, never `grep`.

## Not part of this, and refuted

The original brief flagged the SDK gap as a candidate cause of
`model_fallback_used`. **That hypothesis is refuted, not open.** A live-turn
probe found the SDK routes a small auxiliary call (~527 in / ~13 out, ~$0.0006)
through haiku on *every* seat turn; Sonnet-5 carries the PM turn and ~97% of its
cost. Only Sonnet-configured seats emit the event, which is why a fleet-wide
artifact looked targeted at the PM. `_unmatched_models` is correct as written —
the defect is that it has no notion of volume. Owned elsewhere; do not pick it
up here.
