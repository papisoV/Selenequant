# /tg-analyze 使用指南

## 基本指令

```
/tg-analyze                                          # 分析最近100条未分析消息（自动过滤噪音）
/tg-analyze 200                                      # 分析200条
/tg-analyze 50 --chat 1234567890                     # 指定群组
/tg-analyze 30 --from 2026-07-01 --to 2026-07-15    # 指定日期范围
/tg-analyze 50 --mode deep                           # 深度分析（增加政策传导路径等）
/tg-analyze 50 --all                                 # 跳过噪音预过滤，分析全部消息
/tg-analyze 50 --overwrite                           # 重新分析已分析过的消息
/tg-analyze stats                                    # 查看分析覆盖率统计
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 数量 | 一次分析多少条消息 | 100 |
| `--chat <id>` | 指定群组 chat_id | 全部群组 |
| `--from <date>` | 起始日期 (YYYY-MM-DD) | 无 |
| `--to <date>` | 结束日期 (YYYY-MM-DD) | 无 |
| `--mode <mode>` | 分析模式: factor/deep | factor |
| `--all` | 跳过噪音预过滤 | 默认过滤 |
| `--overwrite` | 重新分析已分析过的消息 | 默认跳过 |

## 分析模式

- **factor** (默认): 三层管线 — 本地规则引擎预分析 → 噪音自动填充 → Claude 审核 signal candidates
- **deep**: 在 factor 基础上增加政策传导路径、历史类比、反向观点、时间视野、关键不确定性

## 信号 vs 噪音

**信号** (is_signal=true):
- 宏观事件: 美联储降息/加息、CPI、GDP、利率、通胀
- 监管事件: SEC起诉、新法规、禁令、罚款
- 地缘政治: 制裁、战争、贸易战、特朗普政策
- 安全事件: 黑客、被盗、漏洞、跑路
- 系统性风险: 银行破产、救助

**噪音** (is_signal=false，默认预过滤掉):
- 价格行情: "BTC突破64,000"
- ETF资金流: 每日流入/流出
- 交易所上线: "币安上线XXX永续合约"
- 常规数据: 恐惧贪婪指数、资金费率、持仓量
- 空投/代币公告

## 输出字段 (v2 Float Schema)

每条消息输出以下结构化因子:

| 字段 | 类型 | 范围 | 说明 |
|------|------|------|------|
| is_signal | bool | true/false | 信号/噪音标记（策略层只消费 true） |
| direction | float | [-1.0, 1.0] | 方向：负=利空，正=利好 |
| magnitude | float | [0.0, 1.0] | 影响强度 |
| urgency | float | [0.0, 1.0] | 时间敏感度 |
| confidence | float | [0.0, 1.0] | 判断置信度 |
| halflife_min | int | >= 1 | 信号衰减半衰期（分钟） |
| symbols | str | JSON array | 受影响币种，如 `'["BTC","ETH"]'`，宏观事件为 `[]` |
| event_type | str | | security/regulatory/macro/whale/market/listing/partnership/other |
| reasoning | str | <=200 chars | 判断依据（含政策传导路径） |
| llm_model | str | | claude / rule-engine |

### 半衰期参考值

| event_type | halflife_min | 说明 |
|------------|-------------|------|
| security | 180 | 安全事件衰减快 (3h) |
| regulatory | 1440 | 监管事件持续久 (1天) |
| macro | 720 | 宏观事件 (12h) |
| whale | 120 | 鲸鱼动向 (2h) |
| market | 60 | 市场噪音 (1h) |
| listing | 240 | 上线事件 (4h) |
| partnership | 360 | 合作事件 (6h) |

## 数据存储

分析结果写入 `signal_factors` 表（v2 float schema），噪音由规则引擎自动填充（llm_model=rule-engine），信号由 Claude 审核（llm_model=claude）。

```sql
-- 查看高优先级信号
SELECT sf.*, m.text, m.date FROM signal_factors sf
JOIN messages m ON sf.message_id = m.message_id AND sf.chat_id = m.chat_id
WHERE sf.is_signal = 1 AND sf.urgency >= 0.6 AND sf.direction != 0
ORDER BY m.date DESC LIMIT 20;

-- 信号/噪音比例
SELECT is_signal, COUNT(*) FROM signal_factors GROUP BY is_signal;

-- 方向分布（多空）
SELECT
  CASE WHEN direction > 0.1 THEN 'bullish'
       WHEN direction < -0.1 THEN 'bearish'
       ELSE 'neutral' END AS dir,
  COUNT(*), AVG(magnitude), AVG(urgency)
FROM signal_factors WHERE is_signal = 1
GROUP BY dir;

-- 按模型来源统计
SELECT llm_model, COUNT(*) FROM signal_factors GROUP BY llm_model;
```

## 导出

在 Web UI 信号分析页面点"导出"按钮，支持:
- 按群组/日期范围过滤
- 格式: JSON / CSV / Markdown
- 仅导出信号（排除噪音）
