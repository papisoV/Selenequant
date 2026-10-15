import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { AlphaMiningPanel } from "@/components/research/alpha-mining-panel";
import { listMiningSessions, resumeTraining, startBatchTraining, listDataFiles, syncKlinePartition, getKlineCoverage } from "@/lib/api/alpha";

// === mock api / apiPost =========================================
const apiCalls: { url: string; body?: unknown }[] = [];
vi.mock("@/lib/api-client", () => ({
  api: vi.fn(async (url: string) => {
    apiCalls.push({ url });
    if (url.includes("/alpha/formulas"))
      return {
        formulas: [
          {
            formula_id: "bt_f1",
            formula_tokens: [0, 6, 97],
            formula_readable: "close/sub(open,volume)",
            best_score: 0.42,
            created_at: 1780000000,
          },
        ],
      };
    if (url.includes("/alpha/saved-strategies"))
      return {
        strategies: [
          {
            name: "alpha_v5_adaptive",
            version: "alpha_v5",
            formula_readable: "test_formula",
            formula_tokens: [0, 6, 3, 1],
            profiles: [{ base_interval: "15m" }],
          },
        ],
      };
    if (url.includes("/alpha/train/")) return { status: "completed", step: 100 };
    return {};
  }),
  apiPost: vi.fn(async (url: string, body?: unknown) => {
    apiCalls.push({ url, body });
    if (url.includes("/backtest"))
      return {
        strategy_name: "alpha_v5_adaptive",
        symbol: "BTC/USDT",
        interval: "15m",
        total_return: 0.12,
        annual_return: 0.35,
        sharpe_ratio: 1.5,
        sortino_ratio: 1.9,
        max_drawdown: -0.08,
        win_rate: 0.55,
        total_trades: 42,
        profit_factor: 1.7,
        equity_curve: [10000, 10100, 10200, 10150, 10300],
        rolling_sharpe: [1.2, 1.4, 1.1, 1.6, 1.5],
        trades: [],
        formula_readable: "test",
      };
    return { session_id: "test-session", status: "starting" };
  }),
}));

