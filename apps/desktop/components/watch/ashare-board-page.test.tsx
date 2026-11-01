import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

// === mock useApi =================================
const mockApiData: Record<string, unknown> = {};
// 记录每个端点拿到的轮询档位 —— 闸门传错是静默的,只有这里能看出来。
const mockApiOpts: Record<string, { refreshInterval?: number } | undefined> = {};
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string, opts?: { refreshInterval?: number }) => {
    mockApiOpts[url] = opts;
    return { data: mockApiData[url] ?? null, loading: false, error: null };
  },
}));

// === mock session gate ============================
// 页面原本自己调 /ashare/session;这里把闸门提出来做成可切换的,用于验证
// 「盘中轮询 / 收盘停轮询」这条接线本身。
const mockSession = { open: false };
vi.mock("@/lib/hooks/use-ashare-session", () => ({
  useAshareSession: () => ({
    data: { as_of: "2026-10-21T10:00:00+08:00", phase: "OPEN", polling: mockSession.open, is_trading_day: true, status: "ok" },
    phase: "OPEN",
    sessionOpen: mockSession.open,
    unknown: false,
    error: null,
  }),
  useRevalidateAshareOnOpen: () => {},
}));

// 新两块(大盘总览/异动偏离)走 useApiPage(distinguish),与存量的 useApi 不同源:
// 它们的 error 通道是真值表位(服务挂 vs 真无数据)。同表驱动,避免两套 mock。
const mockApiErrors: Record<string, { message: string; status: number } | undefined> = {};
vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: (url: string, opts?: { refreshInterval?: number }) => {
      mockApiOpts[url] = opts;
      return {
        data: mockApiData[url] ?? null,
        error: mockApiErrors[url],
        isLoading: false,
        mutate: async () => {},
      };
    },
  }),
}));

// === mock card primitives (avoid heavy deps) =====
vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>
      {right != null ? <span data-testid={`right-${title}`}>{right}</span> : null}
      {children}
    </div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

// === import AFTER mocks =================================
import { AshareBoardPage, pollIntervals } from "./ashare-board-page";

const URLS = {
  pool: "/api/v1/ashare/limit-up-pool",
  ladder: "/api/v1/ashare/ladder",
  phase: "/api/v1/ashare/phase",
  concept: "/api/v1/ashare/boards?kind=concept",
  industry: "/api/v1/ashare/boards?kind=industry",
  northbound: "/api/v1/ashare/northbound",
  rotation: "/api/v1/ashare/rotation?days=12",
  mainline: "/api/v1/ashare/mainline?days=20",
  conceptRps: "/api/v1/ashare/concept-rps?lookback=120",
  overview: "/api/v1/ashare/overview",
  deviation: "/api/v1/ashare/deviation?live=true",
};
// 龙虎榜 URL 带当日日期(组件用 todayStr()),测试同步算出同一 key。
const lhbUrl = () =>
  `/api/v1/ashare/lhb?date=${new Date().toISOString().slice(0, 10).replace(/-/g, "")}`;

function populatedApi() {
  mockApiData[URLS.pool] = {
    status: "ok",
    date: "20241101",
    pool_count: 1,
    stats: { limit_up_today: 42, seal_rate: 0.81, open_count: 7, limit_down_today: 3 },
    pool: [{ code: "600519", name: "贵州茅台", change_rate: 10.0 }],
  };
  mockApiData[URLS.ladder] = {
    status: "ok",
    date: "20241101",
    history: [],
    history_count: 0,
    today: { max_consecutive: 6, first_board: 12, ge2_count: 5, ge3_count: 2 },
    sealed: {
      real_up: 58, fake_up: 2, real_down: 11, fake_down: 4,
      undecided: 3, ready: true,
    },
    sealed_reason: null,
  };
  mockApiData[URLS.phase] = {
    status: "ok",
    as_of: "20241031",
    current: { phase: "climax", max_consecutive: 6, ge2_count: 5, promo_rate: 0.35, seal_rate: 0.72 },
    series: [],
  };
  mockApiData[URLS.concept] = {
    status: "ok",
    kind: "concept",
    count: 1,
    boards: [{ rank: 1, concept: "AI算力", change_pct: 3.2, up_count: 40, down_count: 5, leader: "中际旭创" }],
  };
  mockApiData[lhbUrl()] = [
    { code: "300750", name: "宁德时代", reason: "日涨幅偏离值达7%" },
  ];
  mockApiData[URLS.northbound] = [
    { date: "20240816", 当日成交净买额: 1234567890, 买入成交额: 2000000000 },
  ];
  mockApiData[URLS.rotation] = {
    status: "ok",
    reason: null,
    days: 12,
    membership_note: "概念成分为当前快照",
    dates: ["2026-10-16", "2026-10-15"],
    columns: {
      "2026-10-16": [["AI算力", 5.22], ["机器人", -1.4]],
      "2026-10-15": [["AI算力", 2.1], ["机器人", 0.8]],
    },
    concept_count: 2,
    as_of: "2026-10-16",
  };
  mockApiData[URLS.mainline] = {
    status: "ok",
    reason: null,
    days: 20,
    streak_capped: true,
    membership_note: "概念成分为当前快照",
    dates: ["2026-10-16"],
    columns: {
      "2026-10-16": [["AI算力", 100.0, 7, 4, "600001.SH"]],
    },
    as_of: "2026-10-16",
  };
  mockApiData[URLS.conceptRps] = {
    status: "ok",
    reason: null,
    lookback: 120,
    as_of: "2026-10-16",
    membership_note: "概念成分为当前快照",
    drift_note: "概念 RPS 用当前成分快照回放历史; thin(3-7 只)的排名不可用于决策。",
    concept_count: 1179,
    concepts: [
      { concept: "印制电路板", rps: 99.9, ret: 24.88, member_count: 46, stability: "solid", is_strong: true },
      { concept: "PCB铜箔", rps: 99.8, ret: 24.18, member_count: 8, stability: "fair", is_strong: false },
      { concept: "超硬材料", rps: 100.0, ret: 35.43, member_count: 4, stability: "thin", is_strong: false },
    ],
  };
  mockApiData[URLS.overview] = overviewPayload();
  mockApiData[URLS.deviation] = deviationPayload();
}

