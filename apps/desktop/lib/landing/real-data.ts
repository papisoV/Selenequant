/**
 * SNAPSHOT: 2026-10-05 (refresh-landing-snapshot.py --write 自动戳)
 * Landing 真实数据 fixture —— 全部逐字取自本机真实研究库
 * `~/.selenespace/data/selenespace.db`(research_questions / research_revisions /
 * research_evidence / research_episodes)。
 *
 * ⚠️ 计数会漂移:文件是静态 fixture(landing 需脱离 local-api 独立构建),但真库每天在长。
 *    刷新/校验:`python scripts/refresh-landing-snapshot.py [--write]`
 *    (DB_OBJECTS/SCALE_FACTS 的机械计数由它维护;note 与各处散文需人工同步)。
 *
 * 纪律(与 DESIGN.md 军规⑤"示意内容物不进规格"一致):
 * - 每条内容都有真实 id(rev- / ev- / ep- / q-),可回源;
 * - 数字未经美化(如 |IC|≤0.02、n=203、CI95 跨零),不编造"漂亮"值;
 * - Landing 是静态页,不能依赖本地 local-api,故把真值固化为常量;
 *   改这里 = 换一批真实证据,不新增字段。
 */

export interface RealRevision {
  id: string;
  seq: number;
  /** 该版显式引用的上游 id(真实 cited_json) */
  cited: string[];
  actor: "selene" | "user" | "director";
  createdAt: string;
  /** 逐字正文(可截断用于展示,原文见真实库) */
  content: string;
}

export interface RealQuestion {
  id: string;
  title: string;
  status: string;
  openedAt: string;
  revisions: RealRevision[];
}

export interface RealEvidence {
  id: string;
  episodeTitle: string;
  /** RECORDED = 实测;RECONSTRUCTED = 事后重构(产品真实 provenance 档) */
  mode: "RECORDED" | "RECONSTRUCTED" | "AFTER_THE_FACT";
  sourceRef: string;
  /** 该实验的判决 + 关键数(真值) */
  verdict: string;
  keyNumbers: string;
  observedAt: string;
}

export interface RealMemoryEntry {
  /** 知识条目的载体 = 一条被后续版本引用过的 revision */
  revisionId: string;
  kind: "假设" | "结论";
  text: string;
  /** 真实反向索引:被后续回答引用了几次 */
  usedCount: number;
  /** 引用它的下游版本 */
  usedBy: { questionId: string; seq: number }[];
  chain: { ep: string; ev: string }[];
}

/* ── ① Hero / Loop 的主线问题:一个判断如何被上一版改变 ───────────────────── */

/** BTC 反弹问题 —— v2 显式引用 v1 并在其上修订。真实的"下一次引用上一次"。 */
export const HERO_QUESTION: RealQuestion = {
  id: "q-e31df43a5161",
  title: "BTC 为什么最近反弹了?9 月 17 日宣布加息,不应该利空吗",
  status: "open",
  openedAt: "2026-09-18",
  revisions: [
    {
      id: "rev-f812f21b0d52",
      seq: 1,
      cited: [],
      actor: "selene",
      createdAt: "2026-09-18 07:25",
      content:
        "第一版判断:利空已提前定价,加息落地 = 不确定性出清。9/15-16 BTC 已先跌到 $75.6K 月内低点,9/16 ETF 单日流出 $4.5 亿(6 月以来最大);市场在决议前已按约 93% 概率定价 25bp 加息。9/17 加息落地反而触发反弹,BTC 站上 $77K。",
    },
    {
      id: "rev-532cd8241a19",
      seq: 2,
      cited: ["rev-f812f21b0d52"],
      actor: "selene",
      createdAt: "2026-09-18 07:27",
      content:
        "第二版:在第一版(利空提前定价、现货吸收衍生品卖压)基础上,反弹成色存疑。需求端三条腿都弱:Coinbase 溢价打到月内新低、企业金库 3 个月仅买入约 5900 BTC、ETH/XRP ETF 转为净流出。若「加息后反弹 = 出清」成立,后续应有现货持续净流入确认;若 BTC 跌回 $75K 下方且溢价不修复,则第一版的出清论是错的。",
    },
  ],
};

/* ── ② THE LOOP 的纵深案例:一个问题 → 四次实验 → 结论翻转 ────────────────── */

