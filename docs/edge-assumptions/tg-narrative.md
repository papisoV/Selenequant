# 一页纸假设 — TG 叙事信号 (tg-narrative)

> Edge Gate 文档 (Stage C-4 / P1-2)。策略进 CONFIRM 前必须通过本 gate，触发线打钩即降级或停机。
> 关联实现: `packages/alpha-core/.../factors/tg_spike_source.py`, `packages/tg-ingest`（Telegram 消息 → LLM 信号提取 → `tg_factors.db`）, `factory.py` 装配 `tg_db_path`。

## 1. 赚什么钱（机制）

**赚的是叙事扩散早期、价格尚未充分定价的信息差。** Telegram 大 V / 项目方 / 社区频道里的重大事件（listing / whale / security / market / macro / partnership）经 LLM 提取为结构化信号，聚合出 `vol_trigger`（urgency × magnitude × 指数衰减，半衰期 6h），在滚动 20 根 4h bar 的 z-score ≥ 2 时视为 spike。

- **看空为主（均值回归）**: 叙事驱动的短期冲高随后回落 → 分层做空: S 级（多币种同步）1.0x / A 级 0.7x / B 级 0.5x
- **反向看多（1 例）**: C_ETH_REG（ETH + 监管）为 reverse alpha → 0.5x 做多
- **保护**: 宏观事件（CPI/FOMC/NFP ±24h）内趋势延续而非反转 → 仓位减半；SECURITY/REGULATORY ±2h 仓位 ×0.7
- 回测: 8.5 个月 +12.72%，Sharpe ~4.62（2026-07-18 重验证，cluster-aware + LOO）

本质: **散户追逐叙事的行为偏差**。消息驱动的买盘冲高过度，随后在没有新信息时回落，做空这个"冲高"赚回归的钱。

## 2. 为什么还没被赚光

- **信息提取基础设施**: 频道订阅 + LLM 结构化 + 信号聚合是重投入，多数交易者没这管道
- **半衰期窗口**: 信号 6h 内衰减殆尽，需要实时监听——时效性即 alpha，慢一拍就没了
- **噪声过滤**: 大量 bot 频道灌水，需要 urgency/magnitude 加权 + spike z-score 严格检测才不误开仓
- **容量有限**: 单币种可交易信号稀少（BTC/ETH/SOL），单策略资金规模天然受限，套利资金不会来抢

## 3. 什么 regime 会死

| Regime | 为什么死 |
|---|---|
| **宏观事件密集期** | CPI/FOMC/NFP 窗口内叙事信号变趋势延续（回测: 25% win / -1.43% mean），做空叙事在宏观叙事面前是逆势 |
| **信息被提前定价** | 市场变高效，消息落地即一步到位 → spike 不再有回落空间，均值回归失效 |
| **频道生态退化** | 主力频道停更 / 被限流 / 被 bot 污染 → vol_trigger 失去信号源 |
| **单边强趋势** | 叙事信号与强趋势同向时，做空"冲高"会在趋势里被碾死 |

## 4. 死前触发线（可量化，打钩即动作）

| 触发线 | 阈值 | 动作 |
|---|---|---|
| 月度净收益 | 连续 2 个月 net PnL < 0（扣费后） | 降级 confirm→shadow，回查信号质量 |
| 胜率 | 滚动 60 笔交易 win rate < 40%（回测 57.6% 为基准） | 停机，重跑 2026-07-18 重验证流程 |
| 信号枯竭 | 30 天内有效 spike < 8 次（信号源退化信号） | 告警 + 检查频道健康，确认 bot 污染 |
| 宏观覆盖比 | 30 天内宏观窗口内交易占比 > 60% | 降仓位至 0.5x 常态，等宏观日历清空 |
| 数据健康 | `tg_factors.db` 连续 48h 无新信号（监听断） | 停机，检查 tg-listener 进程 |
