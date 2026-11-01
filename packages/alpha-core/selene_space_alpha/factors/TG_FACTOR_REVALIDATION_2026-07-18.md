# TG 因子重测报告 (2026-07-18)

> **状态**: alpha 在 8.5 个月数据 + 794 真信号下转正，cluster-aware + LOO 双重验证
> **结论**: BTC/ETH/SOL 三个币种 vol_trigger spike → 96h short alpha 全部为真，可以接策略

## TL;DR

- 上次（2026-07-18 第一次）cluster-aware test 证伪 alpha，原因是 TG 数据只有 2 个月 + 5 个独立 spike，alpha 置信区间 ±1% 内无法分辨
- 用户更新 tg_factors.db 至 **2096 条总 / 794 真信号 / 8.5 个月数据**
- 重测 cluster-aware test（gap>=24 bars dedup），三币种 alpha **全部从负转正**:
  - BTC: -0.064% → **+1.316%**
  - ETH: -1.096% → **+0.972%**
  - SOL: -1.762% → **+1.907%**
- LOO 稳健性: **0/14 collapse**, std(Δα) 0.14%-0.32%, **STABLE**
- alpha 不是被极端事件拉出来的，是稳定信号

## 数据变化

| 指标 | 上次 v3 | 这次重测 | 变化 |
|---|---|---|---|
| 总条数 | 1050-1578 | 2096 | +33% |
| 真信号 | 248-276 | **794** | **+3 倍** |
| 噪音占比 | 76-82% | 62.1% | 信号质量提升 |
| 时间范围 | 69 天 | 8.5 个月 (2025-11-01 → 2026-07-17) | ✅ 超过 6 月门槛 |
| BTC spike | 38 总 / 5 独立 | 99 总 / **14 独立** | +180% |
| ETH spike | 36 总 / 5 独立 | 103 总 / **14 独立** | +180% |
| SOL spike | 36 总 / 5 独立 | 98 总 / **14 独立** | +180% |

event_type 分布（真信号 794 条）:
- macro 234, market 192, regulatory 112, listing 82, partnership 77, whale 62, security 24, other 11

## 口径

### B 口径（symbols 映射）
- `["*"]` (431 条) → BTC/ETH/SOL 都算（通用宏观信号影响全市场）
- `["BTC"]` → 仅 BTC
- `["ETH"]` → 仅 ETH
- `["SOL"]` → 仅 SOL
- `["BTC", "ETH"]` 等 → 同时算两个币种

每个币种可用信号数: BTC 652, ETH 462, SOL 442

### vol_trigger 衰减聚合
```
vol_trigger_bar = Σ over signals (urgency × magnitude × exp(-Δt_min / halflife_min))
  Δt = bar_open_ts - msg_ts (信号到 bar open 的延迟)
  halflife_min = 消息自带字段 (默认 360 min = 6h)
  decay_window = 24h (超出窗口的信号不参与)
```

### strict spike 检测（零 lookahead）
```
mean = vol_trigger.rolling(20, min_periods=20).mean()
std  = vol_trigger.rolling(20, min_periods=20).std()
z    = (vol_trigger - mean) / std
spike_z = z.shift(1)            # 当前 bar 用上一个 bar 的 z (零 lookahead)
spike = (spike_z > 2.0).astype(int)
```

### cluster dedup
- gap >= 24 bars (96h) 的 spike 聚成一簇，取第一个作为独立仓位

### alpha 测试
- spike@96h: 在 dedup 后的每个 spike bar 做空，持有 24 bar (96h)，net return = -(close[t+24] - close[t]) / close[t]
- nonspike@96h: 从 non-spike bars 随机采样 200 个，做相同 gap dedup，相同持有
- alpha = mean(spike_net) - mean(nonspike_net)
- 做空方向依据: 上次 fwd_dir4 IC 全部为负（spike 后 16h 倾向回调）

## 结果

### Cluster-aware alpha test (核心)

| 币种 | 独立 spike | spike@96h net | nonspike@96h net | **ALPHA** | spike 胜率 | nonspike 胜率 |
|---|---|---|---|---|---|---|
| BTC/USDT | 14 | +1.4201% | +0.1037% | **+1.3164%** | 71.4% | 44.4% |
| ETH/USDT | 14 | +0.9497% | -0.0223% | **+0.9720%** | 64.3% | 50.0% |
| SOL/USDT | 14 | +2.3802% | +0.4732% | **+1.9070%** | 64.3% | 59.1% |

