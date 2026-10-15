import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { EpisodesPage } from "@/components/research/episodes-page";

const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));
const { searchParamsMock } = vi.hoisted(() => ({ searchParamsMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: searchParamsMock }),
}));

vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({ useApi: useApiMock }),
}));

// replay payload shape mirrors services/local-api episodes.py live response
// (episode epi-7ba2d7372b83, ARB/USDT 1h, bt-581e359a)
const T1 = 1_779_000_000_000;
const replayWithheld = {
  episode_id: "epi-1",
  strategy_kind: "bridge",
  strategy_id: "bridge:ARBUSDT_autogen_factor",
  strategy_name: "ARBUSDT_autogen_factor",
  symbol: "ARB/USDT",
  interval: "1h",
  exchange: "binance",
  market_type: "futures",
  at: T1,
  at_iso: new Date(T1).toISOString(),
  at_source: "REQUESTED",
  revealed: false,
  state: {
    market: {
      provenance: "RECORDED",
      bars: [
        { timestamp: T1 - 3_600_000 * 3, open: 1, high: 1, low: 1, close: 1, volume: 1 },
        { timestamp: T1, open: 2, high: 2, low: 2, close: 2, volume: 2 },
      ],
      bar_count: 2,
      last_close: 2,
    },
    strategy: { provenance: "RECORDED", params: { n: 5 }, kind: "bridge" },
    signal: {
      provenance: "RECORDED",
      trades_before_t: [
        {
          side: "long",
          entry_action: "buy",
          entry_price: 1.1,
          exit_price: 1.2,
          entry_time: T1 - 7_200_000,
          exit_time: T1 - 3_600_000,
          pnl: 0.1,
        },
      ],
      closed_trade_count: 174,
      last_trade: null,
      backtest_id: "bt-1",
    },
    memory: { provenance: "NOT_AVAILABLE", note: "no prior-episode retrieval wired in v0" },
    events: { provenance: "NOT_AVAILABLE", note: "no event feed captured" },
  },
  outcome: {
    provenance: "WITHHELD",
    trades_after_t: [],
    note: "T-after trades exist but are withheld; pass ?reveal=true",
    withheld_count: 92,
  },
};

const replayRevealed = {
  ...replayWithheld,
  revealed: true,
  outcome: {
    provenance: "RECORDED",
    trades_after_t: [
      {
        side: "long",
        entry_action: "buy",
        entry_price: 1.2,
        exit_price: 1.4,
        entry_time: T1 + 3_600_000,
        exit_time: T1 + 7_200_000,
        pnl: 0.2,
      },
    ],
    closed_trade_count: 92,
    metrics: { total_return: 0.6029, sharpe_ratio: 1.63, max_drawdown: -0.2 },
    post_mortem: {
      as_of: T1,
      last_action_side: "buy",
      realized_pnl_before_t: 1.5,
      realized_pnl_after_t: 2.0,
      direction_correct: true,
    },
  },
};

const listRows = [
  {
    id: "epi-1",
    strategy_kind: "bridge",
    strategy_id: "bridge:ARBUSDT_autogen_factor",
    strategy_name: "ARBUSDT_autogen_factor",
    symbol: "ARB/USDT",
    interval: "1h",
    backtest_id: "bt-1",
    provenance: "RECORDED",
    created_at: "2026-09-20T00:00:00",
  },
];

function apiResult(overrides?: Partial<Record<string, unknown>>) {
  return { data: null, error: undefined, isLoading: false, mutate: vi.fn(), ...overrides };
}

// Path-aware api mock: world-model path → worldModel data, else listRows data.
function pathAwareMock(worldModel: unknown, list: unknown = listRows) {
  return useApiMock.mockImplementation((path: string) =>
    apiResult({ data: path.includes("world-model") ? worldModel : list })
  );
}

const worldModelPayload = {
  groups: [
    {
      symbol: "BTC/USDT",
      interval: "1h",
      episode_count: 2,
      winning_count: 2,
      win_rate: 1.0,
      kinds: ["bridge", "qd"],
      best: {
        strategy_id: "qd:src9",
        annual_return: 0.6535,
        sharpe_ratio: 2.33,
      },
      best_sharpe: 2.33,
      worst_sharpe: 1.6,
      avg_sharpe: 1.95,
      last_episode_at: "2026-09-23T13:00:00",
    },
    {
      symbol: "ETH/USDT",
      interval: "4h",
      episode_count: 1,
      winning_count: 0,
      win_rate: 0.0,
      kinds: ["qd"],
      best: { strategy_id: "qd:src48", annual_return: -0.3712, sharpe_ratio: -0.4 },
      best_sharpe: -0.4,
      worst_sharpe: -0.4,
      avg_sharpe: -0.4,
      last_episode_at: "2026-09-23T12:00:00",
    },
  ],
  generated_at: "2026-09-23T14:00:00",
  total_episodes: 3,
};

