# Claims log: coordinating parallel AI coding agent sessions on one repo

Run date: 2026-08-24. Purpose: ground the `morning-standup` redesign (poll → board → lanes → dispatch).

Status rules: **verified** = 2+ independently read sources from different origins, or a direct local
test (a run outranks a document). **single-source** = one read origin. **inference** = derived by
combining sources. **prior-knowledge** = training or user-supplied, unconfirmed this run.

| Claim | Status | Source(s) |
|---|---|---|
| `claude agents --json [--cwd <path>]` returns a repo-scoped roster with `name`, `sessionId`, `cwd`, `kind`, `startedAt` | single-source (direct local run) | Direct run on this machine (4 fund rows returned); [agent view docs](https://code.claude.com/docs/en/agent-view) (read) |
| That roster **includes the calling session** | single-source (direct local run) | Direct run: own sessionId `b2d8014a…` returned as row `fund-7e`. No document consulted; re-runnable in one command |
| `ListAgents` **excludes** the calling session | single-source | [cross-session messaging docs](https://code.claude.com/docs/en/cross-session-messaging) (read) — "This session isn't one of the rows"; consistent with measured notes in the user's own `get-aligned`/`morning-standup` skill files |
| Agent teams is experimental, off by default, gated on `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`; without it no team dirs are written | single-source (doc + local test) | [agent teams docs](https://code.claude.com/docs/en/agent-teams) (read); direct test — flag unset in all settings files, `~/.claude/teams` and `~/.claude/tasks` both absent |
| Agent teams has a shared task list (pending/in-progress/completed + dependencies, auto-unblock) with **file-locking on claim** | single-source | [agent teams docs](https://code.claude.com/docs/en/agent-teams) (read) |
| Task list lives at `~/.claude/tasks/{team}`; team config at `~/.claude/teams/{team}/config.json`; mailboxes at `~/.claude/teams/{team}/inboxes/{agent}.json` | single-source | [agent teams docs](https://code.claude.com/docs/en/agent-teams) (read) |
| **One team per session, not shareable across sessions**; lead is fixed for its lifetime; teammates are spawned *by the lead*, not joined by independently-opened chats | single-source | [agent teams docs](https://code.claude.com/docs/en/agent-teams) (read) — "Limitations" |
| Agent teams does **not** worktree-isolate teammates; partition by file ownership instead | single-source | [run agents in parallel](https://code.claude.com/docs/en/agents) (read) |
| Agent teams task status lags in practice — teammates fail to mark complete, blocking dependents | single-source | [agent teams docs](https://code.claude.com/docs/en/agent-teams) (read) — stated limitation |
| Agent view background sessions are auto-isolated into `.claude/worktrees/<id>/`; disable per-repo with `worktree.bgIsolation: "none"` | single-source | [agent view docs](https://code.claude.com/docs/en/agent-view) (read, summarizer paraphrase) |
| `claude agents --json` lists all local sessions, human-opened interactive ones included, distinguished by a `kind` field (`"interactive"` vs `"background"`); agent view's dashboard UI, built for dispatching and monitoring background sessions, surfaces a human-opened session only after `/bg` backgrounds it, but the JSON listing is not so limited | single-source (direct local run) | Direct local run, verified twice on this machine: interactive sessions listed with `"kind": "interactive"` alongside `--bg` ones with `"kind": "background"`; dashboard-UI backgrounding behavior per [agent view docs](https://code.claude.com/docs/en/agent-view) (read) |
| Claude Code officially recommends worktrees for parallel sessions and enforces isolation by blocking edits/commands that reach the main checkout | single-source | [worktrees docs](https://code.claude.com/docs/en/worktrees) (read) |
| Worktree-per-agent is the dominant duplicate-work prevention across the ecosystem, not lock protocols | **verified** | [Baton writeup](https://muhammadraza.me/2026/building-baton-autonomous-agent-orchestrator/) (read); [code-conductor](https://github.com/ryanmac/code-conductor) (read); [Addy Osmani, Code Agent Orchestra](https://addyosmani.com/blog/code-agent-orchestra/) (read) |
| GitHub Issues as an agent work queue is an established pattern (label-marked tasks, agent claims, worktree per issue, PR closes it) | **verified** | [code-conductor](https://github.com/ryanmac/code-conductor) (read) — `conductor:task` label; [Baton](https://muhammadraza.me/2026/building-baton-autonomous-agent-orchestrator/) (read) — polls labelled issues |
| Baton releases a claim by checking whether a PR exists for the issue's branch after the worker finishes; without it, finished agents held slots | single-source | [Baton writeup](https://muhammadraza.me/2026/building-baton-autonomous-agent-orchestrator/) (read) |
| `acceptEdits` deadlocks an unattended agent ("blocks forever waiting for a prompt nobody will answer"); autonomous runs need `bypassPermissions` + disposable worktrees | single-source | [Baton writeup](https://muhammadraza.me/2026/building-baton-autonomous-agent-orchestrator/) (read) |
| "One file, one owner" — never let two agents edit one file — is the stated core rule | single-source | [Addy Osmani](https://addyosmani.com/blog/code-agent-orchestra/) (read) |
| The bottleneck in multi-agent coding has moved from generation to **verification** | single-source | [Addy Osmani](https://addyosmani.com/blog/code-agent-orchestra/) (read) |
| Recommended team size is 3–5 agents; three focused beat five scattered | single-source | [agent teams docs](https://code.claude.com/docs/en/agent-teams) (read) |
| A cross-session message can never approve a permission, change config, or run a command in the receiver; receiver's own prompts still fire | single-source | [cross-session messaging docs](https://code.claude.com/docs/en/cross-session-messaging) (read) |
| Message loops are throttled: repeats dropped, ≤50 queued, bursts refused at the sender | single-source | [cross-session messaging docs](https://code.claude.com/docs/en/cross-session-messaging) (read) |
| `SendMessage` supports `notify_when_idle` — a one-shot idle notice from a watched session, no polling, 12-hour expiry, main-conversation only | single-source | [cross-session messaging docs](https://code.claude.com/docs/en/cross-session-messaging) (read) |
| No first-party surface coordinates a fleet of **independently human-opened, long-lived, worktree-spread** sessions against a durable external board | **inference** | Derived from the three docs' stated scopes: teams = lead-spawned + session-scoped; agent view = its `--json` listing sees interactive sessions too, but its dashboard is built for background dispatch/monitoring and has no poll, board, or lane assignment; cross-session messaging = transport only, explicitly "not a coordination feature" |

**Declared hole (not a claim):** whether `claude agents --cwd <main checkout>` also returns sessions
whose cwd is a *worktree* of that repo was **not tested** — no live worktree session existed during the
run — and no source addresses it. It is load-bearing for replacing the skill's worktree-path join, so it
must be tested before that replacement ships.

**Correction (not a new claim):** the row above on `claude agents --json` and human-opened interactive
sessions replaces an earlier, docs-only version of that row, which claimed such sessions do not appear
in agent view at all. Direct local testing contradicts that for the JSON listing specifically, so the
row is corrected rather than left standing beside a run that contradicts it.

## Search : fetch ratio

4 searches, 8 fetches, 4 local command runs. Every claim above traces to a fetched read or a local run;
no claim rests on a search snippet.
