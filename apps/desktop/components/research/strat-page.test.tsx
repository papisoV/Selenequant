import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

// === mock store =================================
const mockStore = {
  selectTemplateCalls: [] as Array<{ id: string; name: string; category: string; sharpe?: number; winrate?: number }>,
  setOptimizationCalls: [] as Array<Record<string, unknown>>,
  deployTarget: null as null | "paper" | "live",
};

vi.mock("@/lib/store/workflow", () => ({
  useWorkflowStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      selectTemplate: (t: typeof mockStore.selectTemplateCalls[number]) => {
        mockStore.selectTemplateCalls.push(t);
      },
      setDeployTarget: (t: "paper" | "live") => {
        mockStore.deployTarget = t;
      },
      setOptimization: (r: Record<string, unknown>) => {
        mockStore.setOptimizationCalls.push(r);
      },
    }),
}));

// === mock use-api-page(useApiPage 闭包注入 distinguish fetcher) ===
// 错误通道可编程:mockApiErr[key] 有值 → useApi 返回该 error,驱动「加载失败」渲染
const mockApiData: Record<string, unknown> = {};
const mockApiErr: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: (url: string) => ({
      data: mockApiData[url] ?? null,
      error: mockApiErr[url],
      isLoading: false,
      mutate: vi.fn(() => Promise.resolve()),
    }),
  }),
}));

// === mock swr(部署列表/QD 策略库走 useSWR,按 key 预设,消除真 fetch) ===
const swrState: Record<string, { data?: unknown; error?: unknown }> = {};
const swrMutateMock = vi.fn(() => Promise.resolve());
vi.mock("swr", () => ({
  default: (key: string | null) => ({
    data: key != null ? (swrState[key]?.data ?? null) : null,
    error: key != null ? swrState[key]?.error : undefined,
    isLoading: false,
    mutate: swrMutateMock,
  }),
}));

// === mock api-client =================================
const apiPostMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiPost: (url: string, body?: Record<string, unknown>) => apiPostMock(url, body),
}));

// === mock next/navigation =================================
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  // 深链 ?deploy=1&template= 直开向导;测试默认无参数
  useSearchParams: () => new URLSearchParams(),
}));

// === import AFTER mocks =================================
import { StratPage, adaptSavedStrategy, DEPLOY_FILTERS, type DeployFilterKey } from "@/components/research/strat-page";
import { applyTuningResult } from "@/lib/tuning-bridge";
import type { TuningResultSummary } from "@/lib/tuning-bridge";
import type { DeploymentSummary } from "@/lib/api/strategies";

// 测试内部类型 (strat-page 未 export SavedStrategy, 用结构等价)
interface SavedStrategy {
  name: string;
  version: string;
  formula_readable: string;
  formula_tokens: number[];
  profiles: Array<Record<string, unknown>>;
}