/** /ashare/overview —— 全部百分比都是**百分数**(1.23 = +1.23%)。 */
function overviewPayload(over: Record<string, unknown> = {}) {
  return {
    status: "ok",
    reason: null,
    as_of: "2026-10-20",
    adjust: "qfq",
    quote_source: "snapshot",
    coverage: {
      quoted: 5574, universe: 5574, ratio: 1.0,
      baseline_symbols: 5574, baseline_as_of: "2026-10-20",
      baseline_sessions: 120, amount_available: true,
    },
    indices: [
      {
        symbol: "000001.SH", name: "上证指数", last_price: 3210.55, prev_close: 3190.1,
        change_amount: 20.45, change_pct: 0.64, high: 3220, low: 3180,
        amount: 5.2e11, date: "2026-10-20", time: "15:00:00",
      },
    ],
    indices_reason: null,
    breadth: {
      total: 5574, up: 3000, down: 2400, flat: 174,
      up_pct: 53.8, down_pct: 43.1, avg_pct: 0.42, median_pct: 0.15,
      strong_up: 120, strong_down: 40,
    },
    amount: { total: 1.12e12, available: true },
    boards: [],
    limit: {
      limit_up: 42, broken: 7, limit_down: 3, max_boards: 6, seal_rate: 0.81,
      sealed_ready: true, sealed_reason: null, fake_up: 2, fake_down: 1,
    },
    distribution: [],
    trend: {
      above_ma5: 3100, above_ma20: 2800, above_ma60: 2500,
      above_ma5_pct: 55.6, above_ma20_pct: 50.2, above_ma60_pct: 44.8,
      new_high: 88, new_low: 12,
    },
    activity: { avg_vol_ratio: 1.23, high_vol_count: 500, high_vol_pct: 9.0, vol_ratio_coverage: 0.98 },
    radar: [
      { key: "index", label: "指数", value: 62 },
      { key: "profit", label: "赚钱", value: 58 },
      { key: "volume", label: "量能", value: 71 },
      { key: "spec", label: "投机", value: 44 },
      { key: "defend", label: "抗跌", value: 55 },
      { key: "main", label: "主线", value: 66 },
    ],
    emotion: { score: 61, label: "偏暖" },
    top_gainers: [], top_losers: [], amount_leaders: [], vol_ratio_leaders: [],
    concept_rank: { leading: [], lagging: [], basis: "board_index" },
    industry_rank: { leading: [], lagging: [], basis: "board_index" },
    snapshot_reason: null, rank_reason: null, requested_date: null,
    summary: "上证指数 +0.64% | 情绪61(偏暖) | 涨停42 | 成交 11200 亿",
    ...over,
  };
}

/** /ashare/deviation —— value/threshold/closeness 全是**小数**(0.20 = 20%)。 */
function deviationPayload(over: Record<string, unknown> = {}) {
  return {
    status: "ok",
    as_of: "20261020",
    live: true,
    counts: { triggered: 1, edge: 1, watch: 1 },
    rows: [
      {
        symbol: "600519.SH", board: "main", st: false, bench: "000001.SH",
        basis: "spot", basis_date: "20261020", intraday: true,
        max_closeness: 1.05, status: "triggered",
        windows: {
          "3d": { value: 0.215, threshold: 0.2, closeness: 1.05 },
          "10d": { value: 0.9, threshold: 1.0, closeness: 0.9 },
          "30d": { value: 1.2, threshold: 2.0, closeness: 0.6 },
        },
      },
      {
        symbol: "300750.SZ", board: "cyb", st: false, bench: "399006.SZ",
        basis: "spot", basis_date: "20261020", intraday: false,
        max_closeness: 0.72, status: "edge",
        windows: {
          "3d": { value: -0.19, threshold: 0.3, closeness: 0.63 },
          "10d": { value: -0.72, threshold: 1.0, closeness: 0.72 },
        },
      },
      {
        symbol: "000001.SZ", board: "main", st: true, bench: "000001.SH",
        basis: "astock", basis_date: "20261017", intraday: false,
        max_closeness: 0.21, status: "watch",
        windows: { "3d": { value: 0.05, threshold: 0.2, closeness: 0.25 } },
      },
    ],
    skipped: [],
    ...over,
  };
}