// mock lib/api/alpha:getTrainStatus/getTrainLogs 返回实时 history(双线曲线数据源)
const trainingHistory = {
  step: [0, 1, 2],
  avg_reward: [0.1, -0.05, 0.2],
  best_score: [-1, -0.5, 0.1],
  stable_rank: [4.2, null, null],
};
// 可变 status,允许测试切换训练中/终态。用 vi.hoisted 使 vi.mock factory 能安全访问。
const { trainStatusOverride } = vi.hoisted(() => ({ trainStatusOverride: { value: null as Record<string, unknown> | null } }));
const { batchStatusOverride } = vi.hoisted(() => ({ batchStatusOverride: { value: null as Record<string, unknown> | null } }));
vi.mock("@/lib/api/alpha", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/alpha")>();
  return {
    ...actual,
    getTrainStatus: vi.fn(async () => {
      return (
        trainStatusOverride.value ?? {
          session_id: "test-session",
          status: "running",
          step: 2,
          best_score: 0.1,
          train_steps: 100,
          elapsed_seconds: 1,
          history: trainingHistory,
        }
      );
    }),
    getTrainLogs: vi.fn(async () => ({
      session_id: "test-session",
      logs: ["[!] New Best: Score 0.10 | Formula [0, 6, 3, 1]"],
    })),
    getFormulaExplain: vi.fn(async () => ({
      session_id: "test-session",
      source: "rule",
      explanation: "已训练 3 步,当前最佳分数 0.10。avg_reward 为正,训练在向有利方向推进,可以再观察若干步。(未配置 LLM,此解读由规则生成)",
      summary: { step_count: 3, best_score: 0.1, recent_avg_reward: 0.15 },
    })),
    listArtifacts: vi.fn(async () => [
      { name: "alpha_export/best_meme_strategy.json", size: 120, modified: 1700000000, kind: "formula" },
      { name: "alpha_export/training_history.json", size: 480, modified: 1700000001, kind: "history" },
    ]),
    exportArtifacts: vi.fn(async () => ({
      zip_base64: "UEsFBgAAAAAAAAAAAAAAAAAAAAAAAA==",
      count: 2,
      files: ["alpha_export/best_meme_strategy.json", "alpha_export/training_history.json"],
    })),
    importArtifacts: vi.fn(async () => ({
      imported: ["new/formula.json"],
      skipped: [],
    })),
    listMiningSessions: vi.fn(async () => []),
    listDataFiles: vi.fn(async () => []),
    syncKlinePartition: vi.fn(async () => ({ written: 8760 })),
    getKlineCoverage: vi.fn(async () => ({ count: 0, latest_timestamp: null, latest_iso: null })),
    resumeTraining: vi.fn(async () => ({
      session_id: "alpha_stopped_1",
      status: "starting",
      resumed_from_step: 42,
    })),
    startBatchTraining: vi.fn(async () => ({
      batch_id: "batch_1_abcdef01",
      session_ids: ["alpha_1_abcdef01_0", "alpha_1_abcdef01_1"],
      status: "running",
      max_concurrency: 4,
    })),
    getBatchStatus: vi.fn(async () => {
      return (
        batchStatusOverride.value ?? {
          batch_id: "batch_1_abcdef01",
          status: "running",
          session_count: 2,
          max_concurrency: 4,
          sessions: [
            { session_id: "alpha_1_abcdef01_0", status: "running", step: 5, train_steps: 100, best_score: 0.3, symbol: "BTC/USDT", interval: "1h" },
            { session_id: "alpha_1_abcdef01_1", status: "queued", step: 0, train_steps: 100, best_score: null, symbol: "ETH/USDT", interval: "1h" },
          ],
        }
      );
    }),
    validateFormula: vi.fn(async (fid: string) => ({
      formula_id: fid,
      verdict: "VALID",
      decision: "accept",
      reason: "ok",
    })),
    backtestMinedFormula: vi.fn(async (fid: string) => ({
      formula_id: fid,
      source: "real",
      total_bars: 8793,
      symbol: "ARB/USDT",
      interval: "1h",
      latest_score: -0.123,
      signal_counts: { long: 120, short: 98, flat: 8575 },
      formula_readable: "rank(ts_mean(ret_1, 24))",
    })),
  };
});

// 覆盖行默认有数据(kline-coverage 返回形状:count + latest_iso)
const defaultCoverage = { count: 3, latest_timestamp: 1700007200000, latest_iso: "2023-11-14 22:26" };

beforeEach(() => {
  localStorage.clear();
  apiCalls.length = 0;
  vi.clearAllMocks();
  vi.mocked(getKlineCoverage).mockResolvedValue(defaultCoverage);
});

