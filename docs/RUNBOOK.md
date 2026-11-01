# RUNBOOK —— 运维与排障

> **这不是入门教程。** 第一次启动请看 [`README-dev-quickstart.md`](README-dev-quickstart.md)(启动命令、端口、白屏三条必读),产品定位看 [`PRODUCT.md`](PRODUCT.md),发布流程看 [`PUBLISH_RUNBOOK.md`](PUBLISH_RUNBOOK.md)。
> 本文只回答两个问题:**它现在健康吗?** 以及 **它不对劲时,我该看哪里?**

---

## 1. 跑起来以后,先确认这三件事

启动后不要先开页面,先确认后端真的服务得动。三道门是**分层**的,顺序不要跳:

| # | 检查 | 命令 | 健康的表现 |
|---|---|---|---|
| 1 | 进程活着、能应答 | `curl -s http://127.0.0.1:8088/api/v1/health` | HTTP 200,信封里 `data.status` 为 `healthy` |
| 2 | 内部组件都健康、该跑的循环都在跑 | `curl -s http://127.0.0.1:8088/api/v1/health/ready` | HTTP 200,`data.ready` 为 `true` |
| 3 | 环境本身没毛病 | `selenespace doctor`(先 `call activate.bat`) | 19 项检查里没有 `fail` |

文中每条命令都可以直接照抄。凡是"预期看到什么"的地方都写明了具体形状 —— 一条查不出结论的排障命令比没有更糟。

三条门的差别很重要,别混用:

- **`/health` 永远返 200**,哪怕内部组件全是坏的。它是前端启动门 —— 前端会重试 60 次,这里返非 200 会让页面直接白屏。**所以 `/health` 返 200 不等于一切正常**,只说明"HTTP 层活着"。
- **`/health/ready` 只在每个组件都 `healthy` 时返 200**,否则 **503**。容器编排、监控探针要盯的是这个。
- 生产环境(`SELENE_ENV=production`)下,来自非 localhost 的请求只返回总状态,不返回组件明细 —— 避免外泄"哪个组件坏了"。本机排障不受影响。

`/health/ready` 回来时,`data.components` 里每个组件带 `status`、`latency_ms`,有问题时还带 `detail`。**`detail` 是排障的第一手材料**,先读它再猜。

---

## 2. 组件地图:坏了意味着什么

`/health/ready` 的组件名是固定的,对照这张表看 `detail`:

| 组件 | 它测什么 | 它坏了意味着 |
|---|---|---|
| `local_api` | API 进程自身 | 服务本身有问题,先看日志 |
| `market_data` | 行情源连接与订阅 | 价格类监控读到 `None` ⇒ 静默,**不会告警**。注意它的判定:注册了源但**全部没在跑**才算 `degraded`;一个源都没注册**不算**(部署可以合法地没有实时源)—— 所以 `healthy` 不能推出"价格一定拿得到" |
| `kline_quality` | K 线数据质量(缺口/陈旧) | 指标类告警(RSI)可能算出陈旧值,回测口径也不可信 |
| `task_liveness` | 长跑后台任务的存活 | **最常见、也最危险的一类**,见下节 |
| `worker_heartbeats` | 数据同步 worker 心跳 | 后台落库停摆,页面数据会越看越旧 |

### `task_liveness` —— 为什么它单独成节

asyncio 里一个任务意外结束,默认什么都不发生:进程照常应答 HTTP,页面照常打开,而那个循环再也不会跑。`task_liveness` 把这件事变成一个健康组件。

- **它能抓**:协程**返回了**(前置条件不满足导致它提前退出,或异常没被任何 `except` 接住)。
- **它抓不到**:任务还活着但卡在 `await` 里。那是另一种故障(需要每个循环自己的心跳),这里不假装能测。

任务分两类:

- **critical**:停了就是错的。返回了或抛异常 ⇒ `unhealthy` ⇒ `/health/ready` 503。目前包括:mission-worker、revisit-watcher、options-poller、kline-gap-sweeper、astock-daily-sync、astock-daily-all、paper-autosave、fund-ingest-premium-alert、pa-trade-alert、**alert-rule-loop**、**alert-large-trade**。
- **best-effort**:一次性的预热/播种,跑完是设计终点。正常返回不报错,只有抛异常才报 `degraded`。

`detail` 的形状是 `{"stopped": [...], "failed": [...]}`:前者是**该跑却停了**的 critical 任务(状态 `returned`),后者是抛了异常的任务(状态 `failed`,还带 `error` 字段,形如 `ValueError: ...`)。每行带 `name`、`critical`、`state`。

