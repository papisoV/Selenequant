# 数据摄取降级链规范 (Data Ingest Degradation Spec)

**Status:** 正式规范 v1.0(2026-09-19)
**Plan:** `internal-plan` Phase 3 §3.1
**适用范围:** selene 全部外部数据摄取面——HTTP provider(`packages/macrodata`)、EventGuard 读库 Source(`packages/alpha-core/selene_space_alpha/event_guard`)、ingest 脚本(`scripts/`)。后续新增数据面必须遵循本规范,不再各自发明降级形态。

---

## 0. 性质与由来

本规范把 crash-monitor 吸收批次(US-001~US-008,2026-09)中已实践的行为模式固化成文。核心立场(计划正典 P2 原则):

> **诚实降级是行为内核,不是异常处理。** 每级降级必须可观测(status 字段 / log),mock 字面量清零,绝不假数据冒充真数据。

规范以正典实现为锚(代码先行,规范成文),引用处均已核对:

- `packages/macrodata/selene_space_macrodata/fallback.py:19-43` `with_retry` — 异步重试底座(attempts=3 线性退避,每级失败 `logger.warning`)
- `fallback.py:51-62` `is_cache_valid` — TTL 判定(兼容 naive/aware datetime)
- `fallback.py:65-79` `stale_fallback` — stale 兜底:有缓存则带 `logger.warning` 返回,无缓存则 raise
- `packages/alpha-core/selene_space_alpha/event_guard/vix_source.py:67-69` — 读库源缺库告警路径(契约见 §3)
- `services/local-api/app/api/v1/intelligence.py`(`market_pulse_history`,本批次重写)— 多源 API 字段降级链组装
- `services/local-api/app/services/market_pulse_store.py` — 真快照累积存储(历史只由真快照逐时累积,不构造)

一条摄取路径如果无法表达五级降级中的每一级,视为设计未完成。

---

## 1. 五级降级链总览

链逐级下行:上一级失败才进下一级;任何一级成功即止。**禁止跨级**(如 L1 失败直接跳 L5 返回 null,而不尝试缓存与备用源)。

| 级 | 名称 | 触发条件 | 可观测信号 | 测试要求 |
|---|---|---|---|---|
| L1 | 自动获取(live) | 无缓存或 TTL 过期,主动抓取成功 | `data_status="live"` + `fetched_at=now` + `source=<主源>` | success 打桩 → 断言 live + 字段契约 |
| L2 | 缓存(cache) | 抓取失败(重试预算耗尽)且缓存仍在 TTL 内 | 值照常返回,`fetched_at` 为缓存时刻 | 失败打桩 + 预置有效缓存 → 断言值=缓存值 |
| L3 | 备用源(fallback source) | 主源连续失败且无可服缓存 → 备用源顶上 | `source=<备用源>` 切换必须可见;响应携带 per-source status | 主源强制不可达 → 断言 source 切换 |
| L4 | stale 标注 | 主备双失败,缓存超出 TTL 但存在 → 服务旧值并显式标注 | `data_status="stale"` + `fetched_at` 可见 + `logger.warning` | 过期缓存 fixture → 断言 stale 标注非空 |
| L5 | 显式 unavailable | 从未取到过,或策略拒绝服务 stale | 值=null + `data_status="unavailable"` + `reason` 非空;HTTP 仍 200 | 空缓存 + 全源失败 → 断言 unavailable + reason |

仓内先例:`intelligence.py` 旧代码 `pulse_dimensions` 已用 `data_available: False + reason` 显式标注缺数据——L5 的 `data_status: unavailable` 沿此先例升级为统一字段名。

---

## 2. 逐级规范

### 2.1 L1 自动获取(live)

- **触发条件**:无缓存或 `is_cache_valid` 判 TTL 过期 → 经 `with_retry`(3 次线性退避)发起抓取并成功。
- **可观测信号**:响应字段三件套 `data_status: "live"` / `fetched_at`(ISO 时间戳)/ `source`(主源标识)。provider 内部每次重试失败打 `logger.warning`(`with_retry` 形态:`macro provider attempt %d/%d failed: %s`)。
- **测试要求**:unit 层 success 打桩(respx/httpx MockTransport)断言解析正确 + `data_status=live`;契约快照测试固定响应字段集,防止后续改动静默丢字段。

### 2.2 L2 缓存(cache)

