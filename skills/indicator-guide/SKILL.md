---
name: indicator-guide
description: Explains technical indicators (PPO, ChoppinessIndex, EfficiencyRatio, Divergence, plus SMA, EMA, RSI, MACD) with quantitative intuition and example calculations
version: 0.1.0
tags: [indicators, education, technical-analysis]
user_invocable: true
model_invocable: true
requires_tools: [market_kline]
---

# Indicator Guide

## Description
Explains technical indicators (PPO, ChoppinessIndex, EfficiencyRatio, Divergence, plus SMA, EMA, RSI, MACD) with quantitative intuition and example calculations.

## PPO (Percentage Price Oscillator)
- 直觉:MACD 的百分比版 — 快慢 EMA 之差除以慢 EMA,消除不同价格标的量纲差异。
- 关键参数(指标层签名):period=26(slow EMA)、fast_period=12、signal_period=9。
- 信号用法:0 轴上下穿 + 与价格背离。
- 与既有指标:可比作 MACD 的量纲归一化变体。
- 实现:packages/strategy-engine/selene_space_strategy/indicators/ppo.py

## ChoppinessIndex (CHOP)
- 直觉:度量"趋势 vs 震荡"市场清晰度,取值 0-100;值高 = 无方向震荡,值低 = 趋势。
- 关键参数(指标层签名):period=14(注意:预设层参数名 chop_period 是 STRATEGY_REGISTRY 中 chop_guard 模板的参数,预设内部映射到指标层 period)。
- 信号用法:配合趋势策略做"门卫"过滤,震荡期不开仓(chop_guard 模板即此思路)。
- 实现:packages/strategy-engine/selene_space_strategy/indicators/chop.py

## EfficiencyRatio (Kaufman ER)
- 直觉:价格净位移 / 路径总长,0-1;高 = 直线趋势,低 = 来回折返。
- 关键参数(指标层签名):period=14(预设层 er_state_machine 的 er_period 映射到此)。
- 信号用法:ER 状态机切换趋势/回归模式(er_state_machine 模板即此思路)。
- 实现:packages/strategy-engine/selene_space_strategy/indicators/efficiency_ratio.py

## Divergence (背离检测)
- 直觉:价格与指标(R 相对高低点)方向相反 = 动能衰减信号;基于 pivot 高低点配对打分。
- 关键参数(指标层签名):period=14(RSI 周期)、pivot=4(局部极值窗口)、min_strength=0.0(最小强度阈值)。
- 信号用法:顶/底背离作为反转预警,需与趋势过滤叠加。
- 实现:packages/strategy-engine/selene_space_strategy/indicators/divergence.py

## Triggers
- "What is the difference between SMA and EMA?"
- "How do I interpret RSI and MACD together?"

## Steps
1. Ask which indicators and timeframe matter for the user's strategy style.
2. Define each indicator: formula idea, smoothing behavior, and typical range.
3. Give a small numeric toy example (e.g. five closes → SMA vs EMA weighting).
4. Relate signals to strategy design (crossovers, divergence, overbought/oversold) without recommending specific trades.

## Tools Used
- `market_kline`: Optional OHLCV context when the user wants a concrete worked example on a symbol.

## Example Interaction
**User**: Explain MACD in simple terms with numbers.
**Agent**: MACD compares a fast and slow EMA of price, then smooths their difference (signal). For example, if fast EMA is 102 and slow EMA is 100, the MACD line is about +2; the signal line is an EMA of that series. Crossovers and histogram slope are common signal building blocks—always validate on your interval and costs.

## Notes
- This skill is educational; quantitative examples should use hypothetical or cited bars, not investment advice.
- A failed tool call is an error, not an empty result. Never claim "no data" unless a successful call returned explicitly empty data.