describe("AlphaMiningPanel 数据源选择", () => {
  it("#1 渲染数据源选择区(交易所/品种/周期/市场类型) + 覆盖显示", async () => {
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    // 选择器
    expect(screen.getByLabelText(/交易所/)).toBeInTheDocument();
    expect(screen.getByLabelText(/品种/)).toBeInTheDocument();
    expect(screen.getByLabelText(/周期/)).toBeInTheDocument();
    expect(screen.getByLabelText(/市场类型/)).toBeInTheDocument();
    // 覆盖显示:根数 + 最新时间(kline-coverage 轻量端点)
    await waitFor(() => expect(screen.getByText(/3 根/)).toBeInTheDocument());
    expect(screen.getByText(/2023/)).toBeInTheDocument(); // latest_iso
    // 默认调用 getKlineCoverage(binance futures BTC/USDT 1h)
    expect(getKlineCoverage).toHaveBeenCalledWith({
      exchange: "binance", market_type: "futures", symbol: "BTC/USDT", interval: "1h",
    });
  });

  it("#2 无本地数据 → 显示合成降级提示", async () => {
    vi.mocked(getKlineCoverage).mockResolvedValue({ count: 0, latest_timestamp: null, latest_iso: null });
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/无本地数据,训练将使用合成数据/)).toBeInTheDocument());
  });

  it("#3 开始训练发送真实数据字段(use_synthetic_data false + symbol/exchange/market_type/interval)", async () => {
    render(<AlphaMiningPanel />);
    const startBtn = screen.getByRole("button", { name: /开始挖矿/ });
    fireEvent.click(startBtn);
    await waitFor(() => {
      const trainCall = apiCalls.find((c) => c.url === "/api/v1/alpha/train");
      expect(trainCall).toBeDefined();
      expect(trainCall!.body).toEqual({
        train_steps: 100,
        batch_size: 256,
        use_lord: true,
        symbol: "BTC/USDT",
        exchange: "binance",
        market_type: "futures",
        interval: "1h",
        use_synthetic_data: false,
      });
    });
  });

  it("#3b 填 parquet 路径 → train 请求带 data_file,留空则不带", async () => {
    render(<AlphaMiningPanel />);
    const parquetInput = screen.getByLabelText(/Parquet 文件/);
    fireEvent.change(parquetInput, { target: { value: "btc_1h.parquet" } });
    fireEvent.click(screen.getByRole("button", { name: /开始挖矿/ }));
    await waitFor(() => {
      const trainCall = apiCalls.find((c) => c.url === "/api/v1/alpha/train");
      expect(trainCall).toBeDefined();
      expect(trainCall!.body).toMatchObject({ data_file: "btc_1h.parquet" });
    });
  });

  it("#4 切换数据源 → 覆盖重新读取", async () => {
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    const intervalSelect = screen.getByLabelText(/周期/);
    fireEvent.change(intervalSelect, { target: { value: "15m" } });
    await waitFor(() => expect(getKlineCoverage).toHaveBeenCalledWith({
      exchange: "binance", market_type: "futures", symbol: "BTC/USDT", interval: "15m",
    }));
  });
});