- **触发条件**:L1 重试预算耗尽(最后一次 `with_retry` 抛出)且本地缓存的 `fetched_at` 在 TTL 内。
- **可观测信号**:值正常返回,`fetched_at` 如实为缓存时刻(下游据此判新鲜度);不判 stale——TTL 内的缓存视为合法数据。每级失败已由 `with_retry` 的 warning 链留痕。
- **测试要求**:integration 层:主源失败打桩 + 预置 TTL 内缓存 → 断言返回缓存值、`fetched_at` 为缓存时刻;TTL 边界(恰好过期/恰好未过期)各一例。

### 2.3 L3 备用源(fallback source)

- **触发条件**:主源 L1 失败且无 L2 可用缓存 → 显式降级到备用源重走 L1 流程。正典实例:Market Pulse 的 fear_greed 字段 alt.me(主)→ CNN(备用)(`packages/macrodata/selene_space_macrodata/providers/altme_fng.py` / `cnn_fng.py`,链组装在 `intelligence.py`)。
- **可观测信号**:`source` 字段切换为备用源标识,必须可从 API 响应直接读出(前端/运维零日志依赖);降级瞬间 `logger.warning` 带源名 + 错误摘要。两源评分口径不同时,`source` 字段是唯一区分依据,禁止混同。
- **测试要求**:integration 层:主源 base_url 强制指向不可达地址 → 断言响应 `source` 自动切到备用源(验收 3 的降级链顺序断言:alt.me → CNN → unavailable)。

### 2.4 L4 stale 标注

- **触发条件**:主备双失败,缓存存在但超出 TTL。策略是**服务旧值但绝不冒充新值**。
- **可观测信号**:`data_status: "stale"` + 原始 `fetched_at` + `logger.warning`(正典形态 `stale_fallback`:`macro provider using stale cache for %s (fetched_at=%s)`)。前端渲染黄标 + 时间(US-004 徽标)。
- **测试要求**:unit 层:预置过期缓存 fixture → 断言 `data_status=stale` 且 `fetched_at` 非空;断言绝无"stale 值伪装 live 字段"的路径。

### 2.5 L5 显式 unavailable

- **触发条件**:从未取到过任何数据(冷启动 + 全源失败),或调用方策略拒绝服务 stale。
- **可观测信号**:值 = null + `data_status: "unavailable"` + `reason` 非空(人话,如 `"alt.me and cnn_fng unreachable: <err>"`);**HTTP 仍 200 不 503**——降级态是合法响应,前端必须可渲染灰标 + reason(验收 2)。
- **测试要求**:e2e 断网环境打真端点 → 200 + unavailable + reason;契约快照锁定三字段(US-003 落地)。

---

## 3. EventGuard 读库源的降级契约(裁决 D 变体)

EventGuard Source 不直连 HTTP——同步读本地 SQLite,`check(symbol, now_ts) → list[EventAlert]`(VixSource 实证,`vix_source.py:128-136`)。宏观抓取经 ingest 桥写 DB,Source 只读库。此域的降级终点不是 `unavailable` 字段,而是:

**源缺席 ≠ 警报。** 空 DB / 缺表 / 库文件不存在 → `check()` 返回 `[]`,乘数 1.0,无异常。理由:"未知 = 危险"在此域是误伤——无 FRED key 的用户若被永久降乘数,即 vix 教训(-526 PnL)同类(计划 pre-mortem ①)。

三重显式保证(缺席不等于静默):

1. **状态可查**:Source 暴露 `get_status() → {available, last_row_ts, degraded_reason}`(CreditSource 起,VixSource 可后补)。
2. **warn-once 日志**:VixSource 缺库路径(`vix_source.py:67-69`)每次缓存刷新都会重复告警——新源要求全生命周期只告警一次,防日志爆炸(计划 pre-mortem ①缓解)。
3. **ingest 侧 fail-loud**:ingest 脚本缺 key → 打印申请指引 + exit 2,不静默空转。

**贯通断言(路径铁律)**:ingest 写入路径与 Source 解析路径经 `resolve()` 归一后**必须相同**(正典 DB 路径 `services/local-api/data/macro_events.db`,US-007 统一;`SELENE_MACRO_DB` 环境变量覆盖与生产构造同链)。路径错位是"行为完全符合规格却永久静默降级"的最高危形态(计划 pre-mortem ④)——空 DB 返回 `[]` 恰是规格行为,测试越绿越隐形。验收以 e2e 写→读非空锁死。

---

## 4. 红线:与 crash monitor mock 的行为差异