**被取消的任务不报**:取消正是收尾时停掉这些循环的正常方式,一个正在退出的进程不是健康问题。所以「收尾瞬间 `/health/ready` 变 503」不该被当成故障告警。

> **为什么这里不自愈?** TSP 的做法是 `os._exit(70)` 让 supervisor 重启。selene 没有这条路:compose 的 `restart: unless-stopped` 只在**进程退出**时触发,不因为 unhealthy 重启。而且重启治不好"因为符号列表为空而提前返回"这类病 —— 重启回来还是同一个状态。**报警(503)本身就是目的**:让容器被摘掉、让人来看。

**告警规则的两个消费者都在 critical 列表里**,这是有意的:它们停了,前端照样显示"已启用"的规则,而实际上一条都不会触发 —— 对监控功能来说这是最坏的状态。

---

## 3. 告警规则不触发,怎么查

规则从前端界面创建,后端存 SQLite,30 秒一轮求值。按下面的顺序查,每步都能排除一类原因:

**第 1 步:规则到底进后端了吗?**

```bash
curl -s http://127.0.0.1:8088/api/v1/intelligence/alerts/rules
```

看返回里有没有你那条。**没有** ⇒ 它还停在前端 localStorage,卡片上会标"(未同步,不会触发)"。求值器只读后端,本地规则永远不会触发。检查写接口有没有失败(前端写失败会保留本地并标 `syncError`)。

**第 2 步:循环还活着吗?**

```bash
curl -s http://127.0.0.1:8088/api/v1/health/ready | python -c "import json,sys; c=json.load(sys.stdin)['data']['components']['task_liveness']; print(c['status'], json.dumps(c.get('detail',{}), ensure_ascii=False))"
```

健康时输出 `healthy {}`。出问题时 `detail` 里是 `stopped`(该跑却停了的 critical 任务)与 `failed`(抛异常的),每个条目带任务名。**不要用 `grep` 截这段 JSON** —— `detail` 是嵌套对象,`grep -o '"[^}]*}'` 会截出半截、看起来像在报错又读不懂。也别把"这条命令没输出"当成健康:解析失败了同样是没输出。

**第 3 步:按规则类型对号入座。**

| 类型 | 求值方式 | 最常见的"不触发"原因 |
|---|---|---|
| `price` | 30s 轮询 WS 价格缓存 | 行情源没连上(`market_data` 组件 `degraded`)⇒ 读到 `None` ⇒ 静默 |
| `indicator` | 30s 轮询,拉 K 线算 | 只支持 **RSI 与 MACD-signal**(其他指标名一律按 MACD-signal 算,见 `alert_sources.py` 的 `_compute`);K 线不足或不新鲜 ⇒ `no_data` ⇒ 静默 |
| `large_trade` | **事件订阅**,不是轮询 | 见下方专门一节 |
| `voice`(新闻关键词) | 30s 轮询新闻库 | 新闻库没起、窗口内没有匹配 ⇒ 静默 |

求值器的跳过原因会写进日志(**默认 INFO 级别,`no_data` 是 DEBUG**,所以排查时可能要临时降日志级别):

> **只加 `--log-level debug` 是没用的。** `main.py:171` 在导入时调了 `logging.basicConfig(level=logging.INFO)`,它抢先把 root logger 钉在 INFO —— 之后再传 `uvicorn --log-level debug` 抬不动 root,你会看到 0 条应用 DEBUG 日志却以为"求值根本没跑"。真要看得起个包装脚本:**先 `import app.main`**(让 basicConfig 跑完),再 `logging.getLogger().setLevel(logging.DEBUG)` 并把 `app` / `app.core.alert_loop` 一并设成 DEBUG,最后才 `uvicorn.run(...)`。

- `not_met` —— 条件没满足。**不打日志**,这是正常状态。
- `no_data` —— **读不到数据**。DEBUG 级。它**不是**"没有匹配":行情源挂了、新闻库没起、指标算不出来,都归这一类。
- `bad_threshold` —— 规则本身写坏了。这是配置问题,不是数据问题。常见的三种:关键词为空、symbol 缺失、**operator 写错了**(只认 `gt` / `lt`)。最后这种不会"猜一个方向"替你触发 —— `gt` 和 `lt` 互为反义,猜错会把用户写的规则静默反转,所以宁可不动。
- `not_pollable` —— 部署里根本没接这条通路(比如没接新闻库却建了 `voice` 规则)。

**关键区别**:`no_data` 和 `not_met` 在界面上看起来一模一样 —— 都是"没报警"。这正是为什么本系统在没数据时**不通知**而不是"通知你一切正常":一个因为数据源挂了而永不触发的规则,如果被当成"监控中",比没有规则更危险。