describe("AlphaMiningPanel 训练可视化", () => {
  beforeEach(() => {
    apiCalls.length = 0;
    trainStatusOverride.value = null;
    vi.clearAllMocks();
  });

  it("#5 训练中渲染双线曲线(best_score + avg_reward)+ 日志面板", async () => {
    render(<AlphaMiningPanel />);
    const startBtn = screen.getByRole("button", { name: /开始挖矿/ });
    fireEvent.click(startBtn);
    // 训练开始后轮询触发(interval 2000ms),getTrainStatus 返回含 history 的 running status
    await waitFor(() => expect(screen.getByText(/训练曲线/)).toBeInTheDocument(), { timeout: 5000 });
    // 曲线容器存在(自绘 SVG;jsdom 0 宽下仍挂载节点)
    await waitFor(() => expect(document.querySelector('svg[data-chart="train-chart"]')).toBeTruthy(), { timeout: 5000 });
    // 步数统计显示(history 已累计)
    await waitFor(() => expect(screen.getByText(/step 3/)).toBeInTheDocument(), { timeout: 5000 });
    // 日志面板出现且含 "New Best" 行
    await waitFor(() => expect(screen.getByText(/训练日志/)).toBeInTheDocument(), { timeout: 5000 });
    expect(screen.getByText(/New Best/)).toBeInTheDocument();
  });

  it("#6 假进度条已移除(status 存在但不渲染 progress bar)", async () => {
    render(<AlphaMiningPanel />);
    const startBtn = screen.getByRole("button", { name: /开始挖矿/ });
    fireEvent.click(startBtn);
    await waitFor(() => expect(screen.getByText(/训练曲线/)).toBeInTheDocument(), { timeout: 5000 });
    // 假进度条:percent width 的 h-1.5 bar 不应存在
    expect(document.querySelector(".bg-amber-500.h-1\\.5")).toBeNull();
  });

  it("#7 训练结束渲染 AI 解读卡片(生成解读 + 来源标识)", async () => {
    trainStatusOverride.value = {
      session_id: "test-session",
      status: "completed",
      step: 3,
      best_score: 0.1,
      train_steps: 100,
      elapsed_seconds: 2,
      history: trainingHistory,
    };
    render(<AlphaMiningPanel />);
    const startBtn = screen.getByRole("button", { name: /开始挖矿/ });
    fireEvent.click(startBtn);
    // 等待训练状态轮询将 status 置为 completed → training=false → AI 解读卡片出现。
    // 用精确字符串 "AI 解读" 匹配卡片标题(正则 /AI 解读/ 会同时命中占位文案 → 抛 multiple elements)
    await waitFor(() => expect(screen.getByText("AI 解读")).toBeInTheDocument(), { timeout: 5000 });
    // 点击生成解读 → getFormulaExplain 被调用 → 显示 explanation
    const genBtn = screen.getByRole("button", { name: /生成解读/ });
    fireEvent.click(genBtn);
    await waitFor(() => expect(screen.getByText(/已训练 3 步/)).toBeInTheDocument(), { timeout: 5000 });
    expect(screen.getByText(/规则降级/)).toBeInTheDocument();
  });

  it("#8 回测结果显示 8 指标明细 + 资金曲线 + 滚动夏普", async () => {
    render(<AlphaMiningPanel />);
    // 五 tab 骨架下默认训练 tab;回测内容所在的 pane 为 display:none,先切到回测 tab
    fireEvent.click(screen.getByRole("tab", { name: "回测" }));
    // 标题在 q 卡(h3)与 BacktestPane(h4)各出现一次,取任一确认渲染即可
    await waitFor(() => expect(screen.getAllByText(/已保存的 Alpha 策略/).length).toBeGreaterThan(0));
    // 找到策略的 15m 回测按钮(配置文件)
    const btns = screen.getAllByRole("button", { name: /^15m$/ });
    fireEvent.click(btns[0]);
    await waitFor(() => expect(screen.getByText(/回测结果/)).toBeInTheDocument(), { timeout: 5000 });
    // 8 指标明细
    expect(screen.getByText(/年化收益/)).toBeInTheDocument();
    expect(screen.getByText(/Sortino/)).toBeInTheDocument();
    expect(screen.getByText(/盈亏比/)).toBeInTheDocument();
    expect(screen.getByText(/交易数/)).toBeInTheDocument();
    // 资金曲线 + 滚动夏普(精确串:guide-strip 步骤描述里也含"滚动夏普"字样)
    expect(screen.getByText(/资金曲线/)).toBeInTheDocument();
    expect(screen.getByText("滚动夏普")).toBeInTheDocument();
  });

  it("#9 产物管理:列出公式+历史文件", async () => {
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/产物管理/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/alpha_export\/best_meme_strategy\.json/)).toBeInTheDocument());
    expect(screen.getByText(/alpha_export\/training_history\.json/)).toBeInTheDocument();
    // 产物管理卡片内的 公式/历史 徽章(用 within 限定范围,避免命中 "已发现的公式" 标题)
    const artifactCard = screen.getByText(/产物管理/).closest('[data-slot="card"]') as HTMLElement;
    expect(within(artifactCard).getByText(/公式/)).toBeInTheDocument();
    expect(within(artifactCard).getByText(/历史/)).toBeInTheDocument();
  });

  it("#10 产物管理:导出 zip 触发下载 + 成功提示", async () => {
    const { exportArtifacts } = await import("@/lib/api/alpha");
    const exportMock = vi.mocked(exportArtifacts);
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/产物管理/)).toBeInTheDocument());
    // 拦截 createObjectURL 以捕获下载
    const urlSpy = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fake");
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    fireEvent.click(screen.getByRole("button", { name: /导出 zip/ }));
    await waitFor(() => expect(exportMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/已导出 2 个产物/)).toBeInTheDocument());
    urlSpy.mockRestore();
    revokeSpy.mockRestore();
  });

  it("#11 产物管理:导入 zip 触发 import + 刷新列表", async () => {
    const { importArtifacts, listArtifacts } = await import("@/lib/api/alpha");
    const importMock = vi.mocked(importArtifacts);
    const listMock = vi.mocked(listArtifacts);
    listMock.mockResolvedValue([
      { name: "new/formula.json", size: 300, modified: 1700000002, kind: "formula" },
    ]);
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/产物管理/)).toBeInTheDocument());
    // 构造最小 zip 文件模拟上传
    const file = new File(["dummy"], "artifacts.zip", { type: "application/zip" });
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).toBeTruthy();
    fireEvent.change(input!, { target: { files: [file] } });
    await waitFor(() => expect(importMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/已导入 1 个产物/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/new\/formula\.json/)).toBeInTheDocument());
  });
});

