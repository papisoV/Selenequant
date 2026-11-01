# TG 因子事件审计报告 (2026-08-13)

> **状态**: 四轮检验闭环 — **方向预测证伪，事件检测证实**
> **结论**: TG 消息管道是有效的**事件传感器**（macro/listing/whale 消息所在 bar 显著放量、market 消息振幅 1.25x），
> 但**方向预测无价值**（permutation 不显著、LLM direction 在 BTC 上反向有害），
> **无提前量**（量价先动，消息滞后 8-12h）。消息值得用于"事件发生时的反应"，不值得用于"事件发生前的预测"。

## TL;DR

五轮检验覆盖消息价值的五个维度：

| 维度 | 检验 | 判定 |
|---|---|---|
| 方向 | Q1 无脑做空 + permutation | ❌ alpha +1.5~2.0% 但 p=0.12~0.17 不显著 |
| 方向 | Q2 direction 分叉 | ❌ BTC bullish→做多 -3.99%（反向有害） |
| 显著性 | Q3 原复验主张 | ❌ 独立 spike 14→11-12，无偏 permutation 不显著 |
| 量能 | Q4 按事件类型分层 | ✅ macro/listing/whale 消息 bar 显著放量 |
| 波动 | Q5 按事件类型分层 | ✅ market 振幅 1.25x；❌ regulatory/security 无波动 |

- Q4 核心: **macro 消息 bar 量能百分位 59-60%（三币种 p 全 =0.002）**，listing 73-75%、whale 61-69% —— 消息确实标记了真实市场冲击
- Q5 核心: **market（价格突破）当 bar 振幅 1.25x（BTC p=0.002）**；**regulatory ≈ 无波动（SOL 0.97x）**；**security 反向缩波动（0.66-0.87x）**
- 但放量/波动最强的三类（market/whale/listing）全是"果"类事件 —— 价格或资金**先动**，消息后到
- 修正后的判定: **消息 = 事件传感器（有价值），不是方向水晶球（无价值）**

## 数据

| 指标 | 值 |
|---|---|
| tg_factors 真信号 | 794（macro 234 / market 192 / regulatory 112 / listing 82 / partnership 77 / whale 62 / security 24 / other 11） |
| klines | 0718celue/selene-master1（BTC/ETH/SOL 4h，至 07-17 16:00） |
| 窗口 | 2025-11-01 → 2026-07-17（B&H BTC -43.5% / ETH -54.1% / SOL -59.8%，深熊市） |
| 独立 spike（严谨重测） | BTC 11 / ETH 12 / SOL 11 |

## 口径

### 严谨重测三处升级（Q1-Q3，见 `scripts/tg_rigorous_revalidation.py`）

1. **成本模型**: taker 0.05%/边 + 滑点 0.03%/边（spike +0.03%）+ funding 0.01%/8h；基线 RT 0.28%，spike RT 0.34%，差分仅 0.06%
2. **方向分叉**: spike bar 内 decay 加权 `net_dir`，`|net_dir|>0.1` 分 bullish/bearish；bullish 测做多、bearish 测做空
3. **permutation**: 无放回 `rng.choice(n, k)` 无偏采样，N=1000，p_ge 伪计数

### 事件审计（Q4-Q5，见 `scripts/tg_event_audit.py`）

- **事件对齐**: 每条消息 floor 到所属 4h bar（消息时间 ≤ bar open），同 bar 多消息按 bar 去重，避免同一事件被多次加权
- **B 口径 symbols 映射**: `["*"]` → BTC/ETH/SOL 三币；显式短名只算对应币
- **Q4 量能**: 每 bar 绝对量能百分位（全窗口 rank，消除 rolling 分母被前置放量抬高的干扰）；对比随机抽样分布
- **Q5 波动**: 当 bar 振幅 `(high-low)/open` + 事件后 24h 累计绝对波动 `Σ|close/prev-1|`（4 bar）；对比全窗口基线 + permutation

## 结果

### Q1 无脑做空（成本后）