### 大单规则(`large_trade`)专项

它不是轮询的,所以查法不同:

- **触发下限是检测器的,不是规则的。** 生产里 `large_trade_threshold_usd=10_000.0`(`main.py:1533`)。一笔 8000 美元的成交**根本不会变成事件**,所以哪怕你把规则阈值写成 5000 也永远不会触发 —— 桥接层拿到的就是这么一批事件。规则阈值高于这个下限才有意义。
- **它是订阅 `MarketDataService` 自己的总线**,不是模块级 `get_event_bus()`。后者是另一条总线,挂上去一条都收不到(有专门的测试钉住这一点)。
- 去重键是 `rule_id|symbol`:同一条规则报过 BTC 之后,ETH 仍能单独报。
- 队列深度 1000,满了丢弃并计数(累计第 1、101、201… 次丢弃时打一条 `large-trade alert queue full; N event(s) dropped so far`)。**成交爆量时如果丢事件,这条日志是唯一会告诉你"漏了"的地方。**(进程收尾期间的事件也会被计入 drop,但那时不打日志 —— 它们本来就不该再发。)

### 新闻关键词规则(`voice`)专项

`voice` 是历史误名,规则字段其实是 `keyword`,匹配的是标题/正文文本。

- **窗口是 60 分钟**,按 `fetched_at_ms` 算(不是发布时间 —— 源站时间戳常有偏差,而 link 去重保证每篇只入库一次,首次见到的时间就是"新"本身)。窗口外**再匹配也不会触发**:没有这个窗口,一篇旧闻每轮都会被重新数一遍,规则一旦命中就永远武装。
- 每轮每规则最多取 20 行。一个宽泛的关键词(比如 `BTC`)不会拉全表。
- **没有接新闻库的部署**,这类规则恒为 `not_pollable` —— 静默,不假装"零匹配"。

### 告警发出去了但没收到?

通知走 `dispatch(event_type, text)`,事件类型是 **`alert_rule_hit`**(与 PA 的一次性到价提醒 `price_alert` 分开,可以独立静音)。

**最快的验法:先在设置页点"测试",别等真规则命中。** 后端有专门的真实发送端点(不是干跑):

```bash
curl -s -X POST http://127.0.0.1:8088/api/v1/notifications/channels/<channel_id>/test
```

`data.tested` 为 `true` 才算这条渠道通了。为 `false` 时 `data.status` 就是失败原文(形如 `webhook:名字 failed: ...`),先照着它修渠道,再回来查规则。

失败时按顺序看:

1. 设置页有没有给 `alert_rule_hit` 订阅渠道?订阅表在 `~/.selenespace/notification_preferences.json`。**从 API 写入时拼错 channel id 是写不进去的**(`PUT /notifications/preferences` 会返 `NOT_FOUND: Channel '<id>' not found`);但**先订阅、后删渠道**会留下指向已删 id 的陈旧条目,求值时 `_resolve_targets` 跳过它并打 `Notification subscription references unknown channel <id>` 的 WARNING —— 界面上看起来"已订阅"。**而且这时不会报错**:跳过后若没有别的有效订阅者,就落到第 3 条的环境变量回退;环境变量也没配,`dispatch` 返回空列表、什么都不发 —— 告警静默丢失,只有那条 WARNING 留痕。
2. 渠道凭据在 `~/.selenespace/notification_channels.json`。**这个文件不存在 = 一个渠道都没配**,而规则照样显示"已启用"。`GET /api/v1/notifications/channels` 返回空的 `{"channels": []}` 就是在告诉你这件事。
3. 一个渠道都没订阅时,会回退到环境变量目标(`TELEGRAM_BOT_TOKEN` / `NOTIFY_WEBHOOK`)—— 这是为了防止 kill switch 告警因为没配置而静默丢失。
4. 静音时段(`quiet_hours`)会把通知压掉。
5. 同一规则的静默窗是 **30 分钟**(`DEFAULT_QUIET_WINDOW_SECONDS`)—— 条件持续满足时不会每 30 秒发一次。这个窗口只按键(rule id)记,不按观测值记:价格在 29 分 59 秒时抖动一下不会重置窗口。**验证时如果规则刚命中过,换一条新 id 的规则即可绕开;窗口只活在进程内存里,重启后端也会清空。**
6. **渠道 URL 必须是 `https://` 且域名解析到公网地址。** `selene_space_pulse/notify.py` 的 `_assert_safe_url` 会拒绝 `http://`、`localhost`、私网/回环/链路本地 IP —— 这是 SSRF 防护,不是配置错误。**所以本机起的接收端(`http://127.0.0.1:PORT/...`)永远收不到投递**,想端到端验收得用一个真实的公网 HTTPS 接收地址。被拒时日志里是 `[dispatch] webhook:<名字> failed: Unsafe notification URL scheme: 'http'`,同时 `system_logs` 表里也有一条同文本的 `warning`(查日志 API 能看到)。
7. 上面都排除了,去 `system_logs` 按 logger=`selene_space_pulse.notify` 过滤:投递失败会以 `[dispatch] ... failed` 的 WARNING 落库,不会只出现在控制台。

