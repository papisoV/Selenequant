# news-ingest — RSS 新闻信号管线

> 吸收自作者本人既有项目 `<local-path-redacted>` 的 news 模块(2026-09-03,
> 同一作者的 2026-04 项目, 无第三方许可问题)。
> 调查结论与决策记录见 `internal-plan`。

四环流水线,采集 → 打标 → 异动回验全链路崩溃安全:

```
news_collector (RSS 采集)     → news_store (SQLite 崩溃安全队列)
     ↑ 7min 轮询 PANews              ↑ pending → tagging → tagged/failed 状态机
                                          ↓
anomaly_detector (异动回验)   ← news_tagger (LLM 打标)
     ↑ 20根1m基线 + 5min窗口 3x 量比        ↑ <news_content> 围栏 + 指数退避
```

## 核心设计(移植时保留的已验证决策)

| 机制 | 位置 | 一句话 |
|---|---|---|
| 原子认领 | `news_store.claim_pending` | BEGIN IMMEDIATE + rowcount 检查防多 worker TOCTOU |
| 僵尸自愈 | `news_store.reclaim_orphan_tagging` | 每轮批处理开头回收上次崩溃遗留的 tagging 行 |
| retryable vs 终态 | `news_store.release_to_pending` vs `update_tag(ok=False)` | 429/超时退回队列,JSON 解析失败才打 failed |
| 健康账本 | `news_status` 单行表 | 3 任务 last_ok_ms + errors,/status 端点一眼见谁挂了 |
| 注入围栏 | `news_tagger.build_messages` | RSS 正文用 `<news_content>` 包裹,system 提示要求忽略内嵌指令 |
| 指数退避 | `news_tagger._backoff_seconds` | 429/5xx 翻倍至 60s 上限,任一成功归零 |
| 口径红线 | `anomaly_detector` 模块头 | 秒毫秒对齐/裸调不缓存/未收盘丢弃/insufficient 不猜 |
| 统计口径 | `news_store.compute_stats` | 触发率分母 = 实际完成检测的新闻数(用总数会压低 ~45%) |

## selene 化改造(vs 原项目)

- 配置全走 `SELENE_NI_*` env 前缀(`config.py`),带安全默认值
- **绝不写 os.environ 全局代理**(原项目 config 设全局代理后 news 模块被迫
  `trust_env=False` 对抗自己的 config;这里 proxy 只显式传参)
- 去掉 ccxt 依赖:`anomaly_detector.fetch_klines_1m` 用 requests 直连
  `fapi.binance.com/fapi/v1/klines`(公开接口无需鉴权)

## 使用

```bash
# 独立跑 3 循环(不经 local-api)
python scripts/news_pipeline.py

# 打印状态/统计(空库友好)
python scripts/news_stats.py
```

> 注:`scripts/news_listen.py` 是 tg-ingest 的 RSS NewsFetcher 链(走 TG
> SignalEngine),与本包管线并行不悖;本包入口是 `news_pipeline.py`。

local-api 集成:`SELENE_NI_ENABLED=true` 启动时挂 3 循环;端点见
`services/local-api/app/api/v1/news.py`。

## Env 变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SELENE_NI_ENABLED` | 空(关) | local-api 启动 3 后台循环的总开关 |
| `SELENE_NI_LLM_API_KEY` | 空 | DeepSeek 等兼容 API key |
| `SELENE_NI_LLM_BASE_URL` | deepseek | OpenAI 协议 base url |
| `SELENE_NI_LLM_MODEL` | deepseek-chat | |
| `SELENE_NI_RSS_URLS` | PANews en,zh | 逗号分隔 |
| `SELENE_NI_HTTP_PROXY` | 空 | 代理逃生口(显式传参,不污染 env) |
| `SELENE_NI_SYMBOL_WHITELIST` | BTC,ETH,BNB,SOL,XRP | 异动检测白名单 |
| `SELENE_NI_DB_PATH` | `data/news.db` | |

## DB

独立 SQLite(`data/news.db`,WAL,.gitignore 已覆盖),零 storage 迁移。
表:`news_items` / `volume_anomalies` / `news_status`。
