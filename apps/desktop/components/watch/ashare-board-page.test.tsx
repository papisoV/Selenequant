import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// === mock useApi =================================
const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string) => ({ data: mockApiData[url] ?? null, loading: false, error: null }),
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
import { AshareBoardPage } from "./ashare-board-page";

const URLS = {
  pool: "/api/v1/ashare/limit-up-pool",
  ladder: "/api/v1/ashare/ladder",
  phase: "/api/v1/ashare/phase",
  concept: "/api/v1/ashare/boards?kind=concept",
  industry: "/api/v1/ashare/boards?kind=industry",
  northbound: "/api/v1/ashare/northbound",
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
}

describe("AshareBoardPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
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

    // 涨停池 / 板块排名
    expect(screen.getByText("贵州茅台")).toBeTruthy();
    expect(screen.getByText("AI算力")).toBeTruthy();
    expect(screen.getByText("中际旭创")).toBeTruthy();

    // 深度面新增两块:龙虎榜 / 北向(市场面,来自 ashare_deep 端点)
    expect(screen.getByTestId("card-龙虎榜")).toBeTruthy();
    expect(screen.getByTestId("card-北向资金 (每日)")).toBeTruthy();
    expect(screen.getByText("日涨幅偏离值达7%")).toBeTruthy();
    // 北向空行默认被过滤,只显示有净买额的行 → 尾部日期作标题右标
    expect(screen.getByText(/最近 20240816/)).toBeTruthy();
  });

  it("shows empty placeholders when APIs return nothing", () => {
    render(<AshareBoardPage />);
    expect(screen.getByText(/暂无情绪周期数据/)).toBeTruthy();
    expect(screen.getByText(/暂无连板数据/)).toBeTruthy();
    expect(screen.getByText(/暂无涨停池数据/)).toBeTruthy();
    expect(screen.getByText(/暂无概念板块数据/)).toBeTruthy();
    expect(screen.getByText(/暂无该日龙虎榜/)).toBeTruthy();
    expect(screen.getByText(/暂无北向资金数据/)).toBeTruthy();
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
});