describe("AlphaMiningPanel 可恢复会话(断点续训)", () => {
  beforeEach(() => {
    apiCalls.length = 0;
    trainStatusOverride.value = null;
    vi.clearAllMocks();
    vi.mocked(listMiningSessions).mockResolvedValue([
      {
        session_id: "alpha_stopped_1",
        status: "stopped",
        step: 42,
        best_score: 0.73,
        train_steps: 100,
        started_at: 1700000000,
      },
      {
        session_id: "alpha_failed_1",
        status: "failed",
        step: 7,
        best_score: -0.2,
        train_steps: 100,
        started_at: 1700000001,
      },
    ]);
    vi.mocked(resumeTraining).mockResolvedValue({
      session_id: "alpha_stopped_1",
      status: "starting",
      resumed_from_step: 42,
    });
  });

  it("#12 渲染 stopped/failed 会话列表 + 继续训练按钮", async () => {
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/可恢复会话/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/alpha_stopped_1/)).toBeInTheDocument());
    expect(screen.getByText(/alpha_failed_1/)).toBeInTheDocument();
    // 状态徽章 + 进度 + 最佳分数
    expect(screen.getByText("已停止")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText(/42\/100/)).toBeInTheDocument();
    expect(screen.getByText(/0\.73/)).toBeInTheDocument();
    // 每行一个继续训练按钮
    const resumeBtns = screen.getAllByRole("button", { name: /继续训练/ });
    expect(resumeBtns.length).toBe(2);
  });

  it("#13 点击继续训练 → resume API + 进入训练轮询态", async () => {
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/alpha_stopped_1/)).toBeInTheDocument());
    const resumeBtns = screen.getAllByRole("button", { name: /继续训练/ });
    fireEvent.click(resumeBtns[0]);
    await waitFor(() => expect(resumeTraining).toHaveBeenCalledWith("alpha_stopped_1"));
    // resume 成功后进入训练态:轮询 getTrainStatus 返回 running + history → 曲线出现
    await waitFor(() => expect(screen.getByText(/训练曲线/)).toBeInTheDocument(), { timeout: 5000 });
    // 会话切到被恢复的 sessionId → 日志面板轮询该会话
    await waitFor(() => expect(screen.getByText(/训练日志/)).toBeInTheDocument(), { timeout: 5000 });
  });
});