/** funding 拥挤度问题 —— 单条 v1 引用 4 个真实实验证据,含一次结论翻案。 */
export const LOOP_QUESTION: RealQuestion = {
  id: "q-024cfb997e06",
  title: "funding 拥挤度对 BTC/ETH/SOL 收益有预测力吗?",
  status: "sufficiently_answered",
  openedAt: "2026-09-22",
  revisions: [
    {
      id: "rev-c0515ec80093",
      seq: 1,
      cited: ["ev-aa12269ea516", "ev-df9effd9f8ab", "ev-d6345a1ac57d", "ev-905370c8fcf8"],
      actor: "selene",
      createdAt: "2026-09-26 17:23",
      content:
        "四连判决(0922 → 0926):funding 拥挤度对短视界(4-bar/1h)收益无预测力,但对 7d 多日视界信号真实(方向与拥挤度假说一致,ETH 最强且仍在增强),carry 机制成立但量级太小。信号真实、尺度错配、操作化不过关。V52 不加 funding 规则。",
    },
  ],
};

/** 上条 v1 引用的 4 个真实实验(逐条来自 research_evidence + research_episodes)。 */
export const LOOP_EVIDENCE: RealEvidence[] = [
  {
    id: "ev-aa12269ea516",
    episodeTitle: "funding 因子族 4-bar IC sweep",
    mode: "RECORDED",
    sourceRef: "scripts/funding_ic_sweep_2026-09-22.json",
    verdict: "NO-GO",
    keyNumbers: "全族 |IC| ≤ 0.02,弱 TF_TREND 一个数量级",
    observedAt: "2026-09-22",
  },
  {
    id: "ev-df9effd9f8ab",
    episodeTitle: "funding 多日视界 IC sweep",
    mode: "RECORDED",
    sourceRef: "scripts/funding_ic_sweep_multi_horizon_2026-09-25.json",
    verdict: "翻案",
    keyNumbers: "7d 全 symbol 过 0.10 线,方向一致 BTC 8 / ETH 12 / SOL 13 个月",
    observedAt: "2026-09-25",
  },
  {
    id: "ev-d6345a1ac57d",
    episodeTitle: "funding 入场否决实验 门1",
    mode: "RECORDED",
    sourceRef: "scripts/funding_veto_test_2026-09-26.json",
    verdict: "veto 不成立",
    keyNumbers: "3243 笔 tagged 交易分 7 档,WR 全档平坦",
    observedAt: "2026-09-26",
  },
  {
    id: "ev-905370c8fcf8",
    episodeTitle: "funding carry+反转模拟 门2",
    mode: "RECORDED",
    sourceRef: "scripts/funding_carry_reversal_sim_2026-09-26.json",
    verdict: "INCONCLUSIVE",
    keyNumbers: "ETH+SOL 池化 n=203,CI95 跨零,不立多日赛道",
    observedAt: "2026-09-26",
  },
];

/* ── ③ MEMORY:被后续回答真正引用过的知识条目(真实反向索引) ─────────────── */

export const MEMORY_ENTRIES: RealMemoryEntry[] = [
  {
    revisionId: "rev-c0515ec80093",
    kind: "结论",
    text: "funding 拥挤度对短视界收益无预测力,对 7d 多日视界信号真实但尺度错配、操作化不过关。",
    usedCount: 0,
    usedBy: [],
    chain: [
      { ep: "ep-f1249d926160", ev: "ev-aa12269ea516" },
      { ep: "ep-d22888f41a29", ev: "ev-df9effd9f8ab" },
      { ep: "ep-b659aaa746e7", ev: "ev-d6345a1ac57d" },
      { ep: "ep-f032aeffa5c9", ev: "ev-905370c8fcf8" },
    ],
  },
  {
    revisionId: "rev-f812f21b0d52",
    kind: "结论",
    text: "加息落地反弹 = 利空已提前定价、不确定性出清(9/16 ETF 流出 $4.5 亿,决议前已定价 93% 概率)。",
    usedCount: 1,
    usedBy: [{ questionId: "q-e31df43a5161", seq: 2 }],
    chain: [],
  },
  {
    revisionId: "rev-85e844875147",
    kind: "结论",
    text: "周末效应是复合效应:量 / 波成分稳健,均值成分脆弱;只检验均值的方案会漏掉可检验的稳健部分。",
    usedCount: 1,
    usedBy: [{ questionId: "q-d4a7d28f405d", seq: 3 }],
    chain: [],
  },
];