| 币种 | B&H | 独立 spike | spike净 | 基线净 | **α净** | perm_p | null 分布 |
|---|---|---|---|---|---|---|---|
| BTC | -43.50% | 11 | +1.96% | +0.44% | **+1.53%** | **0.121** | +0.68% ± 1.31% |
| ETH | -54.05% | 12 | +2.62% | +0.60% | **+2.01%** | **0.116** | +0.89% ± 1.73% |
| SOL | -59.84% | 11 | +2.55% | +0.81% | **+1.75%** | **0.168** | +1.01% ± 2.03% |

p 全 > 0.05。基线做空在深熊市本身净赚 +0.44~0.81%，spike 择时增量不显著。

### Q2 方向分叉

| 币种 | bullish n | 做多净 | p_long | bearish n | 做空净 | p_short | 方向对齐净 |
|---|---|---|---|---|---|---|---|
| BTC | 4 | **-3.99%** | 0.916 | 3 | +1.21% | 0.320 | **-1.76%** |
| ETH | 4 | +0.43% | 0.309 | 5 | +5.49% | 0.055 | +3.24% |
| SOL | 3 | +0.75% | 0.306 | 5 | +4.27% | 0.122 | +2.95% |

BTC bullish→做多巨亏，**direction 在 BTC 上反向有害**。ETH/SOL bearish→做空弱信号但各 cell 仅 5 笔。

### Q3 显著性

原复验"0/14 collapse STABLE"的 LOO 只测稳健性不测显著性。无偏 permutation 下三币种全不显著。

### Q4 量能分层（消息所在 bar 绝对量能百分位，基线中位 = 50%）

| 事件类型 | BTC | ETH | SOL | 判定 |
|---|---|---|---|---|
| **macro**（FOMC/利率/特朗普） | **60% / p=0.002** | **59% / p=0.002** | **60% / p=0.002** | ✅ 三币种强显著 |
| **listing**（上币） | **73% / p=0.010** | **75% / p=0.004** | **74% / p=0.006** | ✅ 最强，但见下 |
| **whale**（巨鲸） | 61% / p=0.008 | 68% / p=0.012 | 69% / p=0.084 | ✅ BTC/ETH 显著 |
| market（价格突破） | 61% / p=0.002 | 56% / p=0.178 | 56% / p=0.156 | BTC 显著 |
| regulatory（白宫/监管） | 60% / p=0.002 | 56% / p=0.046 | 52% / p=0.253 | 仅 BTC/ETH |
| partnership（合作） | 54% | 53% | 54% | ❌ |
| **security**（安全事件） | 49% | **44%** | 50% | ❌ 反偏低 |

> listing 注: 82 条里 **69 条是 ALT_only**（对三币是板块情绪传导，非直接事件），仅 8 条 `["*"]`。

### Q5 波动分层（当 bar 振幅 vs 基线，倍率；后 24h 累计波动 permutation p）

| 事件类型 | BTC | ETH | SOL | 判定 |
|---|---|---|---|---|
| **market**（价格突破） | **1.25x / p=0.002** | 1.09x / p=0.178 | 1.12x / p=0.006 | ✅ 当 bar 冲击 |
| whale（巨鲸） | 1.29x / p=0.178 | 1.39x / p=0.152 | 1.51x / p=0.383 | 振幅大但样本小(n=5-39) |
| **macro**（FOMC/利率） | 1.11x / p=0.066 | 1.13x / p=0.012 | 1.11x / p=0.008 | ✅ ETH/SOL 显著 |
| listing（上币） | 1.09x / p=0.657 | 1.25x / p=0.399 | 1.22x / p=0.138 | 振幅温和 |
| regulatory（监管） | 1.08x / p=0.389 | 1.05x / p=0.573 | **0.97x / p=0.874** | ❌ ≈ 无波动 |
| partnership | 0.94x | 0.96x | 0.92x | ❌ 低于基线 |
| **security**（安全事件） | **0.82x / p=0.922** | **0.66x / p=0.848** | **0.87x / p=0.561** | ❌ 反向缩波动 |

