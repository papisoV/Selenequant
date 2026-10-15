---
name: strategy-draft
description: Helps users create quantitative strategies through guided conversation and validation
version: 0.1.0
tags: [strategy, quant, drafting]
user_invocable: true
model_invocable: true
requires_tools: [strategy_get_contract, strategy_validate]
---

# Strategy Draft

## Description
Helps users create quantitative strategies through guided conversation and validation.

## Triggers
- "Help me draft a trading strategy"
- "Validate my strategy configuration"

## Steps
1. Clarify the user's market, timeframe, and strategy intent (trend, mean reversion, etc.).
2. Capture symbols, interval, parameters, and entry/exit ideas in structured form.
3. Call `strategy_validate` with a complete strategy config object and iterate on errors.
4. Summarize validated settings and any remaining assumptions.

## Tools Used
- `strategy_validate`: Check parameter ranges, indicator availability, and config consistency before backtesting.

## Example Interaction
**User**: I want a simple SMA crossover on BTC/USDT hourly with 10 and 50 periods.
**Agent**: Here is a draft config. I will run `strategy_validate` with symbols `BTC/USDT`, interval `1h`, and parameters `sma_short=10`, `sma_long=50`. If validation passes, we can refine entry/exit rules or move to a backtest.

## Notes
- Prefer explicit numeric parameters and one primary symbol set before adding complexity.

## Research Thesis First
Before drafting, write one sentence stating the economic mechanism (e.g. "price reverts after short-term extremes"). If the user's idea has no identifiable mechanism, say so and ask — do not silently pick one.

## Recovery Discipline
On validation failure, read only the structured issue fields (`code`, `field_path`, `message`, `recovery`). The `recovery` field is a closed enum:
- `repair_and_revalidate` → fix the named field, re-validate once.
- `fetch_contract_and_rebuild` → re-fetch the contract via `strategy_get_contract` and rebuild the config.
- `stop_and_report` → present the raw output to the user.

Do not re-submit the same failing config unchanged. If the same field fails twice, stop and show the raw output.
