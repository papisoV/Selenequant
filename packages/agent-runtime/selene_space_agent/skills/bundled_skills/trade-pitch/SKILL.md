---
name: trade-pitch
description: >-
  Structure for turning a research view into an actionable, risk-bounded
  pitch: variant view, falsifiable claims, scenario tree, loss budget, and
  monitoring triggers. Follow whenever recommending a position or strategy.
version: 0.1.0
tags: [pitch, trade, risk, research]
risk_level: low
read_only: true
---

# Trade Pitch

A pitch turns a research view into something testable and bounded. Market-
agnostic (crypto, equities, A-shares, FX); size and instruments vary, the
structure does not.

## 1. Intake and posture

Start from the readiness posture of the underlying research (see
research-conventions). A pitch built on `screen-grade` research is itself
`screen-grade` — say so, and do not attach action verbs.

## 2. Variant view (four fields)

- **Market believes** — what current price/positioning implies
- **We believe** — the divergence, stated plainly
- **Why the gap exists** — structural or behavioral reason (not "the market is wrong")
- **What closes it** — the concrete catalyst or mechanism, with a horizon

## 3. Falsifiable claims (2-5)

Each claim: concrete metric + threshold + time window. Group by evidence tag
(fact / source-claim / consensus-estimate / model-derived / judgement /
assumption / needs-source). No tag = not a claim.

## 4. Scenario tree

- **Base case** — what you expect, with rough probability
- **Bull case** — what would make it better, and how you would know
- **Bear case** — what kills the thesis; state **survivability**: does the
  position survive this case? If the answer is "probably not", the sizing is
  wrong, not the thesis.
- **Stressed liquidity** — what happens to exits when conditions turn bad
  (funding flips, volume dries up, limit moves)

## 5. Trade expression

- **Loss budget**: maximum acceptable loss stated before entry, and what
  position size makes that true. The budget sets the size, never the reverse.
- **Short-side gate** (any short / negative-convexity expression): confirm
  borrow/fees are understood, upside on squeeze is bounded by the budget,
  and the exit trigger is written down before entry.

## 6. Monitoring triggers

Write down, before entry: what you will watch, what reading means "thesis
intact", and what reading means "exit or re-underwrite". Triggers must be
observable data (funding rate, volume, price level, filing), not feelings.

## Output shape

Lead with actionability: posture, direction, loss budget, horizon, then the
pitch body. A reader should be able to reject the pitch in one line if the
posture or budget fails their bar.
