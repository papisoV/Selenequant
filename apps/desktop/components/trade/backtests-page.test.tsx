import type { ReactNode } from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { I18nProvider } from "@/components/i18n/i18n-provider";
import { BacktestsPage } from "./backtests-page";

const { replaceMock, pushMock, searchParamsMock } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  pushMock: vi.fn(),
  searchParamsMock: { current: new URLSearchParams() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: pushMock }),
  useSearchParams: () => searchParamsMock.current,
}));

const { getBacktestDetailMock, getKlineWithCacheMock, deleteBacktestMock } = vi.hoisted(() => ({
  getBacktestDetailMock: vi.fn(),
  getKlineWithCacheMock: vi.fn(),
  deleteBacktestMock: vi.fn(),
}));

vi.mock("@/lib/api/backtests", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/backtests")>();
  return {
    ...actual,
    getBacktestDetail: getBacktestDetailMock,
    deleteBacktest: deleteBacktestMock,
  };
});

vi.mock("@/lib/api/markets", () => ({
  getKlineWithCache: getKlineWithCacheMock,
}));

// 复盘卡内嵌 lightweight-charts 重型图表,测试 stub(kf-page.test.tsx 同款惯例)
vi.mock("@/components/trading/candlestick-chart", () => ({
  default: () => <div data-testid="review-candle-chart">CandlestickChart</div>,
}));

const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: useApiMock,
}));

vi.mock("@/lib/store/workflow", () => ({
  useWorkflowStore: vi.fn((sel: (s: unknown) => unknown) =>
    sel({ setBacktest: vi.fn(), setDeployTarget: vi.fn(), optimization: null })),
}));

vi.mock("echarts", () => ({ init: vi.fn(() => ({ dispose: vi.fn(), setOption: vi.fn() })) }));

function detailPayload() {
  return {
    task_id: "bt-share",
    strategy_name: "BB v3",
    symbol: "BTC/USDT",
    status: "completed",
    total_return: 0.12,
    sharpe_ratio: 1.5,
    equity_curve: [1, 2, 3],
    lookahead_warnings: [],
    config_snapshot: { initial_capital: 10000 },
  };
}

function detailWithTrades(): Record<string, unknown> {
  return {
    ...detailPayload(),
    interval: "1d",
    start_date: "2025-01-01T00:00:00",
    end_date: "2025-03-01T00:00:00",
    config_snapshot: { initial_capital: 10000, exchange: "binance", market_type: "spot" },
    trades: [
      {
        entry_time: 1_700_000_000_000,
        exit_time: 1_700_000_000_000 + 48 * 3_600_000,
        side: "buy",
        entry_price: 42000,
        exit_price: 43500,
        quantity: 0.1,
        pnl: 150,
      },
      {
        entry_time: 1_700_100_000_000,
        exit_time: 1_700_100_000_000 + 24 * 3_600_000,
        side: "sell",
        entry_price: 43000,
        exit_price: 42500,
        quantity: 0.1,
        pnl: -50,
      },
    ],
  };
}

const ONE_ROW = [
  { task_id: "bt-share", symbol: "BTC/USDT", strategy_id: "bb_v3", sharpe: 1.5, status: "completed" },
];

function mockUseApi(listRows: Record<string, unknown>[] = ONE_ROW) {
  useApiMock.mockImplementation((url: string) =>
    url === "/api/v1/backtests"
      ? { data: listRows, isLoading: false, mutate: vi.fn(), refreshInterval: 5000 }
      : { data: [], isLoading: false, mutate: vi.fn() }
  );
}

beforeEach(() => {
  replaceMock.mockClear();
  pushMock.mockClear();
  getBacktestDetailMock.mockReset();
  getKlineWithCacheMock.mockReset();
  deleteBacktestMock.mockReset();
  deleteBacktestMock.mockResolvedValue({ deleted: "bt-share" });
  getKlineWithCacheMock.mockResolvedValue({ klines: [], fromCache: false, source: "exchange" });
  searchParamsMock.current = new URLSearchParams();
  useApiMock.mockReset();
  mockUseApi();
});