describe("AshareBoardPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
    Object.keys(mockApiErrors).forEach((k) => delete mockApiErrors[k]);
    Object.keys(mockApiOpts).forEach((k) => delete mockApiOpts[k]);
    // 存量用例都按"收盘"跑(0 轮询),轮询组自己把闸门打开。
    mockSession.open = false;
  });

  it("renders all seven blocks with populated rows", () => {
    populatedApi();
    render(<AshareBoardPage />);

    // 情绪周期状态条:阶段中文 + "截至 面板最后一日"
    expect(screen.getByTestId("card-情绪周期(连板梯队六阶段)")).toBeTruthy();
    expect(screen.getByText("高潮")).toBeTruthy();
    expect(screen.getByText("截至 20241031")).toBeTruthy();

    // 涨停概览 (label 唯一) + 连板梯队 / 状态条 (最高连板/二板以上 各出现两处 → getAll)
    expect(screen.getByTestId("stat-今日涨停").textContent).toBe("42");
    expect(screen.getAllByTestId("stat-最高连板")[0].textContent).toBe("6");
    expect(screen.getAllByTestId("stat-二板以上")[0].textContent).toBe("5");

    // 真封/假封 拆分:五档免费带出的封板可判性,与「今日涨停」并列展示
    expect(screen.getByTestId("stat-真封(涨)").textContent).toBe("58");
    expect(screen.getByTestId("stat-假封(涨)").textContent).toBe("2");
    expect(screen.getByTestId("stat-真封(跌)").textContent).toBe("11");
    // 跌停侧的假封必须与真封分开渲染 —— 后端算了 fake_down,少渲染一格就是
    // "产出但没人读";这里用非零值断言,零值会让断言空过。
    expect(screen.getByTestId("stat-假封(跌)").textContent).toBe("4");
    expect(screen.getByTestId("stat-盘口待定").textContent).toBe("3");

    // 涨停池 / 板块排名 —— 概念名同时出现在板块排名、轮动矩阵、主线三块里,
    // 所以按卡片作用域断言,而不是裸 getByText(会撞多重匹配,也分不清是哪块渲染的)。
    expect(screen.getByText("贵州茅台")).toBeTruthy();
    expect(
      within(screen.getByTestId("card-板块排名")).getByText("AI算力"),
    ).toBeTruthy();
    expect(screen.getByText("中际旭创")).toBeTruthy();

    // 深度面新增两块:龙虎榜 / 北向(市场面,来自 ashare_deep 端点)
    expect(screen.getByTestId("card-龙虎榜")).toBeTruthy();
    expect(screen.getByTestId("card-北向资金 (每日)")).toBeTruthy();
    expect(screen.getByText("日涨幅偏离值达7%")).toBeTruthy();
    // 北向空行默认被过滤,只显示有净买额的行 → 尾部日期作标题右标
    expect(screen.getByText(/最近 20240816/)).toBeTruthy();

    // 概念轮动 / 主线识别 两块(新增)
    expect(screen.getByTestId("card-概念轮动(近 N 日等权涨幅)")).toBeTruthy();
    expect(screen.getByTestId("card-主线识别(涨停梯队强度)")).toBeTruthy();
    // 轮动与主线两块都以「截至 2026-10-16」标注 as_of —— 两处并不同义,
    // 故按卡片分别断言,而不是裸 getByText(它是 2 重匹配)。
    expect(
      within(screen.getByTestId("card-概念轮动(近 N 日等权涨幅)")).getByText("截至 2026-10-16"),
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("card-主线识别(涨停梯队强度)")).getByText("截至 2026-10-16"),
    ).toBeTruthy();
    // 轮动矩阵:两天的 AI算力 涨幅都渲染(5.22 / 2.1)。用作用域断言,
    // 否则这条测试在矩阵整块不渲染、只有别的块碰巧含同样数字时也会绿。
    const rotationCard = screen.getByTestId("card-概念轮动(近 N 日等权涨幅)");
    expect(within(rotationCard).getByText("5.22")).toBeTruthy();
    expect(within(rotationCard).getByText("2.1")).toBeTruthy();
    expect(within(rotationCard).getByText("机器人")).toBeTruthy();
    // 主线一行:强度 100、涨停 7、最高板 4、领涨股 —— 同样按卡片作用域。
    const mainlineCard = screen.getByTestId("card-主线识别(涨停梯队强度)");
    expect(within(mainlineCard).getByText("600001.SH")).toBeTruthy();
    expect(within(mainlineCard).getByText("100")).toBeTruthy();
    expect(within(mainlineCard).getByText("7")).toBeTruthy();
    // 成分快照的口径限制必须对人可见 —— 两块各自渲染一份 note,
    // 否则回看历史会把当期归属误读成当时归属。按卡片作用域断言。
    expect(within(rotationCard).getByText(/概念成分为当前快照/)).toBeTruthy();
    expect(within(mainlineCard).getByText(/概念成分为当前快照/)).toBeTruthy();

    // 概念 RPS 块 —— 三档稳定性各一行,三行都用完全部字段。
    const rpsCard = screen.getByTestId("card-概念 RPS(近 N 日等权涨幅百分位)");
    expect(within(rpsCard).getByText("印制电路板")).toBeTruthy();
    expect(within(rpsCard).getByText("99.9")).toBeTruthy();
    expect(within(rpsCard).getByText("24.88")).toBeTruthy();
    expect(within(rpsCard).getByText("46")).toBeTruthy();
    expect(within(rpsCard).getByText("稳定")).toBeTruthy();
    expect(within(rpsCard).getByText("一般")).toBeTruthy();
    // thin 档是「灰显 + 『不稳定』角标」,不是文字化的「不稳」。
    expect(within(rpsCard).getByTestId("stability-thin-badge").textContent).toBe("不稳定");
    // drift_note 必须是可见正文,不是 tooltip —— 它解释了为什么榜首常是 thin。
    expect(
      within(rpsCard).getByTestId("concept-rps-footnote").textContent,
    ).toContain("thin(3-7 只)的排名不可用于决策");
    // concept_count 渲染了,读者据此判断"合格集"的规模。
    expect(within(rpsCard).getByText(/1179 个概念入选/)).toBeTruthy();
    expect(within(rpsCard).getByText(/120 个交易日/)).toBeTruthy();
  });

  it("highlights only server-declared strong rows, never rps alone", () => {
    // 这一条是本块最要紧的契约:超硬材料 rps=100 但只有 4 只成员(thin),
    // 服务端给了 is_strong=false。前端若自己按 rps>=90 复算,就会把高亮打给
    // 一个排名不稳的概念 —— 顺手把 99.8 的 fair 行也一起验了。
    populatedApi();
    render(<AshareBoardPage />);
    const rpsCard = screen.getByTestId("card-概念 RPS(近 N 日等权涨幅百分位)");
    const strongMark = () => within(rpsCard).queryAllByText("强");

    // 恰好一行带"强"标记,且是 solid 的那一行。
    expect(strongMark()).toHaveLength(1);
    const strongRow = within(rpsCard).getByText("印制电路板").closest("tr")!;
    expect(strongRow.dataset.strong).toBe("true");
    expect(strongRow.textContent).toContain("强");

    // rps 更高、但稳定性不够的两行都不得高亮。
    for (const [name, rps] of [["超硬材料", "100"], ["PCB铜箔", "99.8"]] as const) {
      expect(rps).toBeTruthy();
      const row = within(rpsCard).getByText(name).closest("tr")!;
      expect(row.dataset.strong).toBe("false");
      expect(row.textContent).not.toContain("强");
    }

    // 高亮不只是"多一个字":整行要有强调样式,否则 11px 字号下读者看不出来。
    expect(strongRow.className).toContain("text-gold");
    const weakRow = within(rpsCard).getByText("超硬材料").closest("tr")!;
    expect(weakRow.className).not.toContain("text-gold");
  });

  it("gives the three stability bands visibly different treatment", () => {
    // 三档若只差文字而同色,读者扫一眼仍会把 thin 的 100 分当成强信号。
    // 断言的是**样式差异本身**:solid 用正文色,thin 灰显且**不得**用金色
    // —— 金色是 is_strong 的专用强调色,给 thin 会把"排名不稳"读成"值得看"。
    populatedApi();
    render(<AshareBoardPage />);
    const rpsCard = screen.getByTestId("card-概念 RPS(近 N 日等权涨幅百分位)");
    const exact = within(rpsCard).getByTestId("stability-thin-badge");
    const thinCell = exact.closest("td")!;
    const solidCell = within(rpsCard).getByText("稳定").closest("td")!;

    expect(exact.textContent).toBe("不稳定");
    expect(thinCell.className).toContain("text-ink-4");
    expect(thinCell.className).not.toContain("text-gold");
    expect(solidCell.className).toContain("text-ink");
    expect(thinCell.className).not.toBe(solidCell.className);
  });

  it("renders no row and a placeholder when there are no eligible concepts", () => {
    mockApiData[URLS.conceptRps] = {
      status: "ok", reason: null, lookback: 120, as_of: null,
      membership_note: "n", drift_note: "d", concept_count: 0, concepts: [],
    };
    render(<AshareBoardPage />);
    expect(screen.getByText(/暂无概念 RPS 数据/)).toBeTruthy();
  });

  it("labels a degraded concept-rps block as unavailable", () => {
    mockApiData[URLS.conceptRps] = {
      status: "degraded", reason: "kline_manager_unavailable", lookback: 120,
      concepts: [], concept_count: 0, as_of: null,
      membership_note: "n", drift_note: "d",
    };
    render(<AshareBoardPage />);
    expect(screen.getByText(/暂不可用.*kline_manager_unavailable/)).toBeTruthy();
    expect(screen.queryByText(/暂无概念 RPS 数据/)).toBeNull();
  });

  it("paints rotation gains red and losses green (A-share convention)", () => {
    populatedApi();
    render(<AshareBoardPage />);
    expect(screen.getByText("5.22").className).toContain("text-red-400");
    expect(screen.getByText("-1.4").className).toContain("text-emerald-400");
  });

  it("keeps rotation rows when mainline is unavailable (per-block fail-open)", () => {
    // 两块各用一个端点:主线断线不能把轮动一起拖成"暂无数据"。
    populatedApi();
    delete mockApiData[URLS.mainline];
    render(<AshareBoardPage />);
    expect(screen.getByText(/暂无主线数据/)).toBeTruthy();
    expect(
      within(screen.getByTestId("card-概念轮动(近 N 日等权涨幅)")).getByText("5.22"),
    ).toBeTruthy();
  });

  it("shows empty placeholders when APIs return nothing", () => {
    render(<AshareBoardPage />);
    expect(screen.getByText(/暂无情绪周期数据/)).toBeTruthy();
    expect(screen.getByText(/暂无连板数据/)).toBeTruthy();
    expect(screen.getByText(/暂无涨停池数据/)).toBeTruthy();
    expect(screen.getByText(/暂无概念板块数据/)).toBeTruthy();
    expect(screen.getByText(/暂无该日龙虎榜/)).toBeTruthy();
    expect(screen.getByText(/暂无北向资金数据/)).toBeTruthy();
    expect(screen.getByText(/暂无概念轮动数据/)).toBeTruthy();
    expect(screen.getByText(/暂无主线数据/)).toBeTruthy();
  });

  it("switches board kind to industry on toggle", () => {
    populatedApi();
    mockApiData[URLS.industry] = {
      status: "ok",
      kind: "industry",
      count: 1,
      boards: [{ rank: 1, industry: "半导体", change_pct: -1.1 }],
    };
    render(<AshareBoardPage />);

    fireEvent.click(screen.getByRole("button", { name: "行业" }));
    expect(screen.getByText("半导体")).toBeTruthy();
  });

  it("labels a degraded rotation as unavailable, not as an empty market", () => {
    // degraded = 上游断了;empty = 市场确实没动。两者在页面上必须可区分,
    // 否则成分源挂掉会被读成"今天没有热点"。
    mockApiData[URLS.rotation] = {
      status: "degraded", reason: "concept_membership_unavailable",
      days: 12, membership_note: "n", dates: [], columns: {},
      concept_count: 0, as_of: null,
    };
    render(<AshareBoardPage />);
    expect(screen.getByText(/暂不可用.*concept_membership_unavailable/)).toBeTruthy();
    expect(screen.queryByText(/暂无概念轮动数据/)).toBeNull();
  });

  it("labels a degraded phase as unavailable with the panel hint", () => {
    mockApiData[URLS.phase] = { status: "degraded", reason: "panel_missing" };
    render(<AshareBoardPage />);
    expect(screen.getByText(/暂不可用.*panel_missing/)).toBeTruthy();
  });

  it("marks an intraday live phase as provisional, not as a settled close", () => {
    // 实时档:as_of 是自然今日(未收盘)。若渲染成"截至 今日",会被读成收盘定版。
    populatedApi();
    mockApiData[URLS.phase] = {
      status: "ok",
      source: "live_snapshot",
      label_scope: "intraday_provisional",
      as_of: "20261015",
      current: { phase: "ignite", max_consecutive: 5, ge2_count: 6, promo_rate: 0.17, seal_rate: 0.6 },
      series: [],
    };
    render(<AshareBoardPage />);
    expect(screen.getByText("盘中暂定 · 20261015")).toBeTruthy();
    expect(screen.queryByText("截至 20261015")).toBeNull();
  });

  it("keeps the settled wording for a backfill-panel phase", () => {
    // 回填档:as_of 是面板最后一日,是收盘口径 —— 必须仍标"截至 X 日"。
    populatedApi();
    mockApiData[URLS.phase] = {
      status: "ok",
      source: "backfill_panel",
      as_of: "20241031",
      current: { phase: "climax", max_consecutive: 6, ge2_count: 5, promo_rate: 0.35, seal_rate: 0.72 },
      series: [],
    };
    render(<AshareBoardPage />);
    expect(screen.getByText("截至 20241031")).toBeTruthy();
    expect(screen.queryByText(/盘中暂定/)).toBeNull();
  });

  it("renders — not 0 when the book is unavailable", () => {
    // ready=false 表示全市场盘口都取不到 —— 渲染 0 会被读成"一只真封都没有",
    // 与真实的"全炸板"在页面上无法区分。必须是 "—"。
    populatedApi();
    mockApiData[URLS.ladder] = {
      status: "ok",
      date: "20241101",
      history: [],
      history_count: 0,
      today: { max_consecutive: 6, first_board: 12, ge2_count: 5, ge3_count: 2 },
      sealed: { real_up: 0, fake_up: 0, real_down: 0, fake_down: 0, undecided: 0, ready: false },
      sealed_reason: "snapshot_empty",
    };
    render(<AshareBoardPage />);
    expect(screen.getByTestId("stat-真封(涨)").textContent).toBe("—");
    expect(screen.getByTestId("stat-假封(涨)").textContent).toBe("—");
    expect(screen.getByTestId("stat-真封(跌)").textContent).toBe("—");
    expect(screen.getByTestId("stat-假封(跌)").textContent).toBe("—");
    expect(screen.getByTestId("stat-盘口待定").textContent).toBe("—");
  });

  it("renders — when the ladder payload omits sealed entirely", () => {
    // 旧版后端(无 sealed 字段)也必须降级成 "—",不能崩、不能变 0。
    populatedApi();
    mockApiData[URLS.ladder] = {
      status: "ok",
      date: "20241101",
      history: [],
      history_count: 0,
      today: { max_consecutive: 6, first_board: 12, ge2_count: 5, ge3_count: 2 },
    };
    render(<AshareBoardPage />);
    expect(screen.getByTestId("stat-真封(涨)").textContent).toBe("—");
    expect(screen.getByTestId("stat-假封(跌)").textContent).toBe("—");
    expect(screen.getByTestId("stat-盘口待定").textContent).toBe("—");
  });

  it("shows real 0 as 0 when the book is ready and genuinely empty", () => {
    // 反向:ready=true 且确实是 0 时,必须显示 0(不能一律显示 —)。
    populatedApi();
    mockApiData[URLS.ladder] = {
      status: "ok",
      date: "20241101",
      history: [],
      history_count: 0,
      today: { max_consecutive: 6, first_board: 12, ge2_count: 5, ge3_count: 2 },
      sealed: { real_up: 0, fake_up: 0, real_down: 0, fake_down: 0, undecided: 0, ready: true },
      sealed_reason: null,
    };
    render(<AshareBoardPage />);
    expect(screen.getByTestId("stat-真封(涨)").textContent).toBe("0");
  });

  // === 大盘总览带(B4-①) =================================
  // 这一块的值有两个最容易犯的错:①情绪读数 null 被渲染成 0;②radar 空时
  // 仍画一幅"全 0 轴"的雷达图。两者都会把"没数据"说成一个结论。

  it("renders the overview band with indices, breadth and emotion", () => {
    populatedApi();
    render(<AshareBoardPage />);
    const band = screen.getByTestId("ashare-overview-band");

    // 指数名是服务端下发的中文,走 tr(变量) —— 裸渲染下原样显示。
    expect(within(band).getByText("上证指数")).toBeTruthy();
    expect(within(band).getByText("3210.55")).toBeTruthy();
    // 百分数口径(1.23 = +1.23%),不是小数。
    expect(within(band).getByText("+0.64%")).toBeTruthy();
    // 情绪读数 + 标签
    expect(screen.getByTestId("overview-emotion-score").textContent).toBe("61");
    expect(within(band).getByText("偏暖")).toBeTruthy();
    // 雷达图作为可访问图像渲染,标签在 aria-label 里
    expect(within(band).getByRole("img", { name: /情绪雷达/ })).toBeTruthy();
    // 广度条形图同样有 aria-label(纯视觉条不能是唯一载体)
    expect(within(band).getByRole("img", { name: /涨跌家数/ })).toBeTruthy();
    // 成交额 1.12e12 → 万亿量级
    expect(within(band).getByText("1.12 万亿")).toBeTruthy();
    // 时标:大盘总览的 as_of 必须自己落在本 band 里。变异实测过 ——
    // 删掉这一行,把 band 的 asOf 改成 null 也不会有任何测试变红。
    expect(within(band).getByText("截至 2026-10-20")).toBeTruthy();
  });

  it("renders — and no radar when the cross-section is empty", () => {
    // 实质契约:score === null 是「截面为空」,不是 0 分。零轴平均会造出假温度。
    populatedApi();
    mockApiData[URLS.overview] = overviewPayload({
      status: "empty",
      radar: [],
      emotion: { score: null, label: "无数据" },
      indices: [],
      amount: { total: null, available: false },
    });
    render(<AshareBoardPage />);

    expect(screen.getByTestId("overview-emotion-score").textContent).toBe("—");
    expect(screen.queryByRole("img", { name: /情绪雷达/ })).toBeNull();
    expect(screen.getByText("截面为空,不出情绪读数")).toBeTruthy();
    // 空截面必须说明"这不代表中性",否则会被读成一个中性判断
    expect(screen.getByTestId("overview-notes").textContent).toContain("不代表「中性」");
    expect(screen.getByText("暂无指数行情")).toBeTruthy();
    // 成交额不可得 → 「—」,不是 0。限定在总览带内:选股面板的表头也有
    // 「成交额」三个字,不限定会撞到两个元素。
    const band = screen.getByTestId("ashare-overview-band");
    const amountRow = within(band).getByText("成交额").parentElement!;
    expect(amountRow.textContent).toContain("—");
  });

  it("renders a genuine 0 emotion score as 0, not as missing", () => {
    // 反向:score 真的是 0 时必须显示 0(不能一律显示 —)。
    populatedApi();
    mockApiData[URLS.overview] = overviewPayload({ emotion: { score: 0, label: "冰点" } });
    render(<AshareBoardPage />);
    expect(screen.getByTestId("overview-emotion-score").textContent).toBe("0");
  });

  it("surfaces the daily-fallback degradation as visible prose", () => {
    // 降级不能只藏在徽标里 —— 读者要先知道"这页数按昨收口径"。
    populatedApi();
    mockApiData[URLS.overview] = overviewPayload({ quote_source: "daily" });
    render(<AshareBoardPage />);
    expect(screen.getByTestId("overview-notes").textContent).toContain("行情来自日线回退");
    expect(screen.getByText("日线回退")).toBeTruthy();
  });

  it("surfaces partial coverage with the quoted/universe counts", () => {
    populatedApi();
    mockApiData[URLS.overview] = overviewPayload({
      status: "degraded",
      reason: "partial_coverage",
      coverage: {
        quoted: 3000, universe: 5574, ratio: 0.5382,
        baseline_symbols: 5574, baseline_as_of: "2026-10-20",
        baseline_sessions: 120, amount_available: true,
      },
    });
    render(<AshareBoardPage />);
    const notes = screen.getByTestId("overview-notes").textContent!;
    expect(notes).toContain("截面覆盖不足");
    expect(notes).toContain("3000/5574");
    // ratio 是小数,展示时 ×100
    expect(notes).toContain("53.8%");
  });

  it("shows a server-down state for the overview, distinct from no data", () => {
    // useApiPage(distinguish) 的价值就在这里:error 有值 = 服务挂了,不是"今天没数据"。
    populatedApi();
    mockApiErrors[URLS.overview] = { message: "HTTP 502 Bad Gateway", status: 502 };
    render(<AshareBoardPage />);
    expect(screen.getByTestId("ashare-overview-band").textContent).toContain("大盘总览不可用");
    expect(screen.getByTestId("ashare-overview-band").textContent).toContain("HTTP 502");
    expect(screen.queryByText("暂无大盘总览数据")).toBeNull();
  });

  it("never auto-starts the recap stream", () => {
    // 一次复盘是真实 LLM 调用 —— 进页面就跑等于替用户花钱。
    populatedApi();
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<AshareBoardPage />);
    expect(screen.getByTestId("ashare-recap-panel")).toBeTruthy();
    expect(screen.getByRole("button", { name: "生成复盘" })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  // === 异动偏离(B2) ======================================

  it("renders deviation rows with fractions converted to percent", () => {
    // 单位契约:value/threshold/closeness 是**小数**,展示要 ×100。
    // 这条若写成 (v).toFixed,0.215 会显示成 "0.22%" —— 差 100 倍。
    populatedApi();
    render(<AshareBoardPage />);
    const panel = screen.getByTestId("ashare-deviation-panel");

    expect(within(panel).getByText("600519.SH")).toBeTruthy();
    // 接近度 1.05 → 105%(0 位小数)
    expect(within(panel).getByText("105%")).toBeTruthy();
    // 偏离值 0.215 → +21.50%(2 位小数)
    expect(within(panel).getByText("+21.50%")).toBeTruthy();
    // 负偏离 −0.19 → -19.00%
    expect(within(panel).getByText("-19.00%")).toBeTruthy();
    // 计数 chips 直接读服务端判决,前端不复算
    expect(screen.getByTestId("deviation-count-triggered").textContent).toContain("1");
    expect(screen.getByTestId("deviation-count-edge").textContent).toContain("1");
    expect(screen.getByTestId("deviation-count-watch").textContent).toContain("1");
    // 时标落在本面板作用域内。变异实测:改成 asOf={null} 原本零红。
    expect(within(panel).getByText("截至 20261020")).toBeTruthy();
  });

  it("renders missing windows as — rather than 0", () => {
    // 10d 缺键 = "算不出来",不是"零偏离"。渲染 0 会被读成"这个窗口很安全"。
    populatedApi();
    render(<AshareBoardPage />);
    const panel = screen.getByTestId("ashare-deviation-panel");
    const cybRow = within(panel).getByText("300750.SZ").closest("tr")!;
    // 该行只有 3d/10d;30d 那一格必须是 —
    expect(within(cybRow).getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("marks intraday rows and names the history basis", () => {
    // 盘中标记是逐行的:服务端只在「个股与基准两侧都是今日报价」时才叠加。
    populatedApi();
    render(<AshareBoardPage />);
    expect(screen.getAllByTestId("deviation-intraday-badge")).toHaveLength(1);
    const basis = screen.getByTestId("deviation-basis-note").textContent!;
    expect(basis).toContain("实时分区 spot");
    expect(basis).toContain("盘中实时涨跌已叠加");
  });

  it("renders the disclosure-basis caveat as visible prose", () => {
    // 阈值是交易所披露标准的近似 —— 必须出现在页面上,不能只放在注释里。
    populatedApi();
    render(<AshareBoardPage />);
    expect(screen.getByTestId("ashare-deviation-panel").textContent).toContain(
      "不构成监管认定",
    );
  });

  it("distinguishes no-favorites from a kline-store outage in the empty state", () => {
    populatedApi();
    mockApiData[URLS.deviation] = {
      status: "empty", reason: "no_ashare_favorites",
      hint: "自选里还没有 A股代码", as_of: "20261020", live: false,
      counts: { triggered: 0, edge: 0, watch: 0 }, rows: [], skipped: [],
    };
    const { unmount } = render(<AshareBoardPage />);
    // 服务端 hint 原样呈现 —— 它才是可操作的那句话
    expect(screen.getByTestId("deviation-empty").textContent).toBe("自选里还没有 A股代码");
    // 不渲染空表:空表会被读成"今天没有异动"
    expect(within(screen.getByTestId("ashare-deviation-panel")).queryByRole("table")).toBeNull();
    unmount();

    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
    populatedApi();
    mockApiData[URLS.deviation] = {
      status: "degraded", reason: "kline_manager_unavailable",
      hint: null, as_of: "20261020", live: false,
      counts: { triggered: 0, edge: 0, watch: 0 }, rows: [], skipped: [],
    };
    render(<AshareBoardPage />);
    expect(screen.getByTestId("deviation-empty").textContent).toContain("不是「无异动」");
  });

  // === 轮询闸门 ==========================================
  // 这一组是本任务的核心行为:盘中按档轮询、收盘一律停。闸门传错是**静默**的
  // —— 页面照样显示数据,只是收盘后还在空转、或盘中冻在首帧,肉眼看不出来。
  // 所以直接钉住每个端点拿到的 refreshInterval。

  /** 实时档:响应里带"今日"行、盘中会变的六块。 */
  const LIVE_TIER = [
    "/api/v1/ashare/limit-up-pool",
    "/api/v1/ashare/ladder",
    "/api/v1/ashare/phase",
    "/api/v1/ashare/boards?kind=concept",
    "/api/v1/ashare/overview",
    "/api/v1/ashare/deviation?live=true",
  ];
  /** 慢档:只读已收盘会话的三块,盘中重算答案不变。 */
  const SLOW_TIER = [
    "/api/v1/ashare/rotation?days=12",
    "/api/v1/ashare/mainline?days=20",
    "/api/v1/ashare/concept-rps?lookback=120",
  ];

  it("polls the live and slow tiers at their own intervals while the session is open", () => {
    mockSession.open = true;
    populatedApi();
    render(<AshareBoardPage />);

    for (const url of LIVE_TIER) {
      expect(mockApiOpts[url]?.refreshInterval, `live tier: ${url}`).toBe(30_000);
    }
    for (const url of SLOW_TIER) {
      expect(mockApiOpts[url]?.refreshInterval, `slow tier: ${url}`).toBe(60_000);
    }
    // 慢档必须真的比实时档慢:两档若同值,分档就没有意义。
    expect(mockApiOpts[SLOW_TIER[0]]!.refreshInterval!).toBeGreaterThan(
      mockApiOpts[LIVE_TIER[0]]!.refreshInterval!,
    );
  });

  it("stops every pollable endpoint once the session closes", () => {
    mockSession.open = false;
    populatedApi();
    render(<AshareBoardPage />);

    // 收盘后仍在轮询 = 对着一个不会变的收盘截面空转,且会挡住机器休眠。
    for (const url of [...LIVE_TIER, ...SLOW_TIER]) {
      expect(mockApiOpts[url]?.refreshInterval, `closed session: ${url}`).toBe(0);
    }
  });

  it("never polls the day-frequency blocks, open or closed", () => {
    // 龙虎榜收盘后才发布、北向 2024-08 已停更 —— 轮询它们纯属空转,
    // 与交易时段无关,所以开市时也必须是 0。
    mockSession.open = true;
    populatedApi();
    render(<AshareBoardPage />);
    expect(mockApiOpts[lhbUrl()]?.refreshInterval ?? 0).toBe(0);
    expect(mockApiOpts[URLS.northbound]?.refreshInterval ?? 0).toBe(0);
  });

  it("covers every data endpoint it renders — no unclassified poll tier", () => {
    // 新增一块时若忘了定档,这里会把它揪出来:要么进两档,要么显式归入日频。
    mockSession.open = true;
    populatedApi();
    render(<AshareBoardPage />);
    const pollable = new Set([...LIVE_TIER, ...SLOW_TIER, lhbUrl(), URLS.northbound]);
    for (const url of Object.keys(mockApiOpts)) {
      expect(pollable.has(url), `unclassified endpoint: ${url}`).toBe(true);
    }
    expect(Object.keys(mockApiOpts).length).toBe(pollable.size);
  });

  // === 新鲜度徽章接线 ====================================
  // `ashare-freshness.test.tsx` 只钉住纯函数与组件本身；**页面有没有把它挂上**
  // 是另一件事 —— 拆掉卡片里的 <AshareFreshness> 后纯函数测试仍然全绿。
  // 这里的 session.as_of 固定为 2026-10-21，而 fixture 的数据日是 20241101，
  // 两者不等 ⇒ 应出现金色 [20241101]。等于没接线的情形恰恰是「什么都不出现」，
  // 所以必须断言"出现了"，不能只断言不报错。

  it("flags a block whose data day is not the session day", () => {
    populatedApi();
    render(<AshareBoardPage />);
    expect(within(screen.getByTestId("right-涨停概览")).getByText("20241101")).toBeTruthy();
    expect(within(screen.getByTestId("right-涨停概览")).getByText("[20241101]")).toBeTruthy();
    expect(within(screen.getByTestId("right-连板梯队(今日)")).getByText("[20241101]")).toBeTruthy();
  });

  it("stays quiet when the block's day matches the session day", () => {
    // 新鲜是常态:同一天不该挂任何方括号标注(否则每块常驻一条噪声)。
    populatedApi();
    mockApiData[URLS.pool] = { ...(mockApiData[URLS.pool] as object), date: "20261021" };
    mockApiData[URLS.ladder] = { ...(mockApiData[URLS.ladder] as object), date: "20261021" };
    render(<AshareBoardPage />);
    expect(within(screen.getByTestId("right-涨停概览")).queryByText(/^\[/)).toBeNull();
    expect(within(screen.getByTestId("right-连板梯队(今日)")).queryByText(/^\[/)).toBeNull();
  });

  it("says unlabelled rather than guessing when the block carries no day", () => {
    // 端点回了数据却没给日期 —— 猜一个等于编数据(见 ashare-freshness.tsx:18)。
    populatedApi();
    mockApiData[URLS.pool] = { ...(mockApiData[URLS.pool] as object), date: null };
    render(<AshareBoardPage />);
    expect(within(screen.getByTestId("right-涨停概览")).getByText("[无时标]")).toBeTruthy();
  });
});

describe("pollIntervals", () => {
  it("closed → both tiers 0", () => {
    expect(pollIntervals(false)).toEqual({ live: 0, slow: 0 });
  });

  it("open → both tiers non-zero, slow strictly slower", () => {
    const { live, slow } = pollIntervals(true);
    expect(live).toBeGreaterThan(0);
    expect(slow).toBeGreaterThan(live);
  });
});