beforeEach(() => {
  mockStore.selectTemplateCalls = [];
  mockStore.setOptimizationCalls = [];
  mockStore.deployTarget = null;
  Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  Object.keys(mockApiErr).forEach((k) => delete mockApiErr[k]);
  Object.keys(swrState).forEach((k) => delete swrState[k]);
  apiPostMock.mockReset();
  pushMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("P11 adaptSavedStrategy", () => {
  it("#1 SavedStrategy → SelectedTemplate 5 字段映射正确", () => {
    const s: SavedStrategy = {
      name: "BB_v3",
      version: "0.1.0",
      formula_readable: "close - ma(close, 20)",
      formula_tokens: [1, 2, 3],
      profiles: [],
    };
    const t = adaptSavedStrategy(s);
    expect(t.id).toBe("BB_v3@0.1.0");
    expect(t.name).toBe("BB_v3");
    expect(t.category).toBe("alpha");
    expect(t.sharpe).toBeUndefined();
    expect(t.winrate).toBeUndefined();
  });
});

describe("P12 applyTuningResult", () => {
  it("#2 成功路径: apiPost → setOptimization → router.push", async () => {
    apiPostMock.mockResolvedValueOnce({ id: "task-xyz" });
    const r: TuningResultSummary = {
      file_name: "bb_v3_2026-08-04.json",
      timestamp: "2026-08-04T10:00:00Z",
      strategy_id: "bb_v3",
      strategy_name: "布林带升级版",
      interval: "4h",
      exchange: "binance",
      market_type: "futures",
      symbol: "BTC/USDT",
      best_sharpe: 1.85,
      best_params: { bb_len: 20, buy_mult: 1.05 },
      total_return: 0.42,
      max_drawdown: 0.12,
      win_rate: 0.58,
      n_trials: 30,
      optimization_time: 120,
    };
    const router = { push: pushMock } as unknown as Parameters<typeof applyTuningResult>[1];
    const setOptimization = (x: unknown) => { mockStore.setOptimizationCalls.push(x as Record<string, unknown>); };
    await applyTuningResult(r, router, setOptimization as Parameters<typeof applyTuningResult>[2]);
    expect(apiPostMock).toHaveBeenCalledWith("/api/v1/strategies/factory-templates/bb_v3/create", {});
    expect(mockStore.setOptimizationCalls).toHaveLength(1);
    expect(mockStore.setOptimizationCalls[0]).toMatchObject({
      source: "tuning",
      taskId: "task-xyz",
      sharpe: 1.85,
      interval: "4h",
      symbol: "BTC/USDT",
    });
    expect(pushMock).toHaveBeenCalledWith("/trade/backtests");
  });

  it("#3 失败路径: apiPost reject → console.error,不 push,不 setOptimization", async () => {
    const err = new Error("network");
    apiPostMock.mockRejectedValueOnce(err);
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const r: TuningResultSummary = {
      file_name: "x.json", timestamp: "", strategy_id: "rsi_ma_delta", strategy_name: "RSI",
      interval: "1d", exchange: "okx", market_type: "spot", symbol: "ETH/USDT",
      best_sharpe: 1.2, best_params: {}, total_return: 0.1, max_drawdown: 0.05,
      win_rate: 0.5, n_trials: 10, optimization_time: 30,
    };
    const router = { push: pushMock } as unknown as Parameters<typeof applyTuningResult>[1];
    const setOptimization = (x: unknown) => { mockStore.setOptimizationCalls.push(x as Record<string, unknown>); };
    await applyTuningResult(r, router, setOptimization as Parameters<typeof applyTuningResult>[2]);
    expect(apiPostMock).toHaveBeenCalled();
    expect(mockStore.setOptimizationCalls).toHaveLength(0);
    expect(pushMock).not.toHaveBeenCalled();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe("P11+P12 strat-page section 渲染", () => {
  it("#4 3 section 标题渲染 (策略模板库 + Alpha 挖矿策略 + 最近调参结果)", async () => {
    mockApiData["/api/v1/strategies/factory-templates"] = [];
    mockApiData["/api/v1/alpha/saved-strategies"] = { strategies: [] };
    mockApiData["/api/v1/research/tuning/results"] = [];
    render(<StratPage />);
    await waitFor(() => {
      expect(screen.getByText(/策略模板库/)).toBeInTheDocument();
      expect(screen.getByText(/Alpha 挖矿策略/)).toBeInTheDocument();
      expect(screen.getByText(/最近调参结果/)).toBeInTheDocument();
    });
  });

  it("#5 点 Alpha 卡片 → selectTemplate(category=alpha) + 跳 /research/factors", async () => {
    mockApiData["/api/v1/strategies/factory-templates"] = [];
    mockApiData["/api/v1/alpha/saved-strategies"] = {
      strategies: [{
        name: "MyAlpha",
        version: "0.2.0",
        formula_readable: "rsi < 30",
        formula_tokens: [1, 2],
        profiles: [{ base_interval: "15m" }],
      } as SavedStrategy],
    };
    mockApiData["/api/v1/research/tuning/results"] = [];
    render(<StratPage />);
    await waitFor(() => expect(screen.getByText("MyAlpha")).toBeInTheDocument());
    fireEvent.click(screen.getByText("MyAlpha"));
    expect(mockStore.selectTemplateCalls).toHaveLength(1);
    expect(mockStore.selectTemplateCalls[0]).toEqual({
      id: "MyAlpha@0.2.0",
      name: "MyAlpha",
      category: "alpha",
    });
    expect(pushMock).toHaveBeenCalledWith("/research/factors");
  });
});

describe("US-3 部署列表三态过滤", () => {
  const mk = (
    id: string,
    status: string,
  ): DeploymentSummary => ({ id, strategy_id: `strat-${id}`, status });

  const LIST: DeploymentSummary[] = [
    mk("a", "running"),
    mk("b", "running"),
    mk("c", "paused"),
    mk("d", "stopped"),
  ];

  const pick = (key: DeployFilterKey) =>
    DEPLOY_FILTERS.find((f) => f.key === key)!.match(LIST).map((d) => d.id);

  it("#6 三态过滤: 全部/运行中/需关注 计数与集合正确", () => {
    expect(pick("all")).toEqual(["a", "b", "c", "d"]);
    expect(pick("running")).toEqual(["a", "b"]);
    // 需关注 = 非 running(暂停+停止),后端无 error 字段
    expect(pick("attention")).toEqual(["c", "d"]);
  });

  it("#7 空列表三态都返回空,不抛错", () => {
    for (const f of DEPLOY_FILTERS) {
      expect(f.match([])).toEqual([]);
    }
  });

  it("#8 未知 status 归入需关注(status !== running)", () => {
    const weird: DeploymentSummary[] = [mk("x", "starting")];
    const att = DEPLOY_FILTERS.find((f) => f.key === "attention")!;
    expect(att.match(weird).map((d) => d.id)).toEqual(["x"]);
  });
});

describe("P0 状态诚实: 失败渲染成「加载失败」而非假「暂无」", () => {
  it("#9 error 通道有值 → 卡片渲染「加载失败」+ 重试按钮,不渲染「暂无」", async () => {
    mockApiData["/api/v1/alpha/saved-strategies"] = null;
    mockApiErr["/api/v1/alpha/saved-strategies"] = new Error("HTTP 500");
    swrState["/api/v1/strategies/deployments/list"] = { error: new Error("HTTP 500") };
    render(<StratPage />);
    await waitFor(() => {
      const errs = screen.getAllByText(/加载失败/);
      // 模板库/Alpha/调参/QD 卡未喂数据但 mock 无 error → 只有 Alpha + 部署两张卡失败
      expect(errs.length).toBe(2);
    });
    expect(screen.getAllByRole("button", { name: "重试" }).length).toBe(2);
    // 假「暂无已部署策略」不许出现
    expect(screen.queryByText(/暂无已部署策略/)).not.toBeInTheDocument();
  });

  it("#10 无 error + 空数据 → 维持「暂无」教学空态(不误伤正常路径)", async () => {
    mockApiData["/api/v1/strategies/factory-templates"] = [];
    mockApiData["/api/v1/alpha/saved-strategies"] = { strategies: [] };
    mockApiData["/api/v1/research/tuning/results"] = [];
    swrState["/api/v1/strategies/deployments/list"] = { data: [] };
    render(<StratPage />);
    await waitFor(() => {
      expect(screen.getByText(/暂无已部署策略/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/加载失败/)).not.toBeInTheDocument();
  });
});

describe("部署行级入口:无 strategy_name 时 id 只出现一次", () => {
  it("#11 标题回退用 id 时,右侧不再重复渲染同一个 id", async () => {
    mockApiData["/api/v1/strategies/factory-templates"] = [];
    mockApiData["/api/v1/alpha/saved-strategies"] = { strategies: [] };
    mockApiData["/api/v1/research/tuning/results"] = [];
    // strategy_name 缺失 → 标题行回退显示 strategy_id
    const noName = { id: "dep-1", strategy_id: "strat-96007810", status: "running" };
    swrState["/api/v1/strategies/deployments/list"] = {
      data: [noName, { ...noName, id: "dep-2", strategy_name: "布林带升级版" }],
    };
    render(<StratPage />);
    const rows = await screen.findAllByTitle("打开策略工作台(参数/版本/监控/回测)");
    expect(rows).toHaveLength(2);

    const [fallback, named] = rows;
    // 回退行:标题是 id,id 列必须消失 —— 否则读起来像 "strat-96..strat-96.."
    expect(fallback.textContent).toContain("strat-96007810");
    expect(fallback.textContent?.match(/strat-96007810/g)).toHaveLength(1);
    // 有名字的行不受影响:名字 + 右侧 id 各一次
    expect(named.textContent).toContain("布林带升级版");
    expect(named.textContent?.match(/strat-96007810/g)).toHaveLength(1);
  });
});