// jsdom 无 scrollIntoView(pa-panel.test.tsx 同款 stub)
Element.prototype.scrollIntoView = vi.fn();

// P0-A: BacktestsPage 现用 useChartTheme()→useI18nContext,须包 I18nProvider。
function renderWithProvider(ui: React.ReactNode) {
  return render(<I18nProvider>{ui}</I18nProvider>);
}

describe("BacktestsPage URL 同步(?task_id= 直达 + 关闭清除)", () => {
  it("点行内「复盘」→ openBacktestDetail 写 URL ?task_id=", async () => {
    getBacktestDetailMock.mockResolvedValue(detailPayload());
    renderWithProvider(<BacktestsPage />);
    const infoBtn = await screen.findByRole("button", { name: "复盘" });
    fireEvent.click(infoBtn);
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/trade/backtests?task_id=bt-share", { scroll: false }),
    );
    expect(await screen.findByText(/回测详情 — bt-share/)).toBeInTheDocument();
  });

  it("URL 带 ?task_id= 挂载 → 自动弹详情", async () => {
    searchParamsMock.current = new URLSearchParams("task_id=bt-direct");
    getBacktestDetailMock.mockResolvedValue({ ...detailPayload(), task_id: "bt-direct" });
    renderWithProvider(<BacktestsPage />);
    await waitFor(() => expect(getBacktestDetailMock).toHaveBeenCalledWith("bt-direct"));
    expect(await screen.findByText(/回测详情 — bt-direct/)).toBeInTheDocument();
  });

  it("关闭 Dialog → 清 URL", async () => {
    searchParamsMock.current = new URLSearchParams("task_id=bt-x");
    getBacktestDetailMock.mockResolvedValue(detailPayload());
    renderWithProvider(<BacktestsPage />);
    expect(await screen.findByText(/回测详情 — bt-share/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/trade/backtests", { scroll: false }),
    );
  });
});

describe("按此配置重跑(历史回填)", () => {
  it("点重跑按钮 → 关 Dialog + 清 URL + 表单回填 strategy/symbol/区间/资金", async () => {
    getBacktestDetailMock.mockResolvedValue({
      ...detailPayload(),
      strategy_id: "rsi_ma_delta",
      symbol: "ETH/USDT",
      interval: "4h",
      start_date: "2025-01-01T00:00:00",
      end_date: "2025-06-30T00:00:00",
      config_snapshot: { initial_capital: 20000, commission_rate: 0.0005, slippage_bps: 12 },
    });
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "复盘" }));
    const rerunBtn = await screen.findByRole("button", { name: /按此配置重跑/ });
    fireEvent.click(rerunBtn);
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/trade/backtests", { scroll: false }),
    );
    // 回填落表单: 模板 select 的值、标的 select 的值、日期 input 的值
    const strategySelect = screen.getByDisplayValue("rsi_ma_delta") as HTMLSelectElement;
    expect(strategySelect.tagName).toBe("SELECT");
    expect((screen.getByDisplayValue("ETH/USDT") as HTMLSelectElement).tagName).toBe("SELECT");
    expect((screen.getByDisplayValue("4h") as HTMLSelectElement).tagName).toBe("SELECT");
    expect(screen.getByDisplayValue("2025-01-01")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2025-06-30")).toBeInTheDocument();
    expect((screen.getByDisplayValue("20000") as HTMLInputElement).type).toBe("number");
  });

  it("旧快照缺键(空 config_snapshot + 无区间) → 只回填 task 行字段,表单其余留现值", async () => {
    getBacktestDetailMock.mockResolvedValue({
      ...detailPayload(),
      config_snapshot: {},
    });
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "复盘" }));
    fireEvent.click(await screen.findByRole("button", { name: /按此配置重跑/ }));
    // strategy_id/symbol 来自 detail 顶层;日期无 start_date 不回填 → 保持默认(今年往前一年)
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/trade/backtests", { scroll: false }));
    expect((screen.getByDisplayValue("bb_v3") as HTMLSelectElement).tagName).toBe("SELECT");
    expect(screen.getByDisplayValue("BTC/USDT")).toBeInTheDocument();
  });
});

