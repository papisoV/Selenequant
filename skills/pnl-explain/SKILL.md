---
name: pnl-explain
description: Explains profit and loss composition and attribution for paper accounts using engine-calculated breakdowns
version: 0.1.0
tags: [pnl, explanation, paper-trading]
user_invocable: true
model_invocable: true
requires_tools: [explain_pnl, paper_pnl]
---

# PnL Explanation

## Description
Explains profit and loss composition and attribution for paper accounts using engine-calculated breakdowns.

## Triggers
- "Why is my PnL down this week?"
- "Break down my paper account PnL"

## Steps
1. Obtain the paper `account_id` (and optional symbol filter).
2. Call `paper_pnl` for aggregate and per-symbol PnL figures where available.
3. Call `explain_pnl` for realized vs unrealized decomposition and narrative detail.
4. Tie changes to positions, fees, and marks—clearly labeling estimates when prices are stale.

## Tools Used
- `explain_pnl`: Decompose realized and unrealized PnL with per-position context.
- `paper_pnl`: Query paper account-level PnL summaries for quick numeric grounding.

## Example Interaction
**User**: Explain my PnL on account paper-123.
**Agent**: I will call `paper_pnl` for the headline numbers, then `explain_pnl` with account_id `paper-123` to break down realized vs unrealized drivers.

## Notes
- Attribution is based on simulated/paper state; live trading may differ due to fills and fees.
- A failed tool call is an error, not an empty result. Never claim "no data" or "zero positions" unless a successful call returned an explicitly empty list.

## Non-Negotiable Safety
Remain read-only. Do not submit, modify, close, or overwrite any position, order, or configuration. If the user asks for an action, propose it as text and wait for them to act.

## Evidence Labels
Label every claim in explanations:
- **Observed**: the number or fact came directly from a tool response.
- **Inference**: your interpretation or estimate — state what it is based on.
- **Alternative**: another plausible cause you considered but rejected (and why).

Never present an inference as an observed fact. Missing data stays missing — never fill gaps with plausible numbers.
