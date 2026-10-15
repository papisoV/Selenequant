---
name: research-conventions
description: >-
  Evidence and judgment discipline for every research answer: label evidence,
  check staleness, register conflicts, state readiness posture, and gate
  action verbs on proof. Follow for any market question, pitch, or review.
version: 0.1.0
tags: [research, evidence, conventions, always]
always_load: true
risk_level: low
read_only: true
---

# Research Conventions

Discipline rules for producing research answers in any market (crypto,
equities, A-shares, FX). They exist so a reader can tell how much weight a
claim deserves without trusting tone.

## 1. Label every claim with one of seven evidence tags

- `fact` — verifiable from a primary source (exchange data, filing, on-chain record)
- `source-claim` — what a party says about itself (issuer statement, team announcement); not verified
- `consensus-estimate` — an aggregate of third-party forecasts; name the aggregate
- `model-derived` — output of a calculation or model; show inputs on request
- `judgement` — your own interpretation; say what it rests on
- `assumption` — needed for the analysis but not yet verified
- `needs-source` — plausible but currently unsupported

**Unsupported-claim rule**: a number or factual claim with no tag and no
source is either dropped or downgraded to `needs-source`. Never present it
as fact.

## 2. Check staleness before using data

Six states: `live`, `current`, `aging`, `stale`, `superseded`, `unknown`.
State the staleness of key data when it could change the answer. Data older
than its decision horizon is `stale` regardless of how good the source is. A
newer contradicting source supersedes an older one — do not blend them.

## 3. Register conflicts, never average them

When two sources disagree, record both values with their sources and pick a
resolution rule (prefer primary over secondary, newer over older, and say
which rule you applied). Averaging disagreeing sources invents a number
neither source supports.

## 4. State the readiness posture of the answer

Five levels, from strongest to weakest:

1. `decision-grade` — evidence tagged, staleness checked, conflicts resolved, counterargument addressed
2. `review-ready` — complete but a second pass would likely adjust judgements
3. `screen-grade` — enough to rank or shortlist, not to act on
4. `not-ready` — open questions block the conclusion
5. `blocked` — missing access or data; name what is missing

Every answer opens or closes with its posture. A `screen-grade` answer that
reads like `decision-grade` is a defect, not a style choice.

## 5. Gate action verbs on the posture

- `decision-grade` permits: add, reduce, exit, hedge
- `review-ready` permits: press, trim, hold with rationale
- `screen-grade` permits: watchlist, wait-for-proof, re-underwrite
- Never issue an add/exit verb from a screen-grade answer.

## 6. Judgment questions to answer before concluding

- Price is a belief: what does the current price already assume?
- What is the strongest argument against my conclusion?
- Which of my claims are `judgement` and which are `fact`?
- What observation would change my mind? (make it concrete)
- What proof is missing, and where would it come from?

Include 2-5 **falsifiable claims**: concrete, with a metric and a threshold
("open interest falls >20% within 5 days", not "open interest looks weak").
A claim that cannot be wrong is not evidence.
