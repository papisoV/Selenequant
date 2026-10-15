# TG 消息因子化输出规范

> 目标：定义 TG 消息从"原始文本"到"可回测、可消费、可衰减的因子记录"的标准化输出格式。
> 用途：按此规范改造消息分析流程，输出 SQLite 而非 md 文件。

---

## 1. 为什么当前分析不对

当前 `signal_factors_detail.md` 的 8 维度（情绪等级/事件类型/影响范围/影响强度/紧急程度/推理/筛选结果/状态）有三个硬伤：

1. **不可数值化**："情绪 4/5 利好"是评级，不是可加权的浮点因子值
2. **无标的对齐**："宏观/微观"是模糊分类，因子必须关联到具体 symbol
3. **无时间衰减**：消息影响会衰减，没有半衰期就无法跟 bar 数据对齐

这套维度是"研究员阅读报告"，不是"因子引擎消费的结构化记录"。

---

## 2. 新输出 Schema（10 字段）

每条消息分析后输出一条 JSON 记录，落 SQLite `tg_factors` 表：

```json
{
  "msg_id": 25553,
  "ts": "2026-05-01T08:31:13",
  "symbols": ["ETH"],
  "direction": -0.8,
  "magnitude": 0.6,
  "urgency": 0.8,
  "confidence": 0.7,
  "halflife_min": 120,
  "event_type": "security",
  "reasoning": "500+ ETH 钱包被盗，80 万美元洗钱，影响 ETH 用户信心"
}
```

### 字段定义

| 字段 | 类型 | 约束 | 说明 | 替换原维度 |
|---|---|---|---|---|
| `msg_id` | int | 必填 | 消息唯一 ID | 新增 |
| `ts` | ISO8601 string | 必填 | 消息时间戳 | 原"时间" |
| `symbols` | list[str] | 必填，不可为空 | 关联标的。全市场用 `["*"]`，明确标的使用 `["BTC","ETH"]` | 替换"影响范围" |
| `direction` | float | ∈ [-1.0, 1.0] | 方向分。-1.0 = 强利空，0 = 中性，+1.0 = 强利多 | 替换"情绪 1-5" |
| `magnitude` | float | ∈ [0.0, 1.0] | 影响幅度。0.1 = 微弱，1.0 = 极强 | 替换"影响强度 1-5" |
| `urgency` | float | ∈ [0.0, 1.0] | 紧急度。0.1 = 不急，1.0 = 立即反应 | 替换"紧急程度 1-5" |
| `confidence` | float | ∈ [0.0, 1.0] | LLM 对本次判断的置信度。0.3 = 不太确定，0.9 = 非常确定 | **新增** |
| `halflife_min` | int | ≥ 1 | 消息影响的半衰期（分钟）。60 = 1 小时后影响减半，1440 = 1 天后减半 | **新增** |
| `event_type` | enum | 必填 | 见下方枚举 | 替换"事件类型" |
| `reasoning` | str | 必填，≤200 字 | LLM 推理简述 | 保留"推理" |

**砍掉**：`筛选结果`、`状态`（离线批处理概念，因子不需要）

### event_type 枚举

| 值 | 含义 | 典型消息 | 默认 halflife |
|---|---|---|---|
| `security` | 安全事件 | 钱包被盗、交易所被黑 | 180 min |
| `regulatory` | 监管政策 | 国家禁令、SEC 起诉 | 1440 min |
| `macro` | 宏观经济 | CPI、利率决议、GDP | 720 min |
| `whale` | 鲸鱼/机构 | 大额转账、政府抛售 | 120 min |
| `market` | 市场动态 | 价格突破、暴跌、清算 | 60 min |
| `listing` | 上币/下币 | 新币上线、合约上线 | 240 min |
| `partnership` | 合作/生态 | 企业合作、生态进展 | 360 min |
| `other` | 其他 | 无法归类 | 60 min |

---

## 3. 字段打分指南（给 LLM 的 prompt 参考）

### direction 打分

| 值 | 判断标准 |
|---|---|
| +1.0 | 确定性极强利好（BTC ETF 通过、减半完成） |
| +0.5 ~ +0.9 | 明确利好但力度有限（企业买入、合作） |
| +0.1 ~ +0.4 | 弱利好或市场解读分歧大 |
| 0.0 | 中性/无关/两可 |
| -0.1 ~ -0.4 | 弱利空 |
| -0.5 ~ -0.9 | 明确利空（监管打击、大额抛售） |
| -1.0 | 确定性极强利空（交易���被黑、国家全面封禁） |

### magnitude 打分

