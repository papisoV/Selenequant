---
name: backtest-explain
description: Analyzes backtest results in plain language, connecting metrics to what they mean for the strategy
version: 0.1.0
tags: [backtest, explanation, metrics]
user_invocable: true
model_invocable: true
requires_tools: [backtest_result, explain_pnl]
---

# Backtest Explanation

## Description
Analyzes backtest results in plain language, connecting metrics to what they mean for the strategy.

## Triggers
- "Explain my backtest results"
- "What does this backtest tell me?"
- "Explain this backtest"
- "帮我解释这个回测"

## Steps
1. Obtain the backtest run id the user cares about (or the latest completed run).
2. Call `backtest_result` to load metrics, trade count, and equity context.
3. Call `explain_pnl` when the discussion ties to PnL decomposition on paper accounts; otherwise focus on backtest metrics from step 2.
4. Explain return, drawdown, Sharpe, win rate, and trade count in plain language with caveats.

## Tools Used
- `backtest_result`: Retrieve stored backtest output including metrics and identifiers.
- `explain_pnl`: Relate profit/loss composition when comparing live paper performance to backtest expectations.

## Example Interaction
**User**: I ran a backtest yesterday—can you explain if the drawdown is acceptable?
**Agent**: I will pull the result with `backtest_result`, then walk through max drawdown, win rate, and trade count in context of your strategy goals.

## Notes
- Distinguish backtest metrics from live/paper PnL unless the user links them.
- A failed tool call is an error, not an empty result. Never claim "no backtests found" unless a successful call returned an explicitly empty list.
- If a referenced backtest run is still pending, poll at most 4 times before reporting status and stopping. Never collapse waiting into an unbounded loop.

## Non-Negotiable Safety
Remain read-only. Do not submit, re-run, or modify any backtest. If the user wants a new run or parameter change, propose it as text and route to the appropriate action flow.

## Evidence Labels
Label every claim in explanations:
- **Observed**: the metric or fact came directly from `backtest_result` output.
- **Inference**: your interpretation — state what it is based on.
- **Alternative**: another plausible reading you considered but rejected (and why).

Never present an inference as an observed fact. Missing metrics stay missing — never estimate them.