上游 US_Stock_Crash_Monitor 的 mock 是**断网演示兜底**——挂在显式 demo 入口,有标注、有来源。selene 吸收调查发现的 mock 是**无来源虚构**:`intelligence.py` 旧代码 `btc_dominance=52.3` / `fear_greed_index=45` / `market_cap=2.45T` 硬编码,前端消费的整条 24h pulses 序列全是构造值,无任何真实依据。

规范规则(原文,不得改写):

> **演示模式 mock 只许用于显式 demo 入口,禁止出现在面向用户的默认路径;无来源的构造值(虚构 mock)一律删除不保留,降级到 unavailable + reason 才是正典形态。**

判据:一条构造值能否保留,看它有没有**来源**(demo 入口标注 / 上游引用 / 明示的演示开关)。没有来源 = 虚构 = 删。上游式"演示模式"若要引入 selene,必须走独立 demo 入口,默认路径不可达。

历史数据同理:历史 pulses 只由真快照逐时累积,store 空时返回 `pulses: []` + `accumulating: true`,**不构造历史**。

---

## 5. 外部代码吸收协议 checklist

任何外部代码吸收,commit 前逐条勾验(固化自计划 Phase 0,US-001 已按此执行):

- [ ] **① 读 LICENSE 记判定**:读上游仓库 LICENSE 原文并记录(上游 + 版权人 + 年份 + 许可类型)。crash monitor 判定:MIT © 2025 竹林寺/middletoo。
- [ ] **② 文件头 provenance header**:逐行吸收文件头加 `# Absorbed from <repo> (<license> (c) <year> <holder>) <source file>`;模式吸收注明 `Pattern absorbed from ...`(如 credit_source.py 的 5y 滚动分位,非逐行)。
- [ ] **③ 三件套登记**:`THIRD_PARTY_NOTICES.md`(许可全文整块附上,照 AlphaPurify 先例格式)+ `ATTRIBUTIONS.md`(条目照 ai-berkshire 节格式)+ 包级 `PROVENANCE.md`(macrodata 包本批次新建)。
- [ ] **④ 登记与代码同 commit**:不允许"代码先上、登记补 PR"——登记面与代码原子进库,审计时不存在中间态。
- [ ] **⑤ AGPL 源零复制**:commit 前 `grep -rn "<上游特征符号>"` 验证命中文件 ⊆ THIRD_PARTY_NOTICES 登记清单;commit message 声明 AGPL 路径零读零拷。

**上游 commit hash 引用惯例**:absorption commit message 必须引用上游 commit hash——本批次惯例:crash monitor = `a7e5003cbd6521c2d5e4b5f9d972aab404d45e57`(v1.3: 六因子非线性风险模型 + 代码重构, 2026-09-17)。hash 使"吸收自上游哪个时点"永远可回溯,上游后续改版可 diff 定位。

**合规测试**:含 provenance marker 的文件必须出现在 THIRD_PARTY_NOTICES.md(本批次新增,进 packages/macrodata tests;计划 pre-mortem ③:防 refactor 把 absorbed 代码拷进未登记文件)。

### 吸收面判定速记

| 吸收面 | 判定动作 |
|---|---|
| 逐行吸收(如 `get_fear_greed()` ~15 行 → cnn_fng.py) | 必须带 provenance header + 三件套登记 |
| 模式吸收(如 5y 滚动分位评分 → credit_source.py) | header 注明 `Pattern absorbed` + 三件套登记(节内注记) |
| 概念吸收(序列 ID / 评分思想 / 降级链形态) | 不触发 header,但计划 / 设计笔记 / 本 spec 须留痕 |

---

## 6. 实例对照:本批次三条链

| 数据面 | 降级链 | 正典引用 |
|---|---|---|
| fear_greed(API 字段) | alt.me → CNN → stale cache → unavailable(`source` 全程可见) | `providers/altme_fng.py` / `cnn_fng.py` / `intelligence.py` |
| btc_dominance / market_cap(API 字段) | CoinGecko global → stale cache → unavailable | `providers/coingecko_global.py` |
| credit(EventGuard 源) | credit_daily 表 → 空/缺表 → `check()` 返 `[]` + warn-once + `get_status()`(缺席≠警报) | `event_guard/credit_source.py` / `vix_source.py:67-69`(告警路径先例) |

三条链共用同一底座(`with_retry` / `is_cache_valid` / `stale_fallback`)与同一终点(诚实 unavailable 或缺席 no-op),这正是"沿仓内正典扩展,不造第二套"(计划 P3)的含义。
