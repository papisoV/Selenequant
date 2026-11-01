/**
 * landing 域译文表 —— `components/landing/**` 的 en 译文。
 *
 * 分域原因:全站文案约 4,297 条,单文件会远超项目「单文件 ≤800 行」规则;
 * 且分域后每批只改自己那个文件,并发副本间冲突面最小。
 *
 * 语气约定(与 landing 原文一致):审计底稿口吻,陈述事实与计数,
 * 不用营销词。句中大小写保持;专有名词(Selene / SQLite / Optuna / Git /
 * Obsidian / AGPL-3.0 / RECORDED)原样。
 *
 * 见 `internal-plan` 的分批表(B1 数据层 / B2-B3
 * research / B4 watch+trade / B5 其余)。
 */
export const LANDING_TEXT_EN: Record<string, string> = {
  // ── brand-bar ──
  "AI 量化研究团队": "AI quant research team",
  "问": "questions",
  "GitHub 仓库": "GitHub repository",

  // ── progress-rail ──
  "页面章节": "Page sections",
  "主张": "Thesis",
  "不是什么": "Not a chatbot",
  "开源本地": "Open & local",

  // ── hero ──
  "一个在运转的研究团队 · 非演示数据": "A research team that is actually running · no demo data",
  "提问、实验、判决、等待现实检验:": "Ask, experiment, rule, wait for reality to check it:",
  "循环不停止。": "the loop does not stop.",
  "打开研究库": "Open the research library",
  "看它如何研究": "See how it researches",
  "AGPL-3.0 开源 · 本机 SQLite · 历史不出机器": "AGPL-3.0 open source · local SQLite · history never leaves this machine",
  "研究闭环轨道:问题(q-024cfb997e06)经实验、证据、判决、结果,沉淀为记忆(rev-c0515ec80093),再进入下一次判断。全部节点可回源到库内 id,数据快照 2026-10-02。":
    "Research-loop orbit: question q-024cfb997e06 goes through experiment, evidence, ruling and result, settles into memory (rev-c0515ec80093), then feeds the next judgment. Every node traces back to a database id; data snapshot 2026-10-02.",
  "一次真实研究循环(funding 四连判决)· 数据快照 2026-10-02 · 每步可回源到库内 id":
    "One real research cycle (four consecutive funding rulings) · data snapshot 2026-10-02 · every step traces back to a database id",

  // ── council-orbit ──
  "调查会示意:问题 {q} 被三个视角({names})独立调查,主持人裁决":
    "Council diagram: question {q} is investigated independently from three perspectives ({names}), then ruled on by the moderator",
  "视角事件": "Perspective events:",
  "次 · 成功": "· succeeded",
  "次 · 失败降级继续": "· failed and degraded, continued",

  // ── evidence-convergence ──
  "四条实验证据汇入一条结论的连线图": "Diagram of four experiment evidences converging into one conclusion",
  "v1 结论": "v1 conclusion",

  // ── memory-flow ──
  "记忆回流示意:两条知识被下游回答各取回 1 次,一条尚无引用":
    "Memory loopback diagram: two knowledge entries each retrieved once by downstream answers, one not yet cited",
  "下一次回答 q-e31d… · v2": "next answer q-e31d… · v2",
  "虚线 = 已入账但尚无下游引用(如实画断) · 流点 = 真实取回事件":
    "Dashed line = recorded but not yet cited downstream (drawn as a break, honestly) · moving dot = a real retrieval event",

  // ── ③ 回测墙 ──
  "回测": "Backtests",
  "库里共 {total} 条 · 下面展示 {shown} 条 · {win} 盈 {loss} 亏":
    "{total} in the database · {shown} shown below · {win} profitable, {loss} losing",
  "它真的在跑量化研究,": "It is really running quant research,",
  "包括跑输的那些。": "including the ones that lost.",
  "下面是研究库里真实的回测结果。曲线来自 backtests 表的 equity_curve_json, 指标来自 metrics_json,没有一条是示意数据。 半数以上是亏损,照样入库。这是研究记录,不是成绩单。":
    "These are real backtest results from the research database. Curves come from equity_curve_json in the backtests table, metrics from metrics_json — not one of them is illustrative. More than half are losses, recorded anyway. This is a research log, not a scorecard.",
  "回撤": "Drawdown",
  "笔数": "Trades",
  "胜率": "Win rate",
  "这些是回测,**不是实盘**。回测通过不等于策略可用。 Selene 的纪律是把\"当时知道什么\"和\"事后知道了什么\"分开记账。":
    "These are backtests, **not live trading**. Passing a backtest does not make a strategy usable. Selene's discipline is to book what was known at the time separately from what was learned afterward.",

  // ── ⑤ 实验台 ──
  "实验台": "The bench",
  "{templates} 内置模板 · 7 族 · {sources} 类市场 · {objectives} 调参目标":
    "{templates} built-in templates · 7 families · {sources} market types · {objectives} tuning objectives",
  "研究回路脚下,": "Underneath the research loop",
  "是一整套真仪器。": "is a full set of real instruments.",
  "回路能记住结论,前提是有结论可记。结论跑在这套台面上: 真的市场、真的模板、真的调参目标。下面不是能力清单, 是回路每天要用的台面,每条都能在代码里核对。":
    "The loop can remember conclusions only if there are conclusions to remember. Conclusions are produced on this bench: real markets, real templates, real tuning objectives. What follows is not a capability checklist — it is the bench the loop uses every day, and every line can be checked in the code.",
  "台面": "Bench",
  "内置模板": "Built-in templates",
  "旋钮": "Knobs",
  "Bayesian 搜索(Optuna)按下面任一目标自动调参:": "Bayesian search (Optuna) auto-tunes against any one of these objectives:",
  "模板、市场、调参目标都在仓库里可数。回测引擎跑过的那些 (包括跑输的)在上一节。":
    "Templates, markets and tuning objectives are all countable in the repository. The runs the backtest engine has produced (including the losing ones) are in the previous section.",

  // ── ② 调查会 ──
  "调查会": "The council",
  "这一机制已真实跑过 {n} 次视角事件": "This mechanism has actually run {n} perspective events",
  "AI 自己去查,": "The AI investigates on its own,",
  "而且会当场反驳自己人。": "and contradicts its own members on the spot.",
  "一个问题交给三个视角:各自独立召回材料、各自独立分析,只有主持人能落正式回答。 下面这次调查逐字来自真实事件流。":
    "One question goes to three perspectives: each recalls material independently and analyzes independently, and only the moderator can issue a formal answer. The investigation below is verbatim from a real event stream.",
  "你的问题": "Your question",
  "按材料纪律,本轮无可引用证据": "Per its material discipline, nothing citable this round",
  "主持人逐条裁决": "The moderator rules item by item",
  "三视角的分歧被一条条裁定,不是投票": "Disagreements between perspectives are adjudicated one by one, not voted on",
  "正式回答": "Formal answer",
  "注意最后那句": "Note the last line: ",
  "「任何前向含义均不可答」": "\"any forward-looking implication is not answerable\"",
  ":账本里不存在 BTC 1d 短视界的已验证证据,它就拒绝外推。":
    " — the ledger holds no verified evidence for a BTC 1d short-horizon claim, so it refuses to extrapolate.",
  "(此处到此为止:调查会 → 结案的通路尚未打通,不在本页展示。)":
    "(It stops here: the path from council to case-closed is not wired up yet, so it is not shown on this page.)",

  // ── ④ 研究回路 ──
  "研究回路": "Research loop",
  "{n} 条实验 · 全部 RECORDED": "{n} experiments · all RECORDED",
  "一个判断,走了四次实验,": "One judgment, taken through four experiments,",
  "其中一次推翻了上一次的自己。": "one of which overturned its own previous self.",
  "下面每个节点都是 Selene 真实跑过的一次实验,判决逐字来自研究库。 右侧的结论,引用了全部四次实验结果。":
    "Every node below is an experiment Selene actually ran; rulings are verbatim from the research database. The conclusion on the right cites all four experiment results.",
  "实验链": "Experiment chain",
  "全部 4 次实验结果": "all 4 experiment results",
  "这条结论现在的去向": "Where this conclusion goes next",
  "进入记忆": "Into memory",
  "回路当前断在 Execution:实验做到结论、结论入了记忆、 判断也已落成决策记录,但把结论变成一笔真实下单的执行记录, 库里还是 0 条。下一段回路从这里接。":
    "The loop currently breaks at Execution: experiments reach conclusions, conclusions enter memory, judgments become decision records — but turning a conclusion into a record of an actual order is still at 0 in the database. The next leg of the loop picks up from here.",

  // ── ⑥ 不是什么 ──
  "普通 AI 每个问题都从头开始。": "An ordinary AI starts every question from scratch.",
  "Selene 的每个问题都带着历史。": "Every question in Selene carries its history.",
  "普通 AI 研究助手": "Ordinary AI research assistant",
  "在这套机制里,你不是等答案的人。": "In this mechanism, you are not the person waiting for an answer.",
  "你提出问题、追问证据、推翻解释、叫停跑偏的方向,":
    "You raise the question, press for evidence, overturn explanations, call off directions that drift,",
  "它负责让每一次你做出的改动,都被历史接住。":
    "and it makes sure every change you make is caught by the history.",
  "区别不在「有没有记忆」,在于历史会被": "The difference is not whether there is memory, but whether history gets ",
  "自动取回": "retrieved automatically",
  ",不要求你记得去翻。": " without you having to remember to go look.",

  // ── ⑦ 记忆 ──
  "记忆": "Memory",
  "{n} 条已入账知识": "{n} knowledge entries on the books",
  "它不是记住了什么,": "It is not what it remembered,",
  "是因为记得而改变了什么。": "but what changed because it remembered.",
  "每条知识右侧是真实的反向索引:它被哪些后续回答引用过。 「N 次被取回」不是比喻,是可数的引用次数。":
    "The right side of each entry is a real reverse index: which later answers cited it. \"Retrieved N times\" is not a metaphor — it is a countable number of citations.",
  "证据链": "Evidence chain",
  "次被取回": "retrievals",
  "下游回答引用它:": "Later answers cite it:",
  "已入账,尚无下游回答引用此条。": "On the books, not yet cited by any later answer.",
  "把历史存起来,Git 和 Obsidian 也能做到。": "Git and Obsidian can store history too.",
  "被系统自动取回、并因此改变下一次判断": "Retrieved automatically by the system, and changing the next judgment because of it",
  ",才是分界。": " — that is the dividing line.",

  // ── ⑧ 审计栏 ──
  "审计": "Audit",
  "库里共": "In the database: ",
  "条真实回测、": " real backtests, ",
  "个持久研究问题、": " persistent research questions, ",
  "项证据。决策记录落了 2 条, 但执行环节(live_orders / fills)仍是 0,如实报 0。":
    " pieces of evidence. Two decision records exist, but execution (live_orders / fills) is still 0 — reported as 0, honestly.",
  "[ 展开全表 ]": "[ expand full table ]",
  "[ 收起 ]": "[ collapse ]",
  "(不掩饰,如实标出)": " (not hidden, flagged as-is)",
  "· 本地 SQLite · 直接 COUNT · 可自行 sqlite3 核对": "· local SQLite · direct COUNT · verify it yourself with sqlite3",
  "内置 Skill": "Built-in Skills",
  "运行时加载两组,共 16 个": "loaded at runtime in two groups, 16 total",
  "持久问题(33 open / 12 parked / 2 已回答)": "Persistent questions (33 open / 12 parked / 2 answered)",
  "版本化判断,其中 8 条带真实引用边": "Versioned judgments, 8 of them carrying real citation edges",
  "每次实验的判决与关键数": "The verdict and key numbers of each experiment",
  "最小经验单位(不可改)": "The smallest unit of experience (immutable)",
  "下钻调查对象": "Objects to drill into",
  "对象之间的真实链接": "Real links between objects",
  "决策记录(均未结案:pending 1 / never 1)": "Decision records (none closed: pending 1 / never 1)",
  "量化策略向": "Quant strategy",
  "股票研究向": "Equity research",
  "只信被证据链锚住的历史": "Trusts only history anchored by the evidence chain",
  "只看尚未被证据锚定的记录与缝隙": "Looks only at records and gaps not yet anchored by evidence",
  "魔鬼代言人,找其他视角没看的缝隙": "Devil's advocate — hunts the gaps the other perspectives missed",
  "界面": "Interfaces",

  // ── 实验台:入口源族 ──
  "摄取": "Ingest",
  "现货 / 永续": "spot / perpetual",
  "行情 + 交易": "market data + trading",
  "宏观事件": "macro events",
  "→ 因子 / 特征": "→ factors / features",

  // ── 不是什么:对照行 ──
  "单位": "Unit",
  "一次对话": "a single conversation",
  "一个持久问题(Question)": "a persistent question",
  "每个问题独立,不带走上下文": "each question stands alone; context is not carried over",
  "每条判断带着问题全部历史": "every judgment carries the question's full history",
  "输出": "Output",
  "一段回答文本": "a block of answer text",
  "版本化结论 + 它引用了什么": "a versioned conclusion plus what it cited",
  "回答完就结束": "the answer ends it",
  "现实结果回流,改变下一版判断": "real-world results flow back and change the next judgment",

  // ── ⑨ 开源与本地 ──
  "你的研究历史,从不离开你的机器。": "Your research history never leaves your machine.",
  "研究库落在本机 SQLite,历史不出机器": "The research library lives in local SQLite; history never leaves the machine",
  "AGPL-3.0 开源,代码可读、可自托管": "AGPL-3.0 open source — the code is readable and self-hostable",
  "selenespace CLI:同一套引擎,可脚本化、可接 CI": "selenespace CLI: the same engine, scriptable and CI-ready",
  "LLM 与数据源可换,不锁死在某个云": "LLMs and data sources are swappable — no lock-in to a particular cloud",
  "# 起 local-api + 前端": "# start local-api + frontend",
  "真实运行回放 · 行按类别泛化": "Replay of a real run · lines generalized by category",
  "发布门自检终端回放(ok=false,exit 1)": "Publish-gate self-check terminal replay (ok=false, exit 1)",
  "exit 1,门拒绝放行:实现标识符 / 上游标识 / 密钥形态三类探针有命中, 逐条登记;paths / excluded / fixture 三项零命中。处理完重跑,不许绕过。":
    "exit 1, the gate refuses to pass: three probe classes — implementation identifiers, upstream identifiers, secret shapes — have hits, each logged; paths / excluded / fixture are all zero. Fix them and rerun; bypassing is not allowed.",

  // ── ⑩ CTA ──
  "下一次判断,应该建立在上一次真实发生过的结果之上。":
    "The next judgment should build on a result that actually happened last time.",
  "在本地跑起来,或者先看一次完整研究怎么走完一轮。":
    "Run it locally, or first watch one complete research cycle go the whole way.",
  "在本地跑起来": "Run it locally",
  "看一次完整研究": "Watch a full research cycle",

  // ── 研究回路:判决枚举(系统值,非 LLM 正文) ──
  "翻案": "Reversed",
  "veto 不成立": "veto fails",

  // ── 真实研究库内容(lib/landing/real-data.ts 逐字;渲染点包 tr,源串不改) ──
  // 注:多视角名之间的分隔符不登记 —— `、` 是 U+3001,不在覆盖锁的汉字区间内,
  //     登记会被判「陈旧条目」。该处在渲染点按 locale 选分隔符。
  // 调查会 q-804aa2c92a2c
  "今天btc的行情如何": "How is BTC trading today?",
  "延续 rev-c0515ec80093 的四连判决:funding 短视界无预测力、7d 信号真实但规格错配、carry 池化 CI 跨零。今日快照是描述性数据,单日观察不构成对上述任何 IC 结论的检验,因此无需修正。一句话:中期上行趋势内的一天正常幅度回调。这是描述,不是判断。":
    "Continuing the four consecutive rulings in rev-c0515ec80093: funding has no predictive power at short horizons, the 7d signal is real but spec-mismatched, and the pooled carry CI crosses zero. Today's snapshot is descriptive data; a single day's observation does not test any of the IC conclusions above, so no revision is needed. In one sentence: a normal-magnitude pullback within a medium-term uptrend. This is a description, not a judgment.",
  "本轮仅收到一份行情快照,无证据 id,按规则不进 CITED,以下全部按「待检验线索」处理。记录缝隙:资金费率时间戳与数据截止差 6 天,是滞后还是采集缺陷?未验证。「29日涨跌窗口」与资金费率「30日均值」口径不一致,窗口定义未入档。":
    "This round I received only a market snapshot, with no evidence id, so by rule it does not enter CITED; everything below is treated as a lead pending verification. Record gaps: the funding-rate timestamp is 6 days behind the data cutoff — is that lag or a collection defect? Unverified. The \"29-day change window\" and the funding rate's \"30-day average\" use inconsistent scopes, and the window definition was never filed.",
  "「今天」答不了。数据截止 09-28T00:00Z,即 -1.48% 是 09-27 那根 bar。若回答「今天 BTC 跌了 1.48%」,是拿昨天收盘冒充今天;测试会通过,答案仍是错的。funding「现值」是 6 天前的,是 8h 结算变量,中间约 18 个结算周期。我怀疑快照管道在 09-22 生成后 funding 字段未刷新。":
    "\"Today\" cannot be answered. The data cutoff is 09-28T00:00Z, so the -1.48% belongs to the 09-27 bar. Answering \"BTC fell 1.48% today\" would pass off yesterday's close as today's; the test would pass and the answer would still be wrong. The funding \"current value\" is 6 days old and is an 8h settlement variable — roughly 18 settlement cycles in between. I suspect the snapshot pipeline did not refresh the funding field after it was generated on 09-22.",
  "「今天」的语义": "The semantics of \"today\"",
  "正式回答必须写明「09-28 盘中不可观察,最新可观察对象是 09-27 收盘 bar」":
    "The formal answer must state that \"09-28 intraday is not observable; the latest observable object is the 09-27 close bar\"",
  "带 id 的行情副本能否当证据引用": "Whether an id-tagged market copy can be cited as evidence",
  "本轮仅作数字一致性校验(已通过),不作为行情事实的证据引用":
    "This round it serves only as a numeric consistency check (passed); it is not cited as evidence of market facts",
  "funding 滞后是「滞后」还是「缺陷」": "Whether the funding lag is lag or a defect",
  "三者相容,交由反向检验裁决;先修数据再谈结论":
    "The three are compatible and go to reverse validation for a ruling; fix the data before drawing conclusions",
  "最新已收日K(09-27,截止 09-28T00:00Z)BTC 收 83381.5,日跌 1.48%,约 0.7 个 20d 日波动,属温和上行(7d +2.3%、29d +7.4%)中的普通回调日;资金费率读数滞后 6 天不可用;今日盘中与任何前向含义均不可答。":
    "Latest closed daily bar (09-27, cutoff 09-28T00:00Z): BTC closed at 83381.5, down 1.48% on the day, about 0.7 of a 20d daily volatility — an ordinary pullback day within a mild uptrend (7d +2.3%, 29d +7.4%). The funding-rate reading is 6 days stale and unusable; today's intraday and any forward-looking implication are not answerable.",
  // 研究回路 q-024cfb997e06
  "funding 拥挤度对 BTC/ETH/SOL 收益有预测力吗?": "Does funding crowding predict BTC/ETH/SOL returns?",
  "四连判决(0922 → 0926):funding 拥挤度对短视界(4-bar/1h)收益无预测力,但对 7d 多日视界信号真实(方向与拥挤度假说一致,ETH 最强且仍在增强),carry 机制成立但量级太小。信号真实、尺度错配、操作化不过关。V52 不加 funding 规则。":
    "Four consecutive rulings (0922 → 0926): funding crowding has no predictive power for short-horizon (4-bar/1h) returns, but the signal is real at the 7d multi-day horizon (direction consistent with the crowding hypothesis, strongest on ETH and still strengthening); the carry mechanism holds but its magnitude is too small. Signal real, scale mismatched, operationalization inadequate. V52 adds no funding rule.",
  "funding 因子族 4-bar IC sweep": "funding factor family, 4-bar IC sweep",
  "全族 |IC| ≤ 0.02,弱 TF_TREND 一个数量级":
    "whole family |IC| ≤ 0.02, an order of magnitude weaker than TF_TREND",
  "funding 多日视界 IC sweep": "funding multi-day horizon IC sweep",
  "7d 全 symbol 过 0.10 线,方向一致 BTC 8 / ETH 12 / SOL 13 个月":
    "7d: every symbol clears the 0.10 line, direction consistent for 8 BTC / 12 ETH / 13 SOL months",
  "funding 入场否决实验 门1": "funding entry-veto experiment, gate 1",
  "3243 笔 tagged 交易分 7 档,WR 全档平坦":
    "3243 tagged trades split into 7 buckets; win rate flat across all buckets",
  "funding carry+反转模拟 门2": "funding carry+reversal simulation, gate 2",
  "ETH+SOL 池化 n=203,CI95 跨零,不立多日赛道":
    "ETH+SOL pooled n=203, CI95 crosses zero; no multi-day track is opened",
  // 记忆条目(真实反向索引)
  "funding 拥挤度对短视界收益无预测力,对 7d 多日视界信号真实但尺度错配、操作化不过关。":
    "Funding crowding has no predictive power for short-horizon returns; at the 7d multi-day horizon the signal is real but scale-mismatched and operationalization falls short.",
  "加息落地反弹 = 利空已提前定价、不确定性出清(9/16 ETF 流出 $4.5 亿,决议前已定价 93% 概率)。":
    "The post-hike rebound = the bad news was priced in advance and uncertainty cleared (9/16 ETF outflow of $450M; a 93% probability was priced before the decision).",
  "周末效应是复合效应:量 / 波成分稳健,均值成分脆弱;只检验均值的方案会漏掉可检验的稳健部分。":
    "The weekend effect is composite: the volume / volatility components are robust, the mean component is fragile; a design that tests only the mean misses the robust part that is actually testable.",

  // ── version-chain ──
  "它用了 v": "it used v",
  "人": "Human",
}