## 事件类型审计矩阵

| 用途 | 有价值 | 证据 |
|---|---|---|
| **事件检测**（知道市场正被冲击） | ✅ | macro/listing/whale 消息 bar 显著放量 |
| **事件分类**（知道是哪类冲击） | ✅ | event_type 分层差异明确（macro 放量、market 振幅、security 缩波动） |
| **方向预测**（做多/做空赚钱） | ❌ | perm p 全 >0.05；BTC direction 反向有害 |
| **提前量**（赶在价格前） | ❌ | 量价先动，消息滞后 8-12h；macro 是日历公开事件 |

## 关键观察

1. **检验单位决定结论**: 以"消息流量 spike"（vol_trigger 2σ）为单位时量能偏低（22-41% 百分位），因为 spike 偏"消息风暴"；以"单条大事件所在 bar"为单位时 macro/listing/whale 显著放量。**换单位结论反转** —— 之前的"量能偏低"是流量聚集的假象
2. **放量最强的全是"果"类事件**: market（"BTC 突破 10 万"= 突破完了才发）、whale（链上先动）、listing（公告即兑现）。没有一类"有信息量且能提前行动"
3. **regulatory/security 是市场无感事件**: "白宫结束对加密战争"这种大标题对 SOL 波动 0.97x（低于基线），security 波动 0.66-0.87x（反向缩）。LLM 认为的"重大利好/利空"市场不反应
4. **macro 是唯一例外但不是 TG 的功劳**: FOMC/CPI 这类日历事件放量显著，但公开日历就能预测，不需要 LLM 读 TG 频道
5. **delisting 无法检验**: 数据仅 1 条（11-03 币安下架永续）。"下架早就出问题、消息滞后"无数据反例，逻辑成立

## 为什么 2026-07-18 会判"可以接策略"？

1. 无成本模型（全部 close-to-close 毛收益）
2. 基线乐观（排除 spike±窗口 + 固定 seed 200 采样，虚高 alpha）
3. 无显著性检验（LOO 稳健性 ≠ 统计显著性；14 样本撑不起 ±1.3-2.0% 的 null 分布）
4. 未归因熊市做空偏置（基线做空本身 +0.44-0.81% 净，被误读为择时功劳）

## 决策（修正后）

❌ **方向 alpha**: 不接。无脑做空不显著，direction 无跨币种预测力，BTC 上反向有害。

✅ **事件传感器**: 保留复用。已验证的能力是**波动冲击检测与事件分类**，可接入:
- **风控/仓位 overlay**: 高冲击事件（macro/listing）触发减仓，波动消化后恢复 —— 用已验证的"事件检测"机制，不赌方向
- **波动预警**: security 事件后波动反而缩小的反直觉特征可作 regime 区分
- **事件分类调度**: 按 event_type 区分冲击类型（macro 放量 / market 已动 / regulatory 无感）

**消息 = 传感器，不是水晶球。** 它告诉你"市场正在/刚刚经历真实冲击"，不告诉你"该往哪个方向交易"。

## 文件位置

- 严谨重测: `scripts/tg_rigorous_revalidation.py`（`--nperm` `--hold` `--seed` `--klines`）+ `internal-archive`
- 事件审计: `scripts/tg_event_audit.py`（`--klines` `--nperm` `--seed`）+ `internal-archive`
- 本报告: `packages/alpha-core/selene_space_alpha/factors/TG_FACTOR_REVALIDATION_2026-08-13.md`
- 前次报告（被本报告推翻）: `packages/alpha-core/selene_space_alpha/factors/TG_FACTOR_REVALIDATION_2026-07-18.md`

## 后续待办

- [ ] 若继续: 事件传感器接入风控 overlay（spike/高冲击事件 → 减仓），A/B 验证对基础策略 drawdown/Sharpe 的保护价值
- [ ] 若继续: macro 日历事件（FOMC/CPI/利率决议）直接用公开日历触发波动预警，替代 TG
- [ ] 归档: 方向预测路径关闭，文档已记录负结论