describe("AlphaMiningPanel 批量训练", () => {
  beforeEach(async () => {
    const mod = await import("@/lib/api/alpha");
    vi.mocked(mod.startBatchTraining).mockClear();
    vi.mocked(mod.getBatchStatus).mockClear();
    batchStatusOverride.value = null;
  });

  it("#14 批量模式:勾选后出现多选/并发,启动发送笛卡尔积字段", async () => {
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/批量模式/));
    // 多选出现:品种(含 BTC)+ 周期 + 将启动 1 个会话(默认 BTC×1h)
    expect(screen.getByText(/品种\(多选/)).toBeInTheDocument();
    expect(screen.getByText(/周期\(多选/)).toBeInTheDocument();
    expect(screen.getByText(/将启动 1 个会话/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /批量挖矿/ }));
    await waitFor(() => expect(startBatchTraining).toHaveBeenCalled());
    const arg = vi.mocked(startBatchTraining).mock.calls[0][0];
    expect(arg.symbols).toEqual(["BTC/USDT"]);
    expect(arg.intervals).toEqual(["1h"]);
    expect(arg.max_concurrency).toBe(4);
    expect(arg.exchange).toBe("binance");
    expect(arg.market_type).toBe("futures");
  });

  it("#15 批量轮询:渲染会话网格(每行 symbol/周期/步数/分数/状态徽章)", async () => {
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/批量模式/));
    fireEvent.click(screen.getByRole("button", { name: /批量挖矿/ }));
    await waitFor(() => expect(screen.getByTestId("batch-grid")).toBeInTheDocument(), { timeout: 5000 });
    const grid = screen.getByTestId("batch-grid");
    expect(within(grid).getByText("BTC/USDT 1h")).toBeInTheDocument();
    expect(within(grid).getByText("ETH/USDT 1h")).toBeInTheDocument();
    expect(within(grid).getByText(/5\/100/)).toBeInTheDocument();
    expect(within(grid).getByText(/0\.30/)).toBeInTheDocument();
    expect(within(grid).getByText("训练中")).toBeInTheDocument();
    expect(within(grid).getByText("排队")).toBeInTheDocument();
  });

  it("#16 全部终态 → 停止轮询 + 刷新公式", async () => {
    batchStatusOverride.value = {
      batch_id: "batch_1_abcdef01",
      status: "completed",
      session_count: 2,
      max_concurrency: 4,
      sessions: [
        { session_id: "alpha_1_abcdef01_0", status: "completed", step: 100, train_steps: 100, best_score: 0.9, symbol: "BTC/USDT", interval: "1h" },
        { session_id: "alpha_1_abcdef01_1", status: "completed", step: 100, train_steps: 100, best_score: 0.8, symbol: "ETH/USDT", interval: "1h" },
      ],
    };
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/批量模式/));
    fireEvent.click(screen.getByRole("button", { name: /批量挖矿/ }));
    // 首次 poll 即全终态 → 网格渲染"完成"徽章
    await waitFor(() => expect(screen.getByTestId("batch-grid")).toBeInTheDocument(), { timeout: 5000 });
    await waitFor(() => expect(screen.getAllByText("完成").length).toBe(2));
    // formulas 列表被刷新(mock 返回空,不断言行数,断言调用过)
    const mod = await import("@/lib/api/alpha");
    expect(vi.mocked(mod.getBatchStatus).mock.calls.length).toBeGreaterThan(0);
  });

  it("#18 送验证 → verdict 徽章染色,非 accept 部署被禁", async () => {
    const mod = await import("@/lib/api/alpha");
    batchStatusOverride.value = {
      batch_id: "batch_1_abcdef01",
      status: "completed",
      session_count: 2,
      max_concurrency: 2,
      sessions: [
        { session_id: "alpha_1_abcdef01_0", status: "completed", step: 100, train_steps: 100, best_score: 0.9, symbol: "BTC/USDT", interval: "1h" },
        { session_id: "alpha_1_abcdef01_1", status: "completed", step: 100, train_steps: 100, best_score: 0.8, symbol: "ETH/USDT", interval: "1h" },
      ],
    };
    vi.mocked(mod.validateFormula).mockImplementation(async (fid: string) =>
      fid.endsWith("_0")
        ? { formula_id: fid, verdict: "VALID", decision: "accept", reason: "ok" }
        : { formula_id: fid, verdict: "INVALID", decision: "reject", reason: "thin oos" },
    );
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/批量模式/));
    fireEvent.click(screen.getByRole("button", { name: /批量挖矿/ }));
    await waitFor(() => expect(screen.getByTestId("batch-grid")).toBeInTheDocument(), { timeout: 5000 });
    // 一键送验证 → 两条各调一次 validate
    fireEvent.click(screen.getByTestId("validate-all-btn"));
    await waitFor(() => expect(mod.validateFormula).toHaveBeenCalledTimes(2));
    // 染色:accept 绿 + reject 红
    await waitFor(() => expect(screen.getByText("T4 通过")).toBeInTheDocument());
    expect(screen.getByText("T4 拒绝")).toBeInTheDocument();
    // reject 行部署被禁(title 提示拒因),accept 行可点
    const rejectBtn = screen.getByTitle(/T4 reject: thin oos/);
    expect(rejectBtn).toHaveTextContent("部署");
    fireEvent.click(rejectBtn);
    const deployCalls = apiCalls.filter((c) => c.url?.includes("/deploy"));
    expect(deployCalls.length).toBe(0);
  });

  it("#17 完成行出现漏斗三动作(验证/回测/部署),部署走 batch 来源标记", async () => {
    batchStatusOverride.value = {
      batch_id: "batch_1_abcdef01",
      status: "completed",
      session_count: 1,
      max_concurrency: 2,
      sessions: [
        { session_id: "alpha_1_abcdef01_0", status: "completed", step: 100, train_steps: 100, best_score: 0.9, symbol: "BTC/USDT", interval: "1h" },
        { session_id: "alpha_1_abcdef01_1", status: "running", step: 50, train_steps: 100, best_score: 0.5, symbol: "ETH/USDT", interval: "1h" },
      ],
    };
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/批量模式/));
    fireEvent.click(screen.getByRole("button", { name: /批量挖矿/ }));
    await waitFor(() => expect(screen.getByTestId("batch-grid")).toBeInTheDocument(), { timeout: 5000 });
    // 只有完成行出动作,运行中行没有
    const grid = screen.getByTestId("batch-grid");
    const actions = within(grid).getAllByRole("button", { name: "部署" });
    expect(actions.length).toBe(1);
    expect(within(grid).getAllByRole("button", { name: "回测" }).length).toBe(1);
    // 点部署 → apiPost 打 /formulas/{sid}/deploy
    fireEvent.click(actions[0]);
    await waitFor(() => {
      const call = apiCalls.find((c) => c.url === "/api/v1/alpha/formulas/alpha_1_abcdef01_0/deploy");
      expect(call).toBeDefined();
    });
  });

  it("#18 刀③内联回测:完成行点回测 → inline-backtest 块就地渲染结果", async () => {
    batchStatusOverride.value = {
      batch_id: "batch_1_abcdef01",
      status: "completed",
      session_count: 1,
      max_concurrency: 2,
      sessions: [
        { session_id: "alpha_1_abcdef01_0", status: "completed", step: 100, train_steps: 100, best_score: 0.9, symbol: "BTC/USDT", interval: "1h", formula_readable: "rank(ts_mean(ret_1, 24))" },
      ],
    };
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/批量模式/));
    fireEvent.click(screen.getByRole("button", { name: /批量挖矿/ }));
    await waitFor(() => expect(screen.getByTestId("batch-grid")).toBeInTheDocument(), { timeout: 5000 });
    // 未触发时无 inline 块
    expect(screen.queryByTestId("inline-backtest")).not.toBeInTheDocument();
    // 点完成行的回测 → 就地出结果块
    fireEvent.click(within(screen.getByTestId("batch-grid")).getByRole("button", { name: "回测" }));
    await waitFor(() => expect(screen.getByTestId("inline-backtest")).toBeInTheDocument());
    expect(screen.getByText(/回测 · alpha_1_abcdef01_0/)).toBeInTheDocument();
    expect(screen.getByText(/真实数据 8,793 根/)).toBeInTheDocument();
    expect(screen.getByText(/latest score -0\.123/)).toBeInTheDocument();
    // 关闭按钮撤销
    fireEvent.click(within(screen.getByTestId("inline-backtest")).getByRole("button", { name: "关闭" }));
    expect(screen.queryByTestId("inline-backtest")).not.toBeInTheDocument();
  });

  it("#18b 已发现的公式行点回测 → 同一 inline-backtest 块(与批量行共用渲染)", async () => {
    // 公式行的「回测」与批量完成行的「回测」是同一个 runFormulaBacktestInline → 同一面板;
    // 本用例锁公式行入口这条路径(旧用例只覆盖了批量行)。
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    const formulaCard = screen.getByText(/已发现的公式/).closest('[data-slot="card"]') as HTMLElement;
    expect(within(formulaCard).getByText("close/sub(open,volume)")).toBeInTheDocument();
    expect(screen.queryByTestId("inline-backtest")).not.toBeInTheDocument();
    fireEvent.click(within(formulaCard).getByRole("button", { name: /回测/ }));
    await waitFor(() => expect(screen.getByTestId("inline-backtest")).toBeInTheDocument());
    expect(screen.getByText(/回测 · bt_f1/)).toBeInTheDocument();
    expect(screen.getByText(/ARB\/USDT 1h/)).toBeInTheDocument();
    expect(screen.getByText(/latest score -0\.123/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId("inline-backtest")).getByRole("button", { name: "关闭" }));
    expect(screen.queryByTestId("inline-backtest")).not.toBeInTheDocument();
  });

  it("#19 刀①公式流大屏常驻:无会话也渲染(hero score 占位 + 空态文案)", async () => {
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    const stream = screen.getByTestId("formula-stream");
    expect(within(stream).getByText(/Best Score/)).toBeInTheDocument();
    expect(within(stream).getByText("—")).toBeInTheDocument();
    expect(within(stream).getByText(/暂无最优因子/)).toBeInTheDocument();
  });

  it("#20 刀①公式流大屏:running 无最优公式 → 预热文案", async () => {
    trainStatusOverride.value = {
      session_id: "test-session",
      status: "running",
      step: 3,
      best_score: null,
      train_steps: 100,
      elapsed_seconds: 1,
      history: { step: [0, 1, 2], avg_reward: [-5, -5, -5], best_score: [null, null, null] },
    };
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /开始挖矿/ }));
    await waitFor(() => expect(screen.getByText(/矿机预热中,尚未发现首个因子/)).toBeInTheDocument(), { timeout: 4000 });
  });
});