describe("EpisodesPage — 经验地图 (World Model)", () => {
  beforeEach(() => {
    searchParamsMock.mockImplementation(() => null);
  });

  it("默认 tab 渲染分组卡片:组标题/win_rate 徽章/best 策略行", async () => {
    pathAwareMock(worldModelPayload);
    render(<EpisodesPage />);
    await waitFor(() => screen.getByTestId("world-model-grid"));
    expect(screen.getByText("BTC/USDT · 1h")).toBeInTheDocument();
    expect(screen.getByText("ETH/USDT · 4h")).toBeInTheDocument();
    const badges = screen.getAllByTestId("win-rate-badge");
    expect(badges.map((b) => b.textContent)).toEqual(["胜率 100%", "胜率 0%"]);
    // best strategy row: annual return % + sharpe
    expect(screen.getByText("qd:src9")).toBeInTheDocument();
    expect(screen.getByText(/best 年化 65.35%/)).toBeInTheDocument();
    // 高胜率 emerald / 零胜率 red
    expect(badges[0].className).toContain("emerald");
    expect(badges[1].className).toContain("red");
  });

  it("空 groups 显示引导文案", () => {
    pathAwareMock({ groups: [], generated_at: "2026-09-23T14:00:00", total_episodes: 0 });
    render(<EpisodesPage />);
    expect(screen.getByText(/暂无经验/)).toBeInTheDocument();
  });
});

describe("EpisodesPage — list view", () => {
  beforeEach(() => {
    searchParamsMock.mockImplementation(() => null);
  });

  it("切到 Episode 列表 tab 后渲染列表并链接到 replay", async () => {
    pathAwareMock(worldModelPayload);
    render(<EpisodesPage />);
    fireEvent.click(screen.getByTestId("tab-list"));
    await waitFor(() => screen.getByTestId("episode-list"));
    expect(screen.getByText("ARBUSDT_autogen_factor")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Replay" });
    expect(link.getAttribute("href")).toBe("/research/episodes?id=epi-1");
  });

  it("空列表显示引导文案", async () => {
    pathAwareMock({ groups: [], generated_at: "x", total_episodes: 0 }, []);
    render(<EpisodesPage />);
    fireEvent.click(screen.getByTestId("tab-list"));
    expect(screen.getByText(/暂无 Episode/)).toBeInTheDocument();
  });

  it("错误状态显示失败文案(distinguish=true)", async () => {
    useApiMock.mockImplementation(() => apiResult({ error: { status: 503, message: "down" } }));
    render(<EpisodesPage />);
    expect(screen.getByText(/加载失败/)).toBeInTheDocument();
  });
});

describe("EpisodesPage — replay view", () => {
  beforeEach(() => {
    searchParamsMock.mockImplementation((k: string) => (k === "id" ? "epi-1" : null));
  });

  it("默认拉 replay(未 reveal) — WITHHELD 徽章 + Reveal 按钮 + 扣住计数", async () => {
    useApiMock.mockImplementation((path: string) =>
      apiResult({ data: path.includes("reveal=true") ? replayRevealed : replayWithheld })
    );
    const { container } = render(<EpisodesPage />);
    await waitFor(() => screen.getByTestId("reveal-btn"));
    const texts = screen
      .getAllByTestId("prov-badge")
      .map((b) => b.textContent);
    expect(texts).toContain("WITHHELD");
    expect(texts).toContain("RECORDED");
    expect(texts).toContain("NOT AVAILABLE");
    expect(container.textContent).toContain("92");
    expect(screen.queryByTestId("trades-after")).toBeNull();
  });

  it("点 Reveal 后以 ?reveal=true 重拉并渲染 outcome 表 + post-mortem", async () => {
    useApiMock.mockImplementation((path: string) =>
      apiResult({ data: path.includes("reveal=true") ? replayRevealed : replayWithheld })
    );
    render(<EpisodesPage />);
    await waitFor(() => screen.getByTestId("reveal-btn"));
    fireEvent.click(screen.getByTestId("reveal-btn"));
    await waitFor(() => screen.getByTestId("trades-after"));
    expect(screen.getByTestId("trades-after").textContent).toContain("0.2000");
    expect(screen.getByText(/Post-mortem/)).toBeInTheDocument();
    expect(screen.getByText("direction_correct")).toBeInTheDocument();
    expect(screen.queryByTestId("reveal-btn")).toBeNull();
  });

  it("scrubber 用 RECORDED bars 端点渲染,拖动后以 ?at 重拉", async () => {
    useApiMock.mockImplementation(() => apiResult({ data: replayWithheld }));
    render(<EpisodesPage />);
    await waitFor(() => screen.getByTestId("scrubber"));
    const scrubber = screen.getByTestId("scrubber") as HTMLInputElement;
    expect(scrubber.min).toBe(String(T1 - 3_600_000 * 3));
    expect(scrubber.max).toBe(String(T1));
    fireEvent.change(scrubber, { target: { value: String(T1 - 1_800_000) } });
    await waitFor(() => {
      expect(useApiMock).toHaveBeenCalledWith(
        `/api/v1/episodes/epi-1/replay?at=${T1 - 1_800_000}`
      );
    });
  });

  it("T 前成交表渲染 closed 计数与 pnl 符号", async () => {
    useApiMock.mockImplementation(() => apiResult({ data: replayWithheld }));
    render(<EpisodesPage />);
    await waitFor(() => screen.getByTestId("trades-before"));
    expect(screen.getByTestId("trades-before").textContent).toContain("0.1000");
    expect(screen.getByText(/已平仓 174 笔/)).toBeInTheDocument();
  });
});