/* ── 真实规模读数(全部可数,非吹嘘;2026-10-02 从研究库直接 COUNT) ─────── */

export const REAL_SCALE = {
  knowledgeEntries: MEMORY_ENTRIES.length,
  /** 跨问题被后续版本引用过的 revision 数(真实反向索引非零条数) */
  recalledRevisions: MEMORY_ENTRIES.filter((e) => e.usedCount > 0).length,
  /** 真实 provenance 档位(产品数据模型保证,非营销词) */
  provenanceModes: ["RECORDED", "RECONSTRUCTED", "AFTER_THE_FACT"] as const,
};

/**
 * 研究库真实物体计数 —— 直接 COUNT 本机 `selenespace.db`。
 * 刷新:`python scripts/refresh-landing-snapshot.py --write`(机械计数);note 人工同步。
 * 纪律:只放能直接数出来的量;不做"能力"宣称。
 * 注:decisions 已从 0 变 2(2026-10-03 落库),故不再进 ZERO 组;两条均 outcome 未结
 *    (pending/never),与"回路断在 Decision"的表述并存 —— 断的是**结案回写**,非记录本身。
 */
export const DB_OBJECTS: {
  table: string;
  label: string;
  count: number;
  note: string;
  zero?: boolean;
}[] = [
  { table: "research_questions", label: "Question", count: 47, note: "持久问题(33 open / 12 parked / 2 已回答)" },
  { table: "research_revisions", label: "Revision", count: 18, note: "版本化判断,其中 8 条带真实引用边" },
  { table: "research_evidence", label: "Evidence", count: 13, note: "每次实验的判决与关键数" },
  { table: "research_episodes", label: "Episode", count: 13, note: "最小经验单位(不可改)" },
  { table: "research_missions", label: "Mission", count: 35, note: "下钻调查对象" },
  { table: "research_edges", label: "Edge", count: 27, note: "对象之间的真实链接" },
  { table: "research_decisions", label: "Decision", count: 2, note: "决策记录(均未结案:pending 1 / never 1)" },
];


/* ── NOT A CHATBOT 对照(机制差异,非贬低对手) ─────────────────────────── */

export const CONTRAST_ROWS: { axis: string; chatbot: string; selene: string }[] = [
  {
    axis: "单位",
    chatbot: "一次对话",
    selene: "一个持久问题(Question)",
  },
  {
    axis: "历史",
    chatbot: "每个问题独立,不带走上下文",
    selene: "每条判断带着问题全部历史",
  },
  {
    axis: "输出",
    chatbot: "一段回答文本",
    selene: "版本化结论 + 它引用了什么",
  },
  {
    axis: "结果",
    chatbot: "回答完就结束",
    selene: "现实结果回流,改变下一版判断",
  },
];

/* ── 真实 skill 两组(纠正旧 Landing 只讲一组 8 个) ─────────────────────── */
export const SKILL_GROUPS: { label: string; dir: string; items: string[] }[] = [
  {
    label: "量化策略向",
    dir: "skills/",
    items: [
      "strategy-draft",
      "strategy-coder",
      "backtest-explain",
      "market-analysis",
      "pnl-explain",
      "risk-report",
      "indicator-guide",
      "exchange-setup",
    ],
  },
  {
    label: "股票研究向",
    dir: "bundled_skills/",
    items: [
      "dcf-model",
      "earnings-analysis",
      "earnings-preview",
      "initiating-coverage",
      "morning-note",
      "sector-overview",
      "trade-pitch",
      "research-conventions",
    ],
  },
];

/* ── ⑦ 调查会(COUNCIL):AI 自主研究 —— 全页第二重的真实证据 ────────────────
 *
 * `investigate_council` 是一个真实现:三个异质视角各自独立召回历史材料、
 * 各自独立分析,只有主持人落正式 revision;视角产物只进事件流,不进回答链。
 * 视角差异来自"材料过滤组合 + 人格",不来自换模型。
 *
 * 下面这次跑(q-804aa2c92a2c「今天btc的行情如何」)是库里跑得最完整的一次:
 * 三视角全部成功、内容实质、并且真的产生了分歧与自我挑战。
 * 逐字取自 research_question_events(kind='investigate_perspective')。
 *
 * 诚实约束(与 1002 能力审计一致):
 * - 只展示"调查会本身"(3 视角 → 分歧 → 裁决 → 正式回答),全部真跑过;
 * - **不展示"调查会 → 人拍板 → 结案"闭环** —— decisions 表虽有 2 行(2026-10-03 落库),
 *   但两条 outcome 均未结(pending 1 / never 1),结案回写通路尚未走通;
 *   35 个 mission 有 32 个卡在 investigation(不虚构)。
 */

