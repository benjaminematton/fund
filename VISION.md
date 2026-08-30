# Vision — the firm at steady state

What we are shooting for. Not a plan and not a spec — a picture of the finished
thing, to remember what all the plumbing is in service of.

*(The account is paper today, and stays paper for a long time — that's the
measurement, not a holding pattern. Real capital is where this ends up: the last
step of this project, not a different one.)*

---

## The end state

A working investment firm, staffed by long-lived AI agents, run by one person.

It researches its own ideas, argues them properly, sizes them, trades them, and
grades itself on what actually happened. It runs every day whether or not
anyone is watching. It trades real money — ours. And the agents inside it get
better over months, because the firm keeps score honestly enough to know which
of its own opinions were worth anything — and then does something with the
score, without waiting to be told.

That last part is what kind of firm this is. A firm run by one person can be
one where every improvement waits on that person: read the scoreboard, decide
what it means, edit a prompt, adjust a limit. That firm improves at the speed
of its owner's attention — slowly, and in bursts. Or it can be one where the
firm reads its own record, applies the rules it was given every night, and
turns what the rules can't settle into a concrete proposal with the evidence
attached — so the person's job is to approve or refuse, not to notice and
author. Same person, same rules, same invariants. The difference is whose
attention is the bottleneck. We are building the second kind.

It stays small on purpose. What edge is left at this horizon lives in the
corners that large institutions can't fit into, so size is the thing most likely
to destroy the advantage. The firm is trying to be right, not big.

The interesting part isn't that the agents are smart. It's that the firm knows
what it doesn't know, and acts that way.

## The floors

**The research floor.** Analyst desks, each with its own beat — the tape and
market structure, news and sentiment, macro and rates. Each shows up every
morning with a view, from its own data, in its own voice. They disagree with
each other, and that's the point. Fundamentals is the desk that doesn't fit
this rhythm: it needs evidence that arrives quarterly and a source the firm
doesn't have yet, so it earns a seat only once there's a slower stage to sit in.

**The lab.** A standing research operation inventing, testing, and killing
systematic strategies. Most of what it proposes dies, which is how you know the
testing is real. What survives earns capital and keeps it only while it works.
The lab never stops running, because an edge that isn't being replaced is an
edge quietly expiring.

**The debate.** Seats whose whole job is attacking the firm's own conclusions —
the bull, the bear, and a critic who goes after the reasoning rather than the
position. Every serious multi-agent system ever built keeps a seat like this,
and the ones that dropped it regretted it.

**The portfolio manager.** One seat that sees all of it — the desks, the
argument, the lab's strategies, the current book — and decides what the firm
does today. It listens to each analyst in proportion to how right that analyst
has actually been, not how good today's pitch sounded.

**The trader.** Places the orders. Has no opinions.

**The gate.** The deterministic layer between every decision and every order.
It sizes from volatility and correlation, holds the firm's limits, and either
approves or refuses. No judgment, no language model, no talking it around. This
is the thing that makes the rest of it safe to run, and it behaves identically
on the day the account is real as it did on every paper day before it.

**The scorecard.** Every seat graded continuously on whether it was right and
whether its confidence meant anything. Desks that turn out to have no edge get
narrowed or retired. Nobody grades themselves.

**The improvement loop.** What the scorecard says, the firm acts on, in two
tiers. The first is rules a human already wrote, applied by code every night
with nobody asked: analyst weights recomputed from calibration, a strategy's
allocation ramped or cut by the kill rules, a desk narrowed once its record
says it has no edge. The rule was the approval. The second is anything that
would change a rule or a prompt — a charter that keeps making the same
mistake, a limit the record says is mis-set, a desk that should exist or
shouldn't, a strategy family worth trying. Those the firm cannot change
itself, and doesn't try to. It writes the proposal — the diff, the evidence
behind it, what it expects to improve and how that would show up on the
scorecard — and puts it in front of the human. Nothing in the firm edits its
own instructions; nothing waits for the human to notice a problem either.
Charters, thresholds, and capital change only by human commit. The proposal is
the firm's.

**Operations and the books.** The morning standup, the watch on open positions,
the end-of-day digest, P&L attributed by desk and by strategy, and the running
cost of the firm's own thinking.