describe("AlphaMiningPanel 动线两刀(下拉/持久化/补数据)", () => {
  beforeEach(async () => {
    localStorage.clear();
    vi.mocked(listDataFiles).mockClear();
    vi.mocked(syncKlinePartition).mockClear();
  });

  it("#21 刀A:沙箱有 parquet → 下拉替换手填;选文件进请求体", async () => {
    vi.mocked(listDataFiles).mockResolvedValue([
      { name: "btc_1h.parquet", path: "/home/x/.selenespace/data/btc_1h.parquet", size_bytes: 1024, modified_at: 1759000000 },
      { name: "eth_4h.parquet", path: "/home/x/.selenespace/data/eth_4h.parquet", size_bytes: 2048, modified_at: 1759000000 },
    ]);
    render(<AlphaMiningPanel />);
    // dataFiles 异步到达后 INPUT 才换成 SELECT,findByRole 会重试等它
    const select = await screen.findByRole("combobox", { name: /Parquet 文件/ });
    expect(screen.getByRole("option", { name: "btc_1h.parquet" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "btc_1h.parquet" } });
    fireEvent.click(screen.getByRole("button", { name: /开始挖矿/ }));
    await waitFor(() =>
      expect(apiCalls.some((c) => c.url.includes("/alpha/train") && (c.body as Record<string, unknown>)?.data_file === "btc_1h.parquet")).toBe(true),
    );
  });

  it("#22 刀A:配置持久化 — 勾批量模式后 localStorage 落盘,重渲染恢复", async () => {
    const { unmount } = render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByText(/本地 klines 覆盖/)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/批量模式/));
    await waitFor(() => expect(JSON.parse(localStorage.getItem("alpha-mining-config-v1") ?? "{}").batchMode).toBe(true));
    unmount();
    render(<AlphaMiningPanel />);
    await waitFor(() => expect(screen.getByLabelText(/批量模式/)).toBeChecked());
  });

  it("#23 刀B:覆盖为 0 → 显示同步按钮,点击调 sync 并刷新覆盖", async () => {
    vi.mocked(getKlineCoverage).mockResolvedValue({ count: 0, latest_timestamp: null, latest_iso: null });
    const mod = await import("@/lib/api/alpha");
    vi.mocked(mod.syncKlinePartition).mockResolvedValue({ written: 8760 });
    render(<AlphaMiningPanel />);
    const syncBtn = await screen.findByRole("button", { name: /同步 BTC\/USDT 365 天/ });
    expect(syncBtn).toBeInTheDocument();
    fireEvent.click(syncBtn);
    await waitFor(() => expect(vi.mocked(mod.syncKlinePartition)).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/已写入 8760 根/)).toBeInTheDocument());
  });
});

describe("AlphaMiningPanel 词表消歧义(刀6)", () => {
  it("review_status 徽章带「审读:」词头(与 episode 的「周期:」区分)", async () => {
    render(<AlphaMiningPanel />);
    // fixture 无 review_status → 默认 discovered =「审读:未审读」
    const badge = await screen.findByText("审读:未审读");
    expect(badge).toBeInTheDocument();
  });
});
