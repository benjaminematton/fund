# Vision — the firm at steady state

What we are shooting for. Not a plan and not a spec — a picture of the finished
thing, to remember what all the plumbing is in service of.

*(`fund`, this repo, is the proving ground and stays paper-only. The firm below
trades real capital, and getting there is its own deliberate project.)*

---

## The end state

A working investment firm, staffed by long-lived AI agents, run by one person.

It researches its own ideas, argues them properly, sizes them, trades them, and
grades itself on what actually happened. It runs every day whether or not
anyone is watching. It trades real money — ours. And the agents inside it get
better over months, because the firm keeps score honestly enough to know which
of its own opinions were worth anything.

It stays small on purpose. What edge is left at this horizon lives in the
corners that large institutions can't fit into, so size is the thing most likely
to destroy the advantage. The firm is trying to be right, not big.

The interesting part isn't that the agents are smart. It's that the firm knows
what it doesn't know, and acts that way.

## The floors

**The research floor.** Analyst desks, each with its own beat — the tape and
market structure, news and sentiment, macro and rates, company fundamentals.
Each one shows up every morning with a view, from its own data, in its own
voice. They disagree with each other, and that's the point.

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

**Operations and the books.** The morning standup, the watch on open positions,
the end-of-day digest, P&L attributed by desk and by strategy, and the running
cost of the firm's own thinking.

**The data.** The firm buys its own eyes — real-time market data rather than a
partial free feed, a real news wire with history, corporate actions, and enough
monitoring to know when a feed has gone stale before it acts on it.

**The human.** One, and it stays one. Allocates the capital, sets the limits,
decides which desks exist, and carries what can't be handed to a process.
Everything else, the firm does itself.

## A day, once it's all running

The desks report before the open, each on its own beat. Where they disagree, the
bull and bear take it apart and the critic goes after the reasoning. The PM
reads all of it, weighs each voice by its record, and decides. The gate sizes
what it approves and refuses what it won't. The trader executes. The book is
reconciled, the day is written down, and that night the calls that came due get
scored against what the market actually did — which is what the desks read
tomorrow morning before they open their mouths.

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
year's — measurably, because anything less is a story.

Returns matter too, but they're the last thing to become readable and the
easiest to fool yourself about.

If it turns out the agents simply can't do this — enough graded calls, no skill,
no improvement — then the firm says so out loud and stops. That possibility is
what makes the rest of it worth taking seriously.

## Where we are

As of August 2026: a paper account, two analyst desks, a portfolio manager, a
trader, and the gate — running a real market day end to end and unattended,
from the pre-gate through research, decision, the gate, execution,
reconciliation, and the close. A nightly job resolves decisions once they reach
their horizon, which is what will eventually give the grading something to work
with.

No debate — the decision stage records a default clearance, because there is no
critic seat in it yet. No bull, no bear, no macro, no operations seat. The lab
is built and tested but nothing routes to it. Most of what this document
describes is still ahead of us; the path is in
[`specs/design.md`](specs/design.md) §7.