| 值 | 判断标准 |
|---|---|
| 0.0 ~ 0.2 | 几乎无影响（名人随口评论、无关行业新闻） |
| 0.3 ~ 0.4 | 小幅影响（小币种消息、非主流交易所动态） |
| 0.5 ~ 0.6 | 中等影响（行业政策、中等规模资金异动） |
| 0.7 ~ 0.8 | 较大影响（主流币重大事件、国家级资金动向） |
| 0.9 ~ 1.0 | 极大影响（BTC ETF、主要交易所被黑、全球性监管） |

### confidence 打分

| 值 | 判断标准 |
|---|---|
| 0.9 ~ 1.0 | 事实性消息（价格突破、官方公告） |
| 0.7 ~ 0.8 | 高可信度报道（主流媒体、已知来源） |
| 0.5 ~ 0.6 | 中等可信度（单一来源、待确认） |
| 0.3 ~ 0.4 | 低可信度（传言、匿名消息） |
| 0.1 ~ 0.2 | 极低可信度（炒作、未经证实） |

### halflife_min 判断

问自己："这条消息 2 小时后还有人交易它吗？"

| 场景 | halflife |
|---|---|
| 闪崩/清算 spike → 瞬时反应 | 30 min |
| 鲸鱼转账 → 1-2 小时消化 | 120 min |
| 监管新闻 → 半天到 1 天影响 | 720 min |
| ETF 通过 → 结构性变化 | 4320 min (3 天) |

### symbols 识别

必须识别消息影响的具体标的，不能只写"宏观"：

| 消息示例 | symbols | 错误写法 |
|---|---|---|
| "比特币突破 77000" | `["BTC"]` | ~~`["*"]`~~ |
| "500 ETH 钱包被盗" | `["ETH"]` | ~~`["微观"]`~~ |
| "不丹政府卖 BTC" | `["BTC"]` | ~~`["宏观"]`~~ |
| "巴西禁止加密跨境支付" | `["*"]` | ~~`["宏观"]`~~ |
| "Solana 生态新 DEX" | `["SOL"]` | ~~`["赛道"]`~~ |
| "SEC 起诉 Binance" | `["BNB","BTC"]` | ~~`["交易所"]`~~ |

如果确实影响全市场，用 `["*"]`，但要谨慎——大部分"宏观"消息其实只影响 BTC。

---

## 4. 输出存储：SQLite `tg_factors` 表

```sql
CREATE TABLE IF NOT EXISTS tg_factors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id      INTEGER NOT NULL,
    ts          TEXT NOT NULL,           -- ISO8601
    symbols     TEXT NOT NULL,           -- JSON array: '["BTC","ETH"]'
    direction   REAL NOT NULL,           -- [-1.0, 1.0]
    magnitude   REAL NOT NULL,           -- [0.0, 1.0]
    urgency     REAL NOT NULL,           -- [0.0, 1.0]
    confidence  REAL NOT NULL,           -- [0.0, 1.0]
    halflife_min INTEGER NOT NULL,       -- >= 1
    event_type  TEXT NOT NULL,           -- enum
    reasoning   TEXT NOT NULL,           -- <= 200 chars
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(msg_id)
);

CREATE INDEX IF NOT EXISTS idx_tg_factors_ts ON tg_factors(ts);
CREATE INDEX IF NOT EXISTS idx_tg_factors_event_type ON tg_factors(event_type);
```

**不要写 md 文件**。md 人能读但机器消费不了。SQLite 既能人读（用 DB Browser）又能机器消费（SQL 查询）。

---

## 5. LLM Prompt 模板（给 Telegram 项目用）

```
你是一个加密货币消息因子分析器。对每条 TG 消息，输出 JSON：

{
  "msg_id": <消息ID>,
  "ts": "<ISO8601时间>",
  "symbols": <影响标的列表，如 ["BTC"] 或 ["*"]>,
  "direction": <方向分 -1.0 到 +1.0>,
  "magnitude": <影响幅度 0.0 到 1.0>,
  "urgency": <紧急度 0.0 到 1.0>,
  "confidence": <置信度 0.0 到 1.0>,
  "halflife_min": <半衰期分钟数>,
  "event_type": "<security|regulatory|macro|whale|market|listing|partnership|other>",
  "reasoning": "<≤200字推理>"
}

规则：
1. direction 必须是浮点数，不用等级。0.0 = 中性，负 = 利空，正 = 利多
2. symbols 必须具体到币种。全市场影响才用 ["*"]
3. halflife_min 问自己：这条消息 2 小时后还有人交易它吗？
4. confidence 是你对本次判断的确定程度，不是消息的可信度
5. reasoning ≤200 字，写结论不是写分析过程
6. 不输出筛选结果/状态/影响范围等旧字段

只输出 JSON，不要输出表格或 markdown。
```