describe("运行中任务耗时秒表", () => {
  it("running 行显示已运行秒数而非状态文案", () => {
    const createdAt = Date.now() - 42_000;
    mockUseApi([
      { task_id: "bt-run", symbol: "BTC/USDT", strategy_id: "bb_v3", status: "running", created_at: createdAt },
    ]);
    renderWithProvider(<BacktestsPage />);
    expect(screen.getByText(/42s/)).toBeInTheDocument();
    expect(screen.queryByText("running")).not.toBeInTheDocument();
  });
});

describe("K线复盘卡(点交易行展开)", () => {
  it("点交易行 → 复盘卡出现并按窗口取数(懒取一次),再点收起", async () => {
    getBacktestDetailMock.mockResolvedValue(detailWithTrades());
    getKlineWithCacheMock.mockResolvedValue({
      klines: [{ timestamp: 1_700_000_000_000, open: 1, high: 2, low: 0.5, close: 1.5, volume: 9 }],
      fromCache: false,
      source: "exchange",
    });
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "复盘" }));
    fireEvent.click(await screen.findByText("42000.00")); // 第一笔交易行
    expect(await screen.findByText(/K线复盘 — BTC\/USDT/)).toBeInTheDocument();
    await waitFor(() => expect(getKlineWithCacheMock).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("review-candle-chart")).toBeInTheDocument();
    // 新结果带 snapshot 交易所 → 不显示回退注记
    expect(screen.queryByText(/旧结果未记录交易所/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("42000.00"));
    await waitFor(() => expect(screen.queryByText(/K线复盘 — BTC\/USDT/)).not.toBeInTheDocument());
  });

  it("切换下一笔 → 步进指示更新", async () => {
    getBacktestDetailMock.mockResolvedValue(detailWithTrades());
    getKlineWithCacheMock.mockResolvedValue({ klines: [], fromCache: false, source: "exchange" });
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "复盘" }));
    fireEvent.click(await screen.findByText("42000.00"));
    expect(await screen.findByText(/第 1\/2 笔/)).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("下一笔"));
    expect(await screen.findByText(/第 2\/2 笔/)).toBeInTheDocument();
    expect(screen.getByTitle("下一笔")).toBeDisabled(); // 到底
  });

  it("旧快照缺 exchange/market_type → 显示回退注记", async () => {
    const d = detailWithTrades();
    d.config_snapshot = { initial_capital: 10000 }; // 旧快照:无 exchange/market_type
    getBacktestDetailMock.mockResolvedValue(d);
    getKlineWithCacheMock.mockResolvedValue({ klines: [], fromCache: false, source: "exchange" });
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "复盘" }));
    fireEvent.click(await screen.findByText("42000.00"));
    expect(await screen.findByText(/旧结果未记录交易所/)).toBeInTheDocument();
  });
});

describe("发车区间预估条", () => {
  it("默认 1d×365天 → 显示约 366 根,ok 档无告警后缀", async () => {
    renderWithProvider(<BacktestsPage />);
    const el = await screen.findByText(/约 366 根/);
    expect(el.textContent).not.toMatch(/可能较慢|建议缩短/);
  });

  it("周期切到 1m → 变 heavy 档,附「建议缩短区间或放大周期」", async () => {
    renderWithProvider(<BacktestsPage />);
    fireEvent.change(screen.getByDisplayValue("1d"), { target: { value: "1m" } });
    const el = await screen.findByText(/建议缩短区间或放大周期/);
    expect(el.textContent).toMatch(/约 525,601 根/); // 365 天 @1m
  });

  it("区间倒置(结束早于开始)→ 不显示预估", async () => {
    renderWithProvider(<BacktestsPage />);
    const [startInput] = screen.getAllByDisplayValue(/202[0-9]-/);
    fireEvent.change(startInput, { target: { value: "2026-12-31" } });
    await waitFor(() => expect(screen.queryByText(/约 .* 根/)).not.toBeInTheDocument());
  });
});

