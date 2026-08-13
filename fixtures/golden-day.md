# Golden day — worked example & test vectors

One full pipeline pass for NVDA on run_date `2026-07-06`. These numbers are frozen fixtures: `specs/acceptance.md` Phase 2 asserts the gate reproduces the ticket exactly. Also serves as few-shot material for charters.

## Fixture market state

Equity $100,000 · cash $30,000 · positions: AAPL 120 sh @ $232 (cost $210), MSFT 40 sh @ $505 (cost $480) · NVDA price $180.00 · NVDA 60d annualized vol 42% · corr(NVDA, book) avg 0.55 · SPY +0.3% on the day · sector weights: tech 52% (cap 60%) · position count 2 (cap 8) · daily P&L −0.4% (circuit breaker −3%).

## 09:00 Research — signals (via `submit_signal`)

| agent | direction | confidence | summary (abridged) |
|---|---|---|---|
| fundamentals | bullish | 72 | "DC capex guides re-accelerating; fwd P/E below 3y median; margin risk overstated." |
| technical | bullish | 61 | "Reclaimed 50d on volume; base breakout above 178; invalidation 168." |
| news | neutral | 40 | "Export-license headline risk unresolved; flow mixed." |

Disagreement (neutral vs bullish) + contemplated new position → debate triggers.

## 10:00 Debate (thread excerpt, 2 rounds)

- **Bull R1**: capex re-acceleration is confirmed by two hyperscaler prints, not hope; multiple compression already happened.
- **Bear R1**: those same prints flagged supply digestion in H2; the license overhang caps multiple expansion — dead money at best.
- **Bull R2**: digestion affects timing not demand; even bear's own H2 numbers imply 20% upside to consensus.
- **Bear R2**: concede demand; maintain that position should be half-size until license ruling.
- **Risk Q**: "What single print invalidates the capex thesis?" Bull: "Either top-2 hyperscaler guiding capex flat QoQ."

## 11:00 Decision draft (PM, Slack only)

`VERDICT NVDA: BUY 96` + thesis + invalidation, in the debate thread — not yet submitted.

## 11:05 Critique (Critic, via `submit_critique` — Phase 3+; Phase 2 runs Decision as one turn)

```json
{"ticker": "NVDA", "verdict": "objections",
 "objections": ["Bear's surviving half-size point is cited in the thesis but not applied: 96 shares is ~full allowed size, not half."]}
```

Slack: `CRITIQUE NVDA: 1 OBJECTION` + the sentence above, replied in the debate thread.

## 11:10 Decision final (PM, via `submit_decision`)

PM acknowledges in-thread ("Accepted — resizing to 80, ~half of draft intent over current base") then submits:

```json
{"ticker": "NVDA", "action": "buy", "qty": 80,
 "thesis": "Capex re-acceleration confirmed by two prints; bear case reduced to timing. Bear's surviving half-size point respected in sizing.",
 "invalidation": "Top-2 hyperscaler guides capex flat-or-down QoQ, or close below 168."}
```

`stop_price` is deliberately **unset** here: the invalidation is a *close* below 168 plus a non-price condition — neither is an intraday stop. A decision whose invalidation is a hard intraday level would pass `stop_price` and the trader would submit an `oto` order with a stop leg.

## 11:15 Gate — worked math (test vector)

```
step 1  vol 42% ∈ [15%, 50%] → baseline limit 20% of equity = $20,000
step 2  avg corr 0.55 ∈ [0.4, 0.6) → multiplier 0.95 → $19,000
step 3  price $180 → 105 shares max; cash $30,000 ≥ $19,000 → no cash bind
        allowed: {buy: 105, sell: 0, hold: 0}
step 4  positions 3 ≤ 8 ✓ · tech weight (52k+19k)/100k = 71%? NO —
        weight uses position value post-trade vs equity: (27.8k+20.2k+14.4k)/100k
        = 62.4% > 60% cap → resize to cap: max tech add = $11,960 → 66 shares
output  APPROVED ticket {id: "a3f9…", ticker: NVDA, side: buy, max_qty: 66,
        stop_price: null, expires_at: 12:00:00 ET}
```

PM asked 80 > 66 → trader executes 66 (ticket is the contract). Decision `approved`.

Note for implementers: step-4 resize is the "one resize retry" from the design doc — the gate itself computes the capped quantity; no LLM round-trip.

## 11:30 Execution

Order: `client_order_id = "a3f9…"`, buy 66 NVDA, market. Fill 66 @ $180.14. `#trade-log`: `🧾 NVDA buy 66@180.14 (ticket a3f9)` threaded to the verdict. Order `filled`, ticket `consumed`, decision `executed`.

## 16:15 Close (digest excerpt)

P&L +$212 (+0.21%) vs SPY +0.30% · positions AAPL/MSFT/NVDA · 1 trade, 1 hold-skip (AAPL) · est. inference cost $2.31.

## T+5 Reflection (fixture prices: NVDA $191.20, SPY +1.1% over window)

realized_return = (191.20−180.14)/180.14 = **+6.14%** · alpha_vs_spy = **+5.04pp** · invalidated = 0. PM journal entry: "Bear half-size discipline cost ~1pp but was correct process given license overhang; keep."