---

## 6. 因子对齐：消息 → Bar（Selene 侧做的事）

Telegram 项目只负责输出 `tg_factors` 表。以下对齐逻辑由 Selene 侧做，列出来让双方对齐预期：

### 6.1 时间对齐

消息时间戳 ≠ bar 时间戳。需要把消息流对齐到 bar：

```
BTC 1h bar [14:00, 15:00):
  消息 A: 14:12 direction=-0.8, magnitude=0.6, halflife=120
  消息 B: 14:35 direction=+0.3, magnitude=0.2, halflife=60
  消息 C: 13:50 direction=-0.5, magnitude=0.4, halflife=180 (上一 bar 的残留)

对齐算法：指数衰减加权
  factor_A = -0.8 * 0.6 * exp(-(14:12→15:00)/120min) = -0.243
  factor_B = +0.3 * 0.2 * exp(-(14:35→15:00)/60min)  = +0.039
  factor_C = -0.5 * 0.4 * exp(-(13:50→15:00)/180min) = -0.112

  tg_direction = sum = -0.316
  tg_intensity = sum of |weighted| = 0.394
```

### 6.2 聚合字段

每个 bar 输出两个因子值：

| 因子名 | 计算方式 | 含义 |
|---|---|---|
| `tg_direction` | Σ(direction × magnitude × decay) | 加权方向，正=净利多，负=净利空 |
| `tg_intensity` | Σ(|direction × magnitude × decay|) | 消息总强度，无论方向 |

### 6.3 标的过滤

只聚合 `symbols` 包含目标标的或 `"*"` 的消息。BTC 策略不看 ETH-only 消息。

---

## 7. IC/IR 验证（决定因子是否有 alpha）

对齐完 bar 后，必须验证因子预测力，才能决定要不要进策略：

```python
# 伪代码
for symbol in ["BTC", "ETH", "SOL"]:
    for interval in ["1h", "4h", "1d"]:
        factors = load_tg_factors_aligned(symbol, interval)
        returns  = load_returns(symbol, interval, forward=1)

        ic = factors["tg_direction"].corr(returns)  # Rank IC
        ir = ic.mean() / ic.std() if len(ic) > 20 else 0

        print(f"{symbol} {interval}: IC={ic:.4f} IR={ir:.4f}")
```

**判断标准**：
- `|IC| > 0.05` 且 `|IR| > 0.3` → 有 alpha，值得进策略
- `|IC| ≈ 0` → 无预测力，TG 消息只当风险闸（仓位乘数），不当因子
- `IC < -0.05` → 反向信号（罕见）

**这一步不做，所有分析都是盲信。**

---

## 8. 模型选择

| 场景 | 模型 | 原因 |
|---|---|---|
| 实时每条消息分析 | haiku | 快、便宜、分类+打分够用 |
| 盘面异动综合解释 | opus | 需要综合 K 线 + 订单簿 + 消息 |
| 批量历史回测分析 | sonnet | 平衡成本和质量 |

haiku 实时跑，opus 触发式跑（盘面异动时才调），sonnet 批量跑（回测时用）。

---

## 9. 不做的事

1. **不输出 md 文件**——人能读但机器消费不了，写进 SQLite
2. **不做"筛选结果/状态"**——离线批处理概念，因子不需要
3. **不把 LLM 当 alpha 来源**——LLM 是信息提取器，不是预测器。direction/magnitude 是"消息说了什么"，不是"市场会怎么走"
4. **不跳过 IC 验证**——不验证就不知道因子有没有用

---

## 10. Telegram 项目侧的交付物

按此规范改造后，Telegram 项目应交付：

1. **SQLite 文件** `tg_factors.db`，含 `tg_factors` 表（按 §4 schema）
2. **LLM 分析脚本**，用 §5 的 prompt 模板，输出 §2 的 JSON 格式
3. **历史回填**：对已有消息（如 224 条导出数据）用新 schema 重新跑一遍，落 `tg_factors` 表
4. **实时管道**：新消息到达时自动分析并落库

Selene 侧收到 `tg_factors.db` 后：
1. 按 §6 对齐 bar → 生成 `tg_direction` / `tg_intensity` 因子
2. 按 §7 跑 IC/IR → 决定因子是否有 alpha
3. 有 alpha → 接进策略（`TFnews_*` aux 列）；无 alpha → 只当风险闸（`tg_source.py` 已做）