**The data.** The firm buys its own eyes — real-time market data rather than a
partial free feed, a real news wire with history, corporate actions, and enough
monitoring to know when a feed has gone stale before it acts on it.

**Slack.** Where the firm is visible. Every report, argument, gate verdict, fill
and digest lands in a channel as it happens, so a day can be read after the fact
or watched while it runs, and any seat can be asked a question and will answer
from its own record. It's the firm's face and its running log — never the place
decisions actually come from.

**The human.** One, and it stays one. Holds the only hand that can commit:
capital, limits, which desks exist, what any seat is told. Spends that hand
mostly on the firm's own proposals — merge, refuse, or send back for more
evidence — and on the exceptions no rule was written for. Carries what can't
be handed to a process. Everything else, the firm does itself, and the measure
of whether it does is how little of the human's attention a good month needs.

## A day, once it's all running

The desks report before the open, each on its own beat. Where they disagree, the
bull and bear take it apart and the critic goes after the reasoning. The PM
reads all of it, weighs each voice by its record, and decides. The gate sizes
what it approves and refuses what it won't. The trader executes. The book is
reconciled, the day is written down, and that night the calls that came due get
scored against what the market actually did — which is what the desks read
tomorrow morning before they open their mouths. The same night the scorecard
is recomputed and the rules run on it: weights move, an allocation ramps or is
cut, a desk narrows. Where the record points at something the rules can't
touch, the firm writes the proposal, and it is waiting in the morning — one
thing, with the evidence — for the human to rule on.

Most days it makes one or two small decisions. Some days the right answer is to
do nothing at all, and it does nothing, without anyone having to intervene.

## Counting forward

There's one way a firm like this fools itself that no amount of care in the
trading fixes. An agent asked how a stock did in some past quarter may simply
remember — the period it's being tested on was in its training data. Tested that
way, these systems look brilliant and then lose half their edge the moment
they're asked about a future nobody has seen. It's the failure mode that makes
most published results in this field worth very little.

So the firm's record is built forward, out of calls made before the outcome
existed, on days that hadn't happened yet. Slow, and the only kind that counts.
It also means the years of paper trading aren't a rehearsal to be endured —
they're the measurement, and the only clean one available.

## What success looks like

The firm's confidence means something — when a desk says seventy percent, it
happens about seventy percent of the time. It runs a handful of modest,
uncorrelated strategies that survived honest testing, none of them spectacular,
which against the base rates for this activity is a genuinely good outcome. The
cost of running the whole thing is known and unremarkable. When something breaks
or a feed goes quiet, the firm stops on its own rather than guessing. And it
learns fast enough that this year's agents are measurably better than last
year's — measurably, because anything less is a story — and most of what made
them better, the firm proposed itself. That has a number too: of the changes
to charters, limits, and desks in a year, how many began as a proposal from
the firm, against how many the human had to notice and write by hand. The
firm is self-optimizing when the first number dominates and the human's part
is mostly saying yes.

Returns matter too, but they're the last thing to become readable and the
easiest to fool yourself about.

If it turns out the agents simply can't do this — enough graded calls, no skill,
no improvement — then the firm says so out loud and stops. That possibility is
what makes the rest of it worth taking seriously.

## Where we are

As of August 2026: a paper account, two analyst desks, a portfolio manager, a
trader, a critic that reviews strategy specs, and the gate — running a real
market day end to end and unattended, from the pre-gate through research,
decision, the gate, execution, reconciliation, and the close. A nightly job
resolves decisions once they reach their horizon, and a reflect seat writes
one reflection per resolved decision.

The loop is open. The grade is computed and the reflection written, and then
nothing reads either: no prompt carries yesterday's reflections, the PM's
weights don't come from calibration, and the scoreboard goes to a channel and
stops there. Every change to a charter, a limit, or the watchlist so far has
been noticed and written by the human. Today the firm is the first kind.

No debate — the decision stage records a default clearance, because the critic
doesn't sit in it yet. No bull, no bear, no macro, no operations seat. The lab
is built and tested but nothing routes to it. Most of what this document
describes is still ahead of us; closing the loop is the next step, and the
path is in [`specs/design.md`](specs/design.md) §7.