---

## 4. 数据与状态:哪些文件是真的

写错一个数据库路径,排障会往完全错误的方向跑。本仓至少**三处** `data/` 目录含义不同:

| 路径 | 是什么 | 能不能删 |
|---|---|---|
| `<repo>/data/klines.db` | **正式 K 线库**(约 1.2 GB,由 `activate.bat` 的 `SELENE_KLINES_DB` 指到这里) | 不能。删了等于丢全部历史 |
| `<repo>/data/*.db` | 其他正式库(news / derivatives / macro / fund_ingest 等) | 不能 |
| `services/local-api/data/*.db` | **后端运行时库**,含预警规则 `alert_rules.db`、`market_pulse.db` 等 | 不能删;换机器时要带走 |
| `packages/tg-ingest/data/tgwatcher.db` | Telegram 信号抓取库 | 不能删;丢了要重新积累 |
| 各 `data/optuna_*` 等目录 | 研究产物 | 可删,重跑即得 |

几个容易找错的位置:

- **预警规则**:`services/local-api/data/alert_rules.db`(路径由 `alert_rules_store.py` 按 `__file__` 推导,与 CWD 无关)。
- **日志**:`~/.selenespace/logs/local-api.log`,轮转 5 × 10 MB。控制台也打一份。
- **通知配置**:`~/.selenespace/notification_channels.json`、`notification_preferences.json`。
- **交易所密钥**:OS keyring,不是文件。`selenespace doctor` 里有专门的检查。

> `activate.bat` 会把 `SELENE_KLINES_DB` 设到**当前副本**的 `data/klines.db`;但如果你在机器层面显式设置了 `SELENE_KLINES_DB`,脚本**不会覆盖**它 —— 多个副本可以共享同一个正式库。排障时先 `echo %SELENE_KLINES_DB%` 确认你正在看的是哪个库。

---

## 5. 常见症状速查

启动阶段的问题(白屏、CORS、卡 Connecting)见 [`README-dev-quickstart.md`](README-dev-quickstart.md) 的症状表。这里只列**跑起来之后**的:

| 症状 | 先查什么 | 常见原因 |
|---|---|---|
| 页面开着,但数据全是旧的 | `/health/ready` 的 `kline_quality` 与 `worker_heartbeats` | 同步 worker 停了 / K 线有缺口 |
| 规则显示"已启用"却从不触发 | 第 3 节的三步 | 规则没进后端 / 循环死了 / `no_data` 静默 |
| 大单规则阈值设得很低仍不触发 | `main.py:1533` 的检测器下限 | 成交金额没到 10,000 USD,压根没变成事件 |
| 告警发了但没收到 | `alert_rule_hit` 的渠道订阅 | 没订阅 / 静音时段 / 30 分钟静默窗 / **渠道 URL 不是公网 HTTPS(SSRF 守卫拒收)** |
| 配了本机接收端,投递日志说 `Unsafe notification URL scheme` | 渠道 URL | 这是 SSRF 守卫,合法的拒绝:`http://` 与回环/私网地址一律不收 |
| `/health` 200 但页面不工作 | `/health/ready` | `/health` 永远 200,它不是健康证明 |
| `/health/ready` 503 | `data.components.task_liveness.detail` | 某个 critical 循环停了,查日志找异常 |
| 服务"看起来健康"但其实没在监控 | 同上一行 | 这正是 `task_liveness` 存在的理由 |
| 解除了 kill switch,但下单仍被拒 | `GET /api/v1/trading/kill-switch` | 看这行读数:它若 `true`,打 `POST /trading/kill-switch/reset`(见第 6 节;`release` 不解除单例的历史缺陷已修复,但老版本进程还在跑时仍会遇到) |

---

## 6. 紧急停手:kill switch

```bash
# 触发(需要 admin 角色)
curl -X POST http://127.0.0.1:8088/api/v1/admin/kill-switch/trip \
  -H "Content-Type: application/json" \
  -d '{"reason":"手动停手:行情异常"}'

# 释放(真正让风控恢复下单的入口)
curl -X POST http://127.0.0.1:8088/api/v1/trading/kill-switch/reset
```