export interface CouncilPerspective {
  key: "empiricist" | "recordist" | "challenger";
  label: string;
  /** 视角的材料纪律(来自 investigate_council.py 的人格定义) */
  discipline: string;
  /** 逐字原文(节选自真实事件,保留关键的判断与挑战句) */
  quote: string;
  /** 该视角真实引用的证据 id(recordist 恒为 0,这是纪律使然) */
  citedIds: string[];
}

export interface RealCouncil {
  questionId: string;
  questionTitle: string;
  openedAt: string;
  perspectives: CouncilPerspective[];
  /** 主持人逐条裁决的分歧(真实"观点整合与矛盾"内容) */
  rulings: { dispute: string; verdict: string }[];
  /** 主持人落下的正式回答(逐字,节选) */
  revision: string;
  /** 官方口径:真实跑过的视角事件数(成功/失败都算,失败会降级继续) */
  eventsRun: number;
  eventsOk: number;
}

export const COUNCIL: RealCouncil = {
  questionId: "q-804aa2c92a2c",
  questionTitle: "今天btc的行情如何",
  openedAt: "2026-09-28 03:43",
  eventsRun: 48,
  eventsOk: 22,
  perspectives: [
    {
      key: "empiricist",
      label: "经验派",
      discipline: "只信被证据链锚住的历史",
      quote:
        "延续 rev-c0515ec80093 的四连判决:funding 短视界无预测力、7d 信号真实但规格错配、carry 池化 CI 跨零。今日快照是描述性数据,单日观察不构成对上述任何 IC 结论的检验,因此无需修正。一句话:中期上行趋势内的一天正常幅度回调。这是描述,不是判断。",
      citedIds: [
        "ev-85c701bd2407",
        "ev-aa12269ea516",
        "ev-d6345a1ac57d",
        "ev-df9effd9f8ab",
        "ev-905370c8fcf8",
        "rev-c0515ec80093",
        "ev-fea9fc354bbf",
      ],
    },
    {
      key: "recordist",
      label: "记录派",
      discipline: "只看尚未被证据锚定的记录与缝隙",
      quote:
        "本轮仅收到一份行情快照,无证据 id,按规则不进 CITED,以下全部按「待检验线索」处理。记录缝隙:资金费率时间戳与数据截止差 6 天,是滞后还是采集缺陷?未验证。「29日涨跌窗口」与资金费率「30日均值」口径不一致,窗口定义未入档。",
      citedIds: [],
    },
    {
      key: "challenger",
      label: "挑战派",
      discipline: "魔鬼代言人,找其他视角没看的缝隙",
      quote:
        "「今天」答不了。数据截止 09-28T00:00Z,即 -1.48% 是 09-27 那根 bar。若回答「今天 BTC 跌了 1.48%」,是拿昨天收盘冒充今天;测试会通过,答案仍是错的。funding「现值」是 6 天前的,是 8h 结算变量,中间约 18 个结算周期。我怀疑快照管道在 09-22 生成后 funding 字段未刷新。",
      citedIds: [
        "ev-85c701bd2407",
        "rev-c0515ec80093",
        "ev-aa12269ea516",
        "ev-d6345a1ac57d",
        "ev-905370c8fcf8",
        "ev-df9effd9f8ab",
        "ev-fea9fc354bbf",
        "hyp-5eb464687068",
      ],
    },
  ],
  rulings: [
    {
      dispute: "「今天」的语义",
      verdict:
        "正式回答必须写明「09-28 盘中不可观察,最新可观察对象是 09-27 收盘 bar」",
    },
    {
      dispute: "带 id 的行情副本能否当证据引用",
      verdict:
        "本轮仅作数字一致性校验(已通过),不作为行情事实的证据引用",
    },
    {
      dispute: "funding 滞后是「滞后」还是「缺陷」",
      verdict: "三者相容,交由反向检验裁决;先修数据再谈结论",
    },
  ],
  revision:
    "最新已收日K(09-27,截止 09-28T00:00Z)BTC 收 83381.5,日跌 1.48%,约 0.7 个 20d 日波动,属温和上行(7d +2.3%、29d +7.4%)中的普通回调日;资金费率读数滞后 6 天不可用;今日盘中与任何前向含义均不可答。",
};

