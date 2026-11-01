# 一页纸假设 — 期现资金费率套利 (funding-arbitrage)

> Edge Gate 文档 (Stage C-4 / P1-2)。策略进 CONFIRM 前必须通过本 gate，触发线打钩即降级或停机。
> 关联实现: `templates/funding_rate_arb.yaml`, `execution/dual_leg_executor.py`, `execution/ioc_close.py`, `core/funding_reversal_monitor.py`, `docs/funding-arb-live-wiring.md`。

## 1. 赚什么钱（机制）

**赚的是永续合约的资金费率 carry。** 当 funding rate 高为正时做空 perp + 做多现货，Delta 中性锁住两边，多头持仓者向空头支付资金费，策略作为空头净收取这笔 payment。

- 入场: funding_rate > 0.03% (`funding_rate_threshold=0.0003`) → 空 perp + 多 spot
- 出场: funding_rate 反转 < -0.01% (`funding_rate_exit=-0.0001`) → 双腿平仓
- 执行: `DualLegExecutor` 双腿 delta 中性开/平仓，`IOC` 循环平仓防腿差滑点
- 兜底: `FundingReversalMonitor` 独立 watchdog，费率 < 0.01% (`exit_threshold=0.0001`) 触发 KillSwitch + flatten

本质: 利率套利 / 货币市场 carry。钱来自**对手方为对冲敞口而付出的资金费**——永续多头（杠杆方向性投机者）为维持多头付的钱。

## 2. 为什么还没被赚光

资金费率套利被广泛认知，但仍有可持续空间，因为存在真实进入壁垒:

- **双 key 门槛**: 现货 key + 合约 key 分属不同权限门 (`SELENE_ENABLE_FUTURES` 白名单)，一把 key 无法同时过两个门 —— 挡住了多数散户
- **资本占用**: 双腿 delta 中性 = 锁定 2 倍名义资本，机会成本随利率上升而上升，容量边界 = 资金规模
- **执行摩擦**: 腿差（现货-合约价格偏离）需即时修复，IOC 平仓有成本，小资金套利利润被摩擦吃光
- **反手成本**: 费率反转时仓位的双向滑点 + 资金费支出，要求入场阈值与出场阈值之间有足够窗口

## 3. 什么 regime 会死

| Regime | 为什么死 |
|---|---|
| **费率持续低位/贴水** | funding 中位长期 < 0.02%，carry 不足以覆盖手续费 + 双腿滑点，套利无肉 |
| **费率倒挂** | funding 长期为负，做空方要倒付资金费，本策略方向反了 |
| **单边极端行情** | 现货/合约价格剧烈背离（basis 短期 blowout），delta 中性被腿差穿透，平仓滑点吃掉利润 |
| **交割/资金费结算异常** | 交易所结算频率变化、funding 数据源延迟 → 监控与真实结算脱节 |

## 4. 死前触发线（可量化，打钩即动作）

| 触发线 | 阈值 | 动作 |
|---|---|---|
| 月度净收益 | 连续 2 个月 net PnL < 0（扣费后） | 降级 limited→confirm，复盘 carry 是否被摩擦吃光 |
| 费率窗口 | 滚动 30 天 funding 中位 < 0.015%，或 30 天内入场机会 < 5 次 | 停机待机，等费率窗口重新打开 |
| 费率反转 | funding < 0.01% 且监控触发（`exit_threshold`） | 已由 `FundingReversalMonitor` 自动 flatten + KillSwitch 挡 re-entry |
| 腿差/回撤 | 单日任意单边回撤 > 3%，或双腿总回撤 > 5% | 检查 basis 是否 blowout，超限手动停策略 |
| 数据健康 | funding 数据源连续 6h 无更新 | 停止入场，告警（监控时效性就是套利时效性） |