> Windows `cmd` 里把续行符 `\` 换成 `^`,并把单引号换成转义双引号:`-d "{\"reason\":\"...\"}"`。PowerShell 里 `\` 续行同样不成立,直接写成一行最省事。

### 这里有两套开关(2026-10-25 实测;release 不解除单例的缺陷已修复)

系统里**并存两个 kill switch**,它们不共享状态:

| | `/admin/kill-switch/*` | `/trading/kill-switch*` |
|---|---|---|
| 状态存放 | `admin.py` 的**模块级变量** | `selene_space_risk.KillSwitch` **单例**(持久化到 `~/.selenespace/kill_switch.json`) |
| 谁读它 | 只有 `GET /admin/kill-switch` 自己 | **下单/撤单路径** —— 真正拦交易的是这个 |
| `trip` 的副作用 | 会把单例也 `activate()` | — |
| `release` / `reset` | 现在也会 `deactivate()` 单例 | `POST /trading/kill-switch/reset` 只 `deactivate()` 单例,不碰 admin 变量 |

> **历史陷阱(已修复)**:曾经 `release` 只清 admin 变量、不解除单例 —— 调完返回
> `{"active": false}`、HTTP 200,但 `GET /api/v1/trading/kill-switch` 仍 `active: true`,系统继续拒单。
> 修复后 `release` 与 trip 对称地 `deactivate()` 单例,并有测试钉住
> (`test_killswitch.py::test_release_deactivates_risk_engine_singleton`)。**老习惯仍值得保留**:
> 解除后用 `GET /api/v1/trading/kill-switch` 复核一眼,确认拦交易的那个状态真的回 false。

顺手记两个返回值的坑(我实测过):`/admin/kill-switch/release` 成功时 `data.reason` 是**硬编码的 `"released"`**,不是你传的原因(你写的进了日志与审计);忘了 `force` 时 HTTP 仍是 **200**,靠 `data.error == "force_required"` 和 `data.active == true` 才能看出没解除 —— **不能只看状态码**。

触发时会:激活 `KillSwitch` 单例(触发自动撤单等回调)、发 `kill_switch_activated` 通知、写一条 `severity=critical` 的审计记录。

> **admin 那套状态是进程内的**:重启后 `_kill_switch_active` 回到 `false`。而 `KillSwitch` 单例的状态**持久化在磁盘**(`kill_switch.json`),重启后仍是 `active: true`。所以「重启后 `/admin/kill-switch` 显示未触发」**绝不等于**风控已恢复 —— 去查 `/trading/kill-switch`。

---

## 7. 改完之后怎么自证

跑测试和类型检查的坑,本仓已经踩过一轮,照做能省时间:

- **必须 `call activate.bat`**,否则裸 `python` 找不到 `packages/` 下的模块。不要 `pip install -e .`。
- **后端全量测试请逐文件跑 `--junitxml`**。`pytest -q` 的汇总行在某些组合下会被吞掉,看起来"没输出"不等于"全绿";全量一起跑还会被 cleanroom 扫描 / lifespan 抓取 / e2e teardown 拖死。
- **前端类型检查**在 `apps/desktop` 下跑 `npm run typecheck`(= `tsc --noEmit`),测试用 `npm run test`(vitest),E2E 用 `npm run e2e`(Playwright)。
- **不要把不同车道的改动卷进一个提交**。工作区经常同时有多份未完成的修改,提交前用 `git diff` 看**全文**,不要只看 `--stat`。

---

## 附:本文的事实来源

本文所有端口、路径、阈值、状态码都不是凭印象写的,取自:

- 健康门:`services/local-api/app/api/v1/health.py`(`/health` 与 `/health/ready` 的分工写在它的 docstring 里)
- 任务清单与判定:`services/local-api/app/core/task_liveness.py`
- 告警求值:`services/local-api/app/core/alert_loop.py`、`alert_evaluator.py`、`alert_large_trade.py`、`alert_sources.py`
- 通知投递:`services/local-api/app/core/notification_dispatch.py`、`app/api/v1/notifications.py`
- 检测器下限:`services/local-api/app/main.py:1533`
- 启动脚本:`dev.bat`、`prod.bat`、`dev.py`、`activate.bat`
- 环境自检:`packages/cli/selene_space_cli/doctor/checks.py`(19 项)

代码改了而文档没跟上时,以代码为准,并把这里改掉 —— 一份说谎的排障手册比没有更贵。