describe("回测列表三修(名字兜底 / 时间显示 / 实盘确认)", () => {
  it("AC1: strategy_name 为空串 → 回退显示 strategy_id", () => {
    mockUseApi([
      { task_id: "bt-a", symbol: "BTC/USDT", strategy_name: "", strategy_id: "sma_cross", status: "completed" },
    ]);
    renderWithProvider(<BacktestsPage />);
    expect(screen.getByText("sma_cross")).toBeInTheDocument();
  });

  it("AC2: 策略标识全空 → 显示 —", () => {
    mockUseApi([
      { task_id: "bt-b", symbol: "", strategy_name: "", strategy_id: "", status: "completed" },
    ]);
    renderWithProvider(<BacktestsPage />);
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(2); // 名字列 + 标的列
  });

  it("AC3: symbol 为空串 → 标的列显示 —", () => {
    mockUseApi([
      { task_id: "bt-c", symbol: "", strategy_id: "bb_v3", status: "completed" },
    ]);
    renderWithProvider(<BacktestsPage />);
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("AC4: completed 行显示 created_at 绝对时间(MM/DD HH:MM)", () => {
    // 本地时区 2026-10-09 20:23 对应的 ms,由 formatTime 计算期望值避免时区耦合
    const created = new Date(2026, 9, 9, 20, 23).getTime();
    mockUseApi([
      { task_id: "bt-d", symbol: "BTC/USDT", strategy_id: "bb_v3", status: "completed", created_at: created },
    ]);
    renderWithProvider(<BacktestsPage />);
    const d = new Date(created);
    const pad = (n: number) => String(n).padStart(2, "0");
    expect(screen.getByText(`${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`)).toBeInTheDocument();
  });

  it("AC5: failed 行同样显示时间", () => {
    const created = new Date(2026, 8, 1, 8, 5).getTime();
    mockUseApi([
      { task_id: "bt-e", symbol: "BTC/USDT", strategy_id: "bb_v3", status: "failed", created_at: created },
    ]);
    renderWithProvider(<BacktestsPage />);
    const d = new Date(created);
    const pad = (n: number) => String(n).padStart(2, "0");
    expect(screen.getByText(`${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`)).toBeInTheDocument();
  });

  it("AC6: running 行仍显示相对秒数(不回归)", () => {
    const createdAt = Date.now() - 7_000;
    mockUseApi([
      { task_id: "bt-run2", symbol: "BTC/USDT", strategy_id: "bb_v3", status: "running", created_at: createdAt },
    ]);
    renderWithProvider(<BacktestsPage />);
    expect(screen.getByText(/7s/)).toBeInTheDocument();
  });

  it("AC7+AC10: 点实盘不直接跳转,弹确认框;failed 行按钮仍禁用", async () => {
    const created = new Date(2026, 9, 9, 20, 23).getTime();
    mockUseApi([
      { task_id: "bt-f", symbol: "BTC/USDT", strategy_id: "bb_v3", status: "completed", created_at: created },
      { task_id: "bt-g", symbol: "BTC/USDT", strategy_id: "bb_v3", status: "failed", created_at: created },
    ]);
    renderWithProvider(<BacktestsPage />);
    const liveButtons = screen.getAllByRole("button", { name: "实盘" });
    const completedLive = liveButtons[0];
    expect(liveButtons[1]).toBeDisabled(); // AC10
    fireEvent.click(completedLive);
    expect(pushMock).not.toHaveBeenCalled(); // AC7: 未确认不跳转
    expect(await screen.findByText("部署到实盘")).toBeInTheDocument();
    expect(screen.getByText(/真实资金/)).toBeInTheDocument();
  });

  it("AC8: 确认弹窗点「确认前往」→ push /trade/trd", async () => {
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(screen.getAllByRole("button", { name: "实盘" })[0]);
    fireEvent.click(await screen.findByRole("button", { name: "确认前往" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/trade/trd"));
  });

  it("AC9: 确认弹窗点「取消」→ 不跳转", async () => {
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(screen.getAllByRole("button", { name: "实盘" })[0]);
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.queryByText("部署到实盘")).not.toBeInTheDocument();
  });

  it("AC12: 点行主体 → 打开复盘(回测详情);部署已拆成显式按钮", async () => {
    getBacktestDetailMock.mockResolvedValue(detailPayload());
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(screen.getByText("bt-share"));
    await waitFor(() => expect(pushMock).not.toHaveBeenCalled()); // 行不再直接跳模拟盘
    expect(await screen.findByText(/回测详情 — bt-share/)).toBeInTheDocument();
  });

  it("AC13: 点「模拟」按钮 → 走 paper 路径(不加确认)", async () => {
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(screen.getAllByRole("button", { name: "模拟" })[0]);
    expect(pushMock).toHaveBeenCalledWith("/trade/paper");
  });
});

describe("可区分性:周期 / 区间列", () => {
  it("同策略同标的的两条记录,靠 周期+区间 可区分", async () => {
    mockUseApi([
      { task_id: "bt-a", symbol: "BTC/USDT", strategy_id: "bb_v3", interval: "1d",
        start_date: "2025-10-13T00:00:00+00:00", end_date: "2026-10-13T00:00:00+00:00",
        total_return: 0.1, status: "completed" },
      { task_id: "bt-b", symbol: "BTC/USDT", strategy_id: "bb_v3", interval: "4h",
        start_date: "2025-01-01T00:00:00+00:00", end_date: "2025-06-30T00:00:00+00:00",
        total_return: 0.2, status: "completed" },
    ]);
    renderWithProvider(<BacktestsPage />);
    // 两条记录都渲染(靠 task_id 定位,标的/周期串在发车表单 select 里也有 option)
    const rowA = (await screen.findByText("bt-a")).closest("div")!;
    const rowB = screen.getByText("bt-b").closest("div")!;
    // 周期列区分:1d 与 4h(在各自行内)
    expect(rowA.textContent).toContain("1d");
    expect(rowB.textContent).toContain("4h");
    // 区间列区分:两段互不相同的日期
    expect(rowA.textContent).toContain("2025/10/13→2026/10/13");
    expect(rowB.textContent).toContain("2025/01/01→2025/06/30");
  });

  it("缺 interval/日期 → 渲染「—」不伪造", async () => {
    mockUseApi([
      { task_id: "bt-x", symbol: "BTC/USDT", strategy_id: "bb_v3", status: "failed" },
    ]);
    renderWithProvider(<BacktestsPage />);
    expect(await screen.findByText("bt-x")).toBeInTheDocument();
    // 任务 id + 周期 + 区间 三格各回退 "—"
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });
});

describe("删除回测记录", () => {
  it("点删除图标 → 出现确认;点确认 → 调 deleteBacktest 并刷新列表", async () => {
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "删除 bt-share" }));
    // 未确认前不调接口
    expect(deleteBacktestMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(deleteBacktestMock).toHaveBeenCalledWith("bt-share"));
  });

  it("点确认前点取消 → 不调 deleteBacktest", async () => {
    renderWithProvider(<BacktestsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "删除 bt-share" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteBacktestMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "确认" })).not.toBeInTheDocument();
  });

  it("删除的行恰是打开的详情 → 一并关闭、清 URL", async () => {
    searchParamsMock.current = new URLSearchParams("task_id=bt-share");
    getBacktestDetailMock.mockResolvedValue(detailPayload());
    renderWithProvider(<BacktestsPage />);
    expect(await screen.findByText(/回测详情 — bt-share/)).toBeInTheDocument();
    // 详情弹窗打开时 Radix 给背景挂 aria-hidden,行内按钮需 hidden:true 才查得到
    fireEvent.click(screen.getByRole("button", { name: "删除 bt-share", hidden: true }));
    fireEvent.click(screen.getByRole("button", { name: "确认", hidden: true }));
    await waitFor(() => expect(deleteBacktestMock).toHaveBeenCalledWith("bt-share"));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/trade/backtests", { scroll: false }));
  });
});