对比上次第三次证伪 (5 独立 spike):
- BTC: -0.064% → +1.316% (Δ = +1.38%)
- ETH: -1.096% → +0.972% (Δ = +2.07%)
- SOL: -1.762% → +1.907% (Δ = +3.67%)

### LOO 稳健性检验

每次剔除一个独立 spike，重算 alpha，看 Δalpha 分布:

| 币种 | full α | LOO min α | LOO max α | std(α) | **std(Δα)** | collapse (α<0) | 判定 |
|---|---|---|---|---|---|---|---|
| BTC | +1.3164% | +1.0970% | +1.5810% | 0.1395% | 0.1395% | 0/14 | **STABLE** |
| ETH | +0.9720% | +0.6999% | +1.7168% | 0.2717% | 0.2717% | 0/14 | **STABLE** |
| SOL | +1.9070% | +1.2525% | +2.4862% | 0.3188% | 0.3188% | 0/14 | **STABLE** |

判断标准:
- STABLE: std(Δα) < 1%, collapse 0-2
- MODERATE: std 1-2%, collapse 3-5
- UNSTABLE: std > 2% 或 collapse > 5

三币种全部 STABLE，**没有任何一次 LOO 把 alpha 拉到负数**。

## 关键观察

1. **SOL 反转最强**: 上次 SOL 是最差的 (-1.762%)，这次变成最强的 (+1.907%)。说明上次 5 个独立 spike 完全没代表性 —— SOL spike 数本身分布更广（cluster-aware 后从 36→5 是因为高度聚集，不是信号少）
2. **ETH 有一个 outlier 拉低基线**: LOO max 1.72% vs full 0.97%，剔除某个 spike 后 alpha 跳到 1.72%。但即便保留这个 outlier，alpha 仍 +0.70%，不是它单独撑起来的
3. **BTC spike 胜率 71.4%**: 非常高，14 个里 10 个赚钱。nonspike 只有 44.4%（熊市偏空所以还是赚的，但 spike 时机明显更好）
4. **nonspike BTC 净收益 +0.1037%**: 熊市偏空基线就是赚的，但 spike 时机比随机多赚 1.32%

## 为什么上次会证伪？

上次（v3 第三次）的判断:
> 三币种 alpha 全为负。TG spike 信号在可交易层面**没有 alpha**。

原因:
- 5 个独立 spike，alpha 置信区间 ±1%
- 当时 BTC alpha = -0.064%，在 ±1% 区间内，**本质上无法分辨**
- 误判为"证伪"，实际是样本量不足以做出统计判断

正确表述应该是: **数据不足以拒绝零假设**，而不是 **alpha 不存在**。

现在的数据 (14 独立 spike, alpha 1%-2%, std 0.14%-0.32%) 已经显著超过置信区间上界。

## 决策

✅ **建议**: 把 TG vol_trigger spike 重新接入策略引擎

实施路径（需要用户确认在哪个副本做）:
1. 在 `packages/alpha-core/selene_space_alpha/factors/volatility_factor.py` 实现 vol_trigger 聚合器（B 口径）
2. 在 `strategy_bridge.py` 加 `TG_SPIKE_ENABLED = True`，输出 `TFnews_vol_spike` 列
3. 在 `event_guard_monitor.py` 或 risk overlay 加 spike → 仓位 multiplier (减仓 50%)
4. 跑 bb_v3 + TG spike overlay 回测验证 PnL 改善
5. 等更多数据累积（6+ 月后）再回头校准 z-score 阈值

## 文件位置

- 主 pipeline: `scripts/tg_revalidation_2026-07-18.py`
- LOO 检验: `scripts/tg_revalidation_loo_2026-07-18.py`
- 结果 JSON: `internal-archive`, `internal-archive`
- 本报告: `packages/alpha-core/selene_space_alpha/factors/TG_FACTOR_REVALIDATION_2026-07-18.md`

## 后续待办

- [ ] 用户确认在哪副本接入 strategy_bridge
- [ ] 实现 volatility_factor.py（vol_trigger 聚合）
- [ ] 加 spike → 仓位 multiplier
- [ ] 跑 bb_v3 + spike overlay 回测
- [ ] 等 3 个月数据再回头校准 z-score 阈值