/* ── ⑧ 真实回测(REAL BACKTESTS) —— 逐字取自本机 selenespace.db backtests 表 ───
 * 库里共 177 条真回测,按 symbol 去重后展示 7 条;曲线为真 equity_curve_json 降采样 56 点。
 * ⚠️ 多数是亏损(ETH 1d -78%、Sharpe -0.26)—— 如实保留,不挑赢家。
 * 军规 tile 三选一:真数据 / 定性+缺口 / 不放,没有第四种(假数字)。 */

/** 全库规模读数(与 ticker/回测墙/审计栏共用,禁止字面量散抄) */
export const SCALE_FACTS = {
  strategies: 218,
  backtests: 177,
  questions: 47,
  evidence: 13,
  interfaces: 186,
} as const;

export interface RealBacktest {
  id: string; symbol: string; interval: string; trades: number;
  /** 真 metrics_json 读数 */
  ret: number; sharpe: number; dd: number; win: number; pf: number;
  start: string; end: string;
  /** 真 equity_curve_json(降采样 56 点,归一化起点 10000) */
  curve: number[];
}

export const REAL_BACKTESTS: RealBacktest[] = [
  {
    id: "bt-975cfe0d", symbol: "ETH/USDT", interval: "1d", trades: 396,
    ret: -78.0, sharpe: -0.26, dd: -94.2, win: 46.0, pf: 0.97,
    start: "2020-06-06", end: "2026-09-22",
    curve: [10000.0,10000.0,10000.0,10000.0,10755.2,11740.2,15398.1,17163.5,18207.6,23073.6,22269.0,24068.4,23675.2,26116.3,26341.6,20844.9,18383.5,17880.9,8626.7,10154.3,12193.9,9824.0,8797.9,9025.2,10110.0,9580.3,10884.2,11581.5,12121.9,10099.8,10275.6,10508.6,10631.7,9795.1,9101.4,7479.9,9805.4,8769.2,6977.4,7404.6,7968.0,6725.1,5556.7,3821.6,4804.3,4624.2,4520.8,4283.0,4200.2,3329.0,3082.0,2471.8,2611.9,2369.1,1892.8,2013.4],
  },
  {
    id: "bt-581e359a", symbol: "ARB/USDT", interval: "1h", trades: 266,
    ret: 60.3, sharpe: 1.63, dd: -22.0, win: 36.0, pf: 1.4,
    start: "2025-09-18", end: "2026-09-19",
    curve: [10000.0,10000.0,10111.8,9640.9,9951.3,10185.4,9840.1,9970.4,10254.3,10219.4,10472.8,10242.1,10633.7,9968.7,10224.5,10149.4,10152.7,9862.7,9264.6,9824.9,9473.6,9813.4,10090.1,10097.2,10581.8,10796.4,10288.4,10139.1,10379.0,10929.5,11008.7,11047.1,11570.5,11526.7,11281.9,11154.7,11384.2,11856.9,11555.8,10952.5,10239.3,10155.7,9581.9,9768.6,9699.1,10217.4,10520.6,10286.0,10490.4,9983.1,9942.3,9893.1,11054.8,11444.8,13947.8,14449.6],
  },
  {
    id: "bt-6fe1afeb", symbol: "PENGU/USDT", interval: "1h", trades: 230,
    ret: -8.8, sharpe: -0.17, dd: -28.8, win: 44.0, pf: 0.93,
    start: "2025-09-18", end: "2026-09-18",
    curve: [10000.0,10000.0,10144.4,9979.9,9492.9,9637.2,9088.5,9101.2,9402.2,9318.2,8898.6,8492.8,9369.2,9522.4,8772.5,8896.6,8695.9,9852.5,9816.8,9473.1,9616.7,9081.3,9799.4,9906.4,9752.3,10003.2,9626.5,9672.2,9722.0,9397.8,9184.5,9046.0,8125.4,8015.6,8248.8,8272.5,8114.8,8187.5,7960.9,7586.7,8035.3,8085.2,8106.3,8518.5,8415.8,8438.7,8892.9,8830.0,9353.3,9243.1,8765.6,9059.7,9451.8,9512.0,9195.3,8988.4],
  },
  {
    id: "bt-420a5839", symbol: "XLM/USDT", interval: "1h", trades: 131,
    ret: -11.0, sharpe: -0.96, dd: -25.3, win: 15.0, pf: 0.16,
    start: "2026-06-23", end: "2026-09-23",
    curve: [10000.0,10000.0,10000.0,10000.0,10000.0,10000.0,10000.0,10000.0,10000.0,10479.1,10166.8,10092.1,10260.1,10104.6,10149.3,9850.2,9698.5,9602.8,9512.6,9722.1,9728.6,9567.0,9348.7,9474.0,9192.1,9237.7,9295.7,9181.4,9170.4,9265.8,9108.6,9142.2,9217.1,9245.1,9175.1,9753.2,10606.9,10778.0,10796.1,10882.2,10781.8,10870.6,10957.5,10962.2,10581.6,10356.1,10435.8,10291.8,10376.2,9953.2,10076.4,9805.8,9068.7,8894.7,8776.9,9027.9],
  },
  {
    id: "bt-e088c693", symbol: "BTC/USDT", interval: "4h", trades: 65,
    ret: -16.1, sharpe: -1.86, dd: -29.2, win: 26.0, pf: 0.7,
    start: "2025-10-01", end: "2026-05-21",
    curve: [10000.0,10000.0,9873.8,9811.8,9601.3,9466.8,9308.3,9398.1,9201.2,9201.2,9345.2,8926.5,8926.5,8939.2,9063.6,8883.0,8912.5,8937.3,8473.2,8197.1,8276.5,7948.1,7878.3,8084.0,8142.8,8065.8,8526.7,8364.4,8337.0,8231.4,8231.4,8231.4,7674.4,7794.3,7561.7,7505.8,7528.5,7151.3,7558.6,7735.9,7881.8,7887.5,7924.3,7727.8,7703.6,7419.2,7814.0,8030.2,8366.5,8334.0,8329.1,8086.0,8366.7,8608.5,8583.3,8314.9],
  },
  {
    id: "bt-db24649a", symbol: "BTC/USDT", interval: "1h", trades: 60,
    ret: -20.6, sharpe: -0.69, dd: -36.8, win: 33.0, pf: 0.74,
    start: "2025-08-05", end: "2026-09-29",
    curve: [10000.0,10151.0,9625.7,9370.1,9264.9,9448.2,9426.8,8896.9,9855.8,9452.0,9266.7,9401.3,9082.7,9194.5,8456.3,8225.6,8448.9,8508.8,7904.0,7856.6,7919.2,7929.8,8178.1,7548.7,7162.8,7104.9,6814.0,6724.0,6958.0,7234.7,7344.7,7316.5,7056.7,7591.5,8056.2,8090.7,8199.6,8425.1,8179.8,8044.9,7713.9,7831.9,8050.8,7976.9,7403.5,7705.7,7973.2,8070.2,7735.0,7807.1,7492.4,7927.8,7967.1,8036.3,7980.8,8076.6],
  },
  {
    id: "bt-dd3c67d8", symbol: "ETH/USDT", interval: "4h", trades: 34,
    ret: 36.0, sharpe: 1.13, dd: -17.9, win: 74.0, pf: 3.89,
    start: "2025-06-14", end: "2026-09-23",
    curve: [10000.0,10000.0,10000.0,10000.0,10000.0,10000.0,9820.4,10332.8,10332.8,10670.1,10655.7,10655.7,10655.7,10193.6,10193.6,10627.8,10627.8,10526.9,10832.6,10832.6,11164.9,12010.6,11976.0,12736.2,12736.2,12736.2,12751.6,12118.3,11060.4,11790.5,11790.5,11790.5,12279.6,12668.3,12627.6,12851.3,12851.3,12851.3,13327.3,13327.3,13327.3,13099.5,13275.5,12568.8,12971.3,12971.3,13242.9,13242.9,13388.4,13395.2,13626.7,13599.3,13599.3,13599.3,13599.3,13599.3],
  },
];
