import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { buildOverlays, PaPanel } from "@/components/research/pa-panel";
import type { SetupBacktestResult } from "@/lib/api/setup-backtest";

// ---------------------------------------------------------------------------
// Module mocks — PaPanel 的依赖全部替换，避免真实网络 / 真实 KfChart（lightweight-charts）。
// vi.mock 工厂提升到顶部，跨工厂共享的 mock 用 vi.hoisted 初始化。
// ---------------------------------------------------------------------------

const { sendAgentChatMock } = vi.hoisted(() => ({ sendAgentChatMock: vi.fn() }));
const { approveToolCallMock } = vi.hoisted(() => ({
  approveToolCallMock: vi.fn(async () => ({ ok: true }) as { ok: boolean; errorCode?: string }),
}));
const { runSetupBacktestMock } = vi.hoisted(() => ({ runSetupBacktestMock: vi.fn() }));
const { createExperimentMock } = vi.hoisted(() => ({ createExperimentMock: vi.fn() }));
const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));
const { analyzeTradingMock } = vi.hoisted(() => ({ analyzeTradingMock: vi.fn() }));
const { createPriceAlertMock } = vi.hoisted(() => ({ createPriceAlertMock: vi.fn() }));
const { getHeartbeatStatusMock } = vi.hoisted(() => ({ getHeartbeatStatusMock: vi.fn() }));
const { sendRecordFollowUpMock } = vi.hoisted(() => ({ sendRecordFollowUpMock: vi.fn() }));
const { listRecordsMock } = vi.hoisted(() => ({ listRecordsMock: vi.fn() }));
const { kfChartPropsMock } = vi.hoisted(() => ({ kfChartPropsMock: vi.fn() }));

vi.mock("@/lib/api/chat", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/chat")>("@/lib/api/chat");
  return { ...actual, sendAgentChat: sendAgentChatMock, approveToolCall: approveToolCallMock };
});

vi.mock("@/lib/api/setup-backtest", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/setup-backtest")>("@/lib/api/setup-backtest");
  return { ...actual, runSetupBacktest: runSetupBacktestMock };
});

vi.mock("@/lib/api/experiments", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/experiments")>("@/lib/api/experiments");
  return { ...actual, createExperiment: createExperimentMock };
});

vi.mock("@/lib/api/decision-workflow", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/decision-workflow")>("@/lib/api/decision-workflow");
  return {
    ...actual,
    analyzeTrading: analyzeTradingMock,
    createPriceAlert: createPriceAlertMock,
    getHeartbeatStatus: getHeartbeatStatusMock,
    sendRecordFollowUp: sendRecordFollowUpMock,
    listRecords: listRecordsMock,
  };
});

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: useApiMock,
}));

vi.mock("@/lib/store/market", () => ({
  useMarketStore: (selector: (s: unknown) => unknown) =>
    selector({ pageFilter: { tf: "1h", symbol: "BTC", exchange: "all" } }),
}));

vi.mock("@/components/watch/kf-chart", () => ({
  KfChart: (props: Record<string, unknown>) => {
    kfChartPropsMock(props);
    return <div data-testid="kf-chart" />;
  },
}));

vi.mock("@/components/strategy/markdown-rich", () => ({
  parseRichMarkdownToReact: (content: string) => <span>{content}</span>,
}));

// ---------------------------------------------------------------------------
// 测试数据 — 双阶段管线 AnalyzeResponse(Phase B 后"分析当前图表"走 analyzeTrading)
// ---------------------------------------------------------------------------

function makeRecord(outcome: "trade" | "wait" | "reject", overrides: Record<string, unknown> = {}) {
  const order = outcome === "trade"
    ? { direction: "long", entry: 100, stop_loss: 95, take_profit: 110 }
    : null;
  return {
    meta: { input_id: "pa-test-1", timestamp: "2026-09-10T12:00:00Z", session_id: "s-1" },
    input_data: {
      summary: {
        symbol: "BTC/USDT", interval: "1h", bars_total: 120,
        window_start_iso: "2026-09-09T12:00", window_end_iso: "2026-09-10T12:00",
        last_close: 99, atr14: 5,
      },
    },
    stage1: {
      diagnosis_or_decision: {
        state: outcome === "wait" ? "range" : "trend_up",
        gate_result: outcome === "wait" ? "wait" : "proceed",
        trace: [
          { node_id: "N1", question: "K线数据完整?", answer: "是", bar_range: "K120-1", reason: "120/120 无缺口", branch: "yes" },
          { node_id: "N2", question: "市场状态?", answer: "trend_up", bar_range: "K120-41", reason: "HH/HL 序列", branch: "trend_up" },
        ],
      },
    },
    stage2: outcome === "wait" ? null : {
      diagnosis_or_decision: {
        decision: outcome,
        terminal_outcome: outcome,
        order,
        trace: [
          { node_id: "N4", question: "硬性否决?", answer: outcome === "reject" ? "命中" : "无", bar_range: "K40-1", reason: outcome === "reject" ? "逆势信号" : "无逆势", branch: "n" },
        ],
        decision_summary: outcome === "reject" ? { reject_reason: "上一根放量长阴刺破前低,逆势做多否决" } : {},
      },
    },
    strategy_files_used: ["trading_decision.txt"],
    usage_total: { prompt_tokens: 1000, completion_tokens: 300, total_tokens: 1300 },
    ...overrides,
  };
}

const DECISION = {
  direction: "long",
  entry: 100,
  stop_loss: 95,
  take_profit: 110,
  confidence: 0.7,
  reason: "突破阻力位",
};

const FULL_RESULT: SetupBacktestResult = {
  total_setups: 12,
  win_rate: 0.5833,
  avg_win: 3.0,
  avg_loss: -1.0,
  expectancy: 1.333,
  profit_factor: 2.0,
  max_drawdown: 2.0,
  avg_bars_held: 3.5,
  rr_buckets: { "3": { count: 12, wins: 7, losses: 5, win_rate: 0.5833, expectancy: 1.333 } },
  trades: [],
  open_setups: 0,
  no_trigger: false,
  reason: null,
  symbol: "BTC/USDT",
  timeframe: "1h",
};

const NO_TRIGGER_RESULT: SetupBacktestResult = {
  total_setups: 0,
  win_rate: 0,
  avg_win: 0,
  avg_loss: 0,
  expectancy: 0,
  profit_factor: null,
  max_drawdown: 0,
  avg_bars_held: 0,
  rr_buckets: {},
  trades: [],
  open_setups: 0,
  no_trigger: true,
  reason: "entry 价位在数据范围内未触发",
  symbol: "BTC/USDT",
  timeframe: "1h",
};

// ---------------------------------------------------------------------------
// 辅助
// ---------------------------------------------------------------------------

// 默认 trade 形态:makeRecord 产出 stage2 order → decisionFromRecord 解出 PaDecision
async function renderWithDecision(
  outcome: "trade" | "wait" | "reject" = "trade",
  overrides: Record<string, unknown> = {},
) {
  analyzeTradingMock.mockResolvedValue(makeRecord(outcome, overrides));
  render(<PaPanel />);
  fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));
  if (outcome === "trade") {
    await screen.findByText(/做多 · long/);
  } else if (outcome === "wait") {
    await screen.findByText(/无入场信号 · 不生成交易指令/);
  } else {
    await screen.findByText(/逆势做多否决/);
  }
}

beforeEach(() => {
  sendAgentChatMock.mockReset();
  runSetupBacktestMock.mockReset();
  createExperimentMock.mockReset();
  analyzeTradingMock.mockReset();
  createPriceAlertMock.mockReset();
  sendRecordFollowUpMock.mockReset();
  // 最近诊断列表默认空(组件挂载即拉取;不 mock 会走真 fetch 使错误区渲染)
  listRecordsMock.mockReset();
  listRecordsMock.mockResolvedValue([]);
  // 心跳条默认不渲染 —— 大多数用例关心决策卡,不该被它多出来的 DOM/请求干扰
  getHeartbeatStatusMock.mockReset();
  getHeartbeatStatusMock.mockResolvedValue({ enabled: false });
  useApiMock.mockReset();
  useApiMock.mockReturnValue({ data: { last_price: 101.5 }, error: undefined, isLoading: false, mutate: vi.fn() });
  kfChartPropsMock.mockReset();
  Element.prototype.scrollIntoView = vi.fn();
});

describe("buildOverlays", () => {
  it("决策 + 现价 → 4 条线（现价/入场/SL/TP）", () => {
    const overlays = buildOverlays(
      { direction: "long", entry: 100, stop_loss: 95, take_profit: 110, confidence: 0.7, reason: "突破" },
      101.5,
    );
    expect(overlays).toHaveLength(4);
    expect(overlays.map((o) => o.title)).toEqual(["现价 101.5", "入场 100", "SL 95", "TP 110"]);
    expect(overlays[1].color).toBe("#e3b650");
    expect(overlays[2].color).toBe("#EF5350");
    expect(overlays[3].color).toBe("#10b981");
  });

  it("无决策 → 只有现价线", () => {
    const overlays = buildOverlays(null, 99);
    expect(overlays).toHaveLength(1);
    expect(overlays[0].title).toBe("现价 99");
  });

  it("null 决策 + 无现价 → 空", () => {
    expect(buildOverlays(null, null)).toEqual([]);
  });

  it("非正/NaN 价格被过滤，不画无效线", () => {
    const overlays = buildOverlays(
      { direction: "short", entry: 0, stop_loss: Number.NaN, take_profit: 120, confidence: 0.5, reason: "" },
      88,
    );
    // entry=0 过滤, SL=NaN 过滤, TP 保留 + 现价保留
    expect(overlays.map((o) => o.title)).toEqual(["现价 88", "TP 120"]);
  });
});

describe("PaPanel — setup 回测状态流转", () => {
  it("决策卡含'回测该形态'按钮；点击后加载态（按钮禁用+spinner），成功后渲染结果卡", async () => {
    let resolveBt!: (r: SetupBacktestResult) => void;
    runSetupBacktestMock.mockImplementation(
      () => new Promise<SetupBacktestResult>((resolve) => { resolveBt = resolve; }),
    );

    await renderWithDecision();

    const btBtn = screen.getByRole("button", { name: /回测该形态/ });
    expect(btBtn).toBeEnabled();

    fireEvent.click(btBtn);

    // 加载态：按钮禁用（避免重复提交）
    await waitFor(() => expect(screen.getByRole("button", { name: /回测该形态/ })).toBeDisabled());
    expect(runSetupBacktestMock).toHaveBeenCalledTimes(1);
    // 请求体透传 entry/SL/TP + 统一符号 + 关联诊断记录的 input_id(T2a 审计链)
    expect(runSetupBacktestMock).toHaveBeenCalledWith({
      symbol: "BTC/USDT",
      timeframe: "1h",
      exchange: "binance",
      market_type: "futures",
      entry: 100,
      stop_loss: 95,
      take_profit: 110,
      direction: "long",
      input_id: "pa-test-1",
    });

    act(() => { resolveBt(FULL_RESULT); });

    // 结果卡：样本数 / 胜率 / 盈亏比 / 期望 / 最大回撤 + R:R 分桶
    await screen.findByText(/历史回测 · 12 个样本/);
    // "胜率" 出现两处：指标格 + R:R 分桶行
    expect(screen.getAllByText(/胜率/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("58%")).toBeInTheDocument();
    expect(screen.getByText(/盈亏比/)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/期望/)).toBeInTheDocument();
    expect(screen.getByText("1.33R")).toBeInTheDocument();
    expect(screen.getByText(/最大回撤/)).toBeInTheDocument();
    expect(screen.getByText("2.00R")).toBeInTheDocument();
    // R:R 分桶
    expect(screen.getByText(/R:R 分桶/)).toBeInTheDocument();
    expect(screen.getByText("7 胜 / 5 负")).toBeInTheDocument();
  });

  it("无触发点（no_trigger）→ 明确提示'历史无此价位触发，样本不足'", async () => {
    runSetupBacktestMock.mockResolvedValue(NO_TRIGGER_RESULT);

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /回测该形态/ }));

    await screen.findByText(/历史无此价位触发，样本不足/);
    expect(runSetupBacktestMock).toHaveBeenCalledTimes(1);
  });

  it("回测失败 → 显示错误信息，按钮恢复可点", async () => {
    runSetupBacktestMock.mockRejectedValue(new Error("kline fetch failed"));

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /回测该形态/ }));

    await screen.findByText(/kline fetch failed/);
    expect(screen.getByRole("button", { name: /回测该形态/ })).toBeEnabled();
  });

  it("无决策时无'回测该形态'按钮", () => {
    render(<PaPanel />);
    expect(screen.queryByRole("button", { name: /回测该形态/ })).not.toBeInTheDocument();
  });

  it("回测后保存实验 → backtest_result 流入 createExperiment（接缝修复）", async () => {
    runSetupBacktestMock.mockResolvedValue(FULL_RESULT);
    createExperimentMock.mockResolvedValue({ id: "exp-saved" } as never);

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /回测该形态/ }));
    await screen.findByText(/历史回测 · 12 个样本/);

    fireEvent.click(screen.getByRole("button", { name: /保存为实验/ }));
    await waitFor(() => expect(createExperimentMock).toHaveBeenCalledTimes(1));

    const body = createExperimentMock.mock.calls[0][0];
    expect(body.backtest_result).toEqual(FULL_RESULT);
    expect(body.backtest_result.total_setups).toBe(12);
  });

  it("保存成功 → 显示'查看证据报告'链接指向 evidence 页", async () => {
    createExperimentMock.mockResolvedValue({ id: "exp-saved-1" } as never);

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /保存为实验/ }));
    await waitFor(() => expect(screen.getByText(/已保存/)).toBeInTheDocument());

    const link = screen.getByRole("link", { name: /查看证据报告/ }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/research/experiment-evidence?id=exp-saved-1");
  });
});

describe("PaDecisionRecordCard — 七态蓝本升级", () => {
  it("trade 态:order 金框(方向/R:R/三价位+ATR注记)+ 双链节点 + meta 行", async () => {
    await renderWithDecision("trade");

    // order 金框
    expect(screen.getByText(/做多 · long/)).toBeInTheDocument();
    expect(screen.getByText(/风险回报/)).toBeInTheDocument();
    expect(screen.getByText("1 : 2.0")).toBeInTheDocument(); // (110-100)/(100-95) = 2.0
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText("95")).toBeInTheDocument();
    expect(screen.getByText("110")).toBeInTheDocument();
    // ATR 注记:SL 距入场 -1.0 ATR(ATR14=5)
    expect(screen.getByText("-1.0 ATR")).toBeInTheDocument();
    expect(screen.getByText("+2.0 ATR")).toBeInTheDocument();
    // 快照
    expect(screen.getByText(/快照 BTC-PERP · 1小时/)).toBeInTheDocument();
    // 双链:Stage1 2 节点 + Stage2 1 节点
    expect(screen.getByText(/Stage 1 诊断链 · 2 节点/)).toBeInTheDocument();
    expect(screen.getByText(/Stage 2 决策链 · 1 节点/)).toBeInTheDocument();
    // meta 行
    expect(screen.getByText(/trading_decision.txt/)).toBeInTheDocument();
    expect(screen.getByText(/1000 入 \/ 300 出 \/ 共 1300/)).toBeInTheDocument();
    expect(screen.getByText(/ATR14/)).toBeInTheDocument();
  });

  it("wait 态:短路标记 + 无指令说明块 + 无 order + 保存区禁用文案", async () => {
    await renderWithDecision("wait");

    expect(screen.getByText(/Stage 2 未执行 · 短路/)).toBeInTheDocument();
    expect(screen.getByText(/无入场信号 · 不生成交易指令/)).toBeInTheDocument();
    // 只有 Stage 1 链
    expect(screen.getByText(/Stage 1 诊断链 · 2 节点/)).toBeInTheDocument();
    expect(screen.queryByText(/Stage 2 决策链/)).not.toBeInTheDocument();
    // 保存区被禁用文案替代
    expect(screen.queryByRole("button", { name: /保存为实验/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /回测该形态/ })).not.toBeInTheDocument();
    expect(screen.getByText(/无交易指令,等入场信号后再保存/)).toBeInTheDocument();
  });

  it("reject 态:否决徽章 + 否决块带 reject_reason + 保存区禁用文案", async () => {
    await renderWithDecision("reject");

    expect(screen.getAllByText(/否决/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/逆势做多否决/)).toBeInTheDocument();
    // Stage2 trace N4 答案"命中"
    expect(screen.getByText("命中")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /保存为实验/ })).not.toBeInTheDocument();
    expect(screen.getByText(/硬性否决,无交易指令,不可保存/)).toBeInTheDocument();
  });

  it("next_period 存在时渲染预告块(倾向/观察位/失效条件)", async () => {
    analyzeTradingMock.mockResolvedValue(makeRecord("trade", {
      stage2: {
        diagnosis_or_decision: {
          decision: "trade",
          terminal_outcome: "trade",
          order: { direction: "long", entry: 100, stop_loss: 95, take_profit: 110 },
          trace: [{ node_id: "N5", question: "是否交易?", answer: "trade", bar_range: "K10-1", reason: "趋势回调", branch: "y" }],
          decision_summary: {
            next_period: { direction: "long", watch_levels: [112, 94], invalidate_if: "收盘跌破 94" },
          },
        },
      },
    }));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));

    await screen.findByText(/下周期预告 · NEXT PERIOD/);
    expect(screen.getByText(/看涨/)).toBeInTheDocument(); // NEXT_PERIOD_LABELS long→看涨
    expect(screen.getByText("112 / 94")).toBeInTheDocument();
    expect(screen.getByText(/收盘跌破 94/)).toBeInTheDocument();
  });

  it("node_overrides 存在时渲染改判徽章行;缺省不渲染", async () => {
    analyzeTradingMock.mockResolvedValue(makeRecord("trade", {
      stage1: {
        diagnosis_or_decision: {
          state: "trend_up",
          gate_result: "proceed",
          trace: [
            { node_id: "N1", question: "K线数据完整?", answer: "是", bar_range: "K120-1", reason: "无缺口", branch: "yes" },
          ],
          node_overrides: [
            { node_id: "N1", new_answer: "否", reason: "末三根 bar 实为缺口未回补,人工复核后改判", bar_range: "K3-1" },
          ],
        },
      },
    }));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));

    const badge = await screen.findByText("改判");
    expect(badge).toBeInTheDocument();
    expect(screen.getByText(/末三根 bar 实为缺口未回补/)).toBeInTheDocument();
    expect(screen.getByText("否")).toBeInTheDocument();
  });

  it("无 node_overrides 时不渲染改判区块", async () => {
    await renderWithDecision("trade");
    expect(screen.queryByText("改判")).not.toBeInTheDocument();
  });
});

describe("PaPanel — S8 诚实加载态", () => {
  it("加载中显示静态文案 + elapsed 计时器,无假阶段推进文案", async () => {
    // 挂起 promise:让加载态持续可见;fake timers 驱动 elapsed(全程不用 findByText,避免 waitFor 与 fake timers 死锁)
    let resolveAnalyze!: (r: ReturnType<typeof makeRecord>) => void;
    analyzeTradingMock.mockImplementation(
      () => new Promise((resolve) => { resolveAnalyze = resolve; }),
    );
    vi.useFakeTimers();
    try {
      render(<PaPanel />);
      fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));

      // 静态诚实文案 + 00:00 计时器同步出现(fireEvent 内部已 act 包裹)
      expect(screen.getByText(/两阶段管线运行中\(单次调用,约 1-3 分钟\)/)).toBeInTheDocument();
      expect(screen.getByText(/已运行 00:00/)).toBeInTheDocument();
      // 假阶段推进文案不出现(旧 LOADING_STAGES 全部文案)
      expect(screen.queryByText(/拉取 K 线快照/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Stage 1 结构诊断中/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Stage 2 决策生成中/)).not.toBeInTheDocument();

      // 真实 elapsed:推进 3 秒 → 00:03
      act(() => { vi.advanceTimersByTime(3000); });
      expect(screen.getByText(/已运行 00:03/)).toBeInTheDocument();

      // 完成后加载块与计时器消失
      await act(async () => { resolveAnalyze(makeRecord("trade")); });
      expect(screen.getByText(/做多 · long/)).toBeInTheDocument();
      expect(screen.queryByText(/两阶段管线运行中/)).not.toBeInTheDocument();
      expect(screen.queryByText(/已运行/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("分析失败时同样清除加载态", async () => {
    analyzeTradingMock.mockRejectedValue(new Error("upstream timeout"));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));

    await screen.findByText(/upstream timeout/);
    expect(screen.queryByText(/两阶段管线运行中/)).not.toBeInTheDocument();
  });
});

describe("PaPanel — S3 fail 态重试", () => {
  it("失败后出现重试按钮;点击保留错误文案并重新发起分析", async () => {
    // 第一次调用失败,第二次成功
    analyzeTradingMock.mockRejectedValueOnce(new Error("first attempt failed"));

    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));

    // 失败:错误区 + 重试按钮出现
    await screen.findByText(/first attempt failed/);
    const retryBtn = screen.getByRole("button", { name: /重试/ });
    expect(retryBtn).toBeEnabled();

    // 第二次调用成功
    analyzeTradingMock.mockResolvedValueOnce(makeRecord("trade"));
    fireEvent.click(retryBtn);

    // 重跑成功 → 决策卡出现,错误文案清除
    await screen.findByText(/做多 · long/);
    expect(screen.queryByText(/first attempt failed/)).not.toBeInTheDocument();
    expect(analyzeTradingMock).toHaveBeenCalledTimes(2);
    expect(analyzeTradingMock).toHaveBeenLastCalledWith({
      symbol: "BTC/USDT",
      interval: "1h",
      bars: 120,
      market_type: "futures",
      htf_interval: "4h",
      demo: false,
    }, expect.anything());
  });

  it("成功路径不出现重试按钮", async () => {
    await renderWithDecision("trade");
    expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument();
  });
});

describe("SetupBacktestCard — S5 R:R 分桶样本量", () => {
  const THIN_RESULT: SetupBacktestResult = {
    ...FULL_RESULT,
    total_setups: 3,
    rr_buckets: { "3": { count: 3, wins: 2, losses: 1, win_rate: 0.6667, expectancy: 0.5 } },
  };

  it("每桶显示样本量(取分桶 count);样本 <5 加'样本不足'标注", async () => {
    runSetupBacktestMock.mockResolvedValue(THIN_RESULT);

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /回测该形态/ }));

    await screen.findByText(/R:R 分桶/);
    expect(screen.getByText(/样本 3/)).toBeInTheDocument();
    expect(screen.getByText(/样本不足/)).toBeInTheDocument();
    // 原有渲染保留
    expect(screen.getByText("2 胜 / 1 负")).toBeInTheDocument();
    expect(screen.getByText(/胜率 67%/)).toBeInTheDocument();
  });

  it("样本 >=5 不加'样本不足'标注", async () => {
    runSetupBacktestMock.mockResolvedValue(FULL_RESULT);

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /回测该形态/ }));

    await screen.findByText(/历史回测 · 12 个样本/);
    expect(screen.getByText(/样本 12/)).toBeInTheDocument();
    expect(screen.queryByText(/样本不足/)).not.toBeInTheDocument();
  });

  it("count 缺失时回退 wins+losses", async () => {
    const noCount: SetupBacktestResult = {
      ...FULL_RESULT,
      rr_buckets: { "2": { count: undefined as unknown as number, wins: 4, losses: 3, win_rate: 0.5714, expectancy: 0.3 } },
    };
    runSetupBacktestMock.mockResolvedValue(noCount);

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /回测该形态/ }));

    await screen.findByText(/R:R 分桶/);
    expect(screen.getByText(/样本 7/)).toBeInTheDocument();
  });
});

describe("PaDecisionRecordCard — S9 原始 I/O 查看", () => {
  function withMessages(base: ReturnType<typeof makeRecord>) {
    return {
      ...base,
      stage1: {
        ...base.stage1,
        messages: [
          { role: "system", content: "你是价格行为分析师…" },
          { role: "user", content: "K线快照 BTC/USDT 120 根…" },
        ],
        response: { state: "trend_up" },
      },
      stage2: base.stage2
        ? {
          ...base.stage2,
          messages: [{ role: "user", content: "基于 Stage 1 诊断生成决策…" }],
          response: { decision: "trade" },
        }
        : null,
    };
  }

  it("默认收起;展开后可见 stage1/stage2 messages 完整内容", async () => {
    analyzeTradingMock.mockResolvedValue(withMessages(makeRecord("trade")));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));

    await screen.findByText(/做多 · long/);

    // 默认收起:summary 可见,messages 内容不可见
    const s1 = screen.getByText(/查看原始 prompt\/response · Stage 1/);
    expect(screen.getByText(/查看原始 prompt\/response · Stage 2/)).toBeInTheDocument();
    expect(screen.queryByText(/你是价格行为分析师/)).not.toBeInTheDocument();

    // 展开 Stage 1 → 内容可见
    fireEvent.click(s1);
    await screen.findByText(/你是价格行为分析师/);
    expect(screen.getByText(/K线快照 BTC\/USDT 120 根/)).toBeInTheDocument();
    // JSON 单行紧凑排版:system 消息未被换行拆开
    expect(screen.getByText((_, el) => el?.textContent === JSON.stringify({ role: "system", content: "你是价格行为分析师…" }))).toBeTruthy();

    // Stage 2 仍收起
    expect(screen.queryByText(/基于 Stage 1 诊断生成决策/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/查看原始 prompt\/response · Stage 2/));
    await screen.findByText(/基于 Stage 1 诊断生成决策/);
  });

  it("messages 缺失时不渲染折叠区", async () => {
    await renderWithDecision("trade");
    expect(screen.queryByText(/查看原始 prompt\/response/)).not.toBeInTheDocument();
  });
});

describe("PaDecisionRecordCard — S6 demo 模式标注", () => {
  it("meta.demo=true → 常驻'演示输出 · 不生成交易指令'徽章可见;demo=false 时不可见", async () => {
    // demo record: canned 走查形态(order=None 残卡,outcome=wait 短路)
    analyzeTradingMock.mockResolvedValue(makeRecord("wait", {
      meta: { input_id: "demo-1", timestamp: "2026-09-11T12:00:00Z", session_id: "s-1", demo: true },
    }));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));

    await screen.findByText(/无入场信号 · 不生成交易指令/);
    const badge = screen.getByText("演示输出 · 不生成交易指令");
    expect(badge).toBeInTheDocument();

    // 对照:非 demo record 不渲染徽章
    analyzeTradingMock.mockResolvedValue(makeRecord("wait"));
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));
    await screen.findByText(/无入场信号 · 不生成交易指令/);
    expect(screen.queryByText("演示输出 · 不生成交易指令")).not.toBeInTheDocument();
  });
});

describe("PaPanel — Phase D 演示一轮(真 LLM)", () => {
  it("'演示一轮(真 LLM)'按钮 → analyzeTrading 带 demo=true", async () => {
    analyzeTradingMock.mockResolvedValue(makeRecord("trade", {
      meta: { input_id: "demo-real-1", timestamp: "2026-09-14T12:00:00Z", session_id: "s-1", demo: true },
    }));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /演示一轮/ }));

    await screen.findByText(/做多 · long/);
    expect(analyzeTradingMock).toHaveBeenLastCalledWith({
      symbol: "BTC/USDT",
      interval: "1h",
      bars: 120,
      market_type: "futures",
      htf_interval: "4h",
      demo: true,
    }, expect.anything());
  });

  it("默认'分析当前图表'按钮 → demo=false(不误打演示标)", async () => {
    await renderWithDecision("trade");
    expect(analyzeTradingMock).toHaveBeenLastCalledWith(expect.objectContaining({ demo: false }), expect.anything());
  });

  it("demo 徽章按是否产出订单分支:有单 → '真 LLM 试跑,不入审计表'", async () => {
    analyzeTradingMock.mockResolvedValue(makeRecord("trade", {
      meta: { input_id: "demo-real-2", timestamp: "2026-09-14T12:00:00Z", session_id: "s-1", demo: true },
    }));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /演示一轮/ }));

    await screen.findByText(/做多 · long/);
    expect(screen.getByText(/真 LLM 试跑/)).toBeInTheDocument();
    // 真管线产出了订单 —— 此时再说"不生成交易指令"就是徽章撒谎
    expect(screen.queryByText("演示输出 · 不生成交易指令")).not.toBeInTheDocument();
  });
});

describe("PaDecisionRecordCard — S1f 信心分渲染", () => {
  function withConfidence(confidence: unknown) {
    return makeRecord("trade", {
      stage2: {
        diagnosis_or_decision: {
          decision: "trade",
          terminal_outcome: "trade",
          order: { direction: "long", entry: 100, stop_loss: 95, take_profit: 110 },
          trace: [],
          decision_summary: { confidence },
        },
      },
    });
  }

  it("decision_summary.confidence=0.72 → 渲染'信心 72%'", async () => {
    analyzeTradingMock.mockResolvedValue(withConfidence(0.72));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));

    await screen.findByText(/做多 · long/);
    expect(screen.getByText("信心 72%")).toBeInTheDocument();
  });

  it("confidence 缺失 → 不出现'信心'渲染(不造假占位)", async () => {
    await renderWithDecision("trade");
    expect(screen.queryByText(/信心 \d+%/)).not.toBeInTheDocument();
  });
});

describe("PaDecisionRecordCard — W2 US-1A 结果复盘渲染", () => {
  async function renderWithOutcome(outcome: unknown) {
    analyzeTradingMock.mockResolvedValue(makeRecord("trade", { outcome }));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));
    await screen.findByText(/做多 · long/);
  }

  // 整段限定在 outcome 块内查——卡片别处也有"止损"(OrderRow 的 SL 标签),不限定会撞名
  function auditBlock(): HTMLElement {
    return screen.getByTestId("pa-outcome-audit");
  }

  it("win → 渲染'命中' + '+2.00 R',着色用涨色 utility", async () => {
    await renderWithOutcome({ status: "win", pnl_r: 2.0 });
    const block = within(auditBlock());
    expect(block.getByText(/结果复盘 · OUTCOME/)).toBeInTheDocument();
    expect(block.getByText("命中").className).toContain("text-pos");
    expect(block.getByText("+2.00 R").className).toContain("text-pos");
  });

  it("loss → 渲染'止损' + '-1.00 R',着色用跌色 utility", async () => {
    await renderWithOutcome({ status: "loss", pnl_r: -1.0 });
    const block = within(auditBlock());
    expect(block.getByText("止损").className).toContain("text-neg");
    expect(block.getByText("-1.00 R").className).toContain("text-neg");
  });

  it("expired → 中性(不用涨跌色)+ 说明未开仓,且不渲染 R 值", async () => {
    await renderWithOutcome({ status: "expired", pnl_r: null });
    const block = within(auditBlock());
    const badge = block.getByText("未触发");
    expect(badge.className).not.toContain("text-pos");
    expect(badge.className).not.toContain("text-neg");
    expect(block.getByText(/入场价在观察窗内未被触及/)).toBeInTheDocument();
    expect(block.queryByText(/-?\d+\.\d{2} R/)).not.toBeInTheDocument();
  });

  it("timeout_open → 中性 + 说明结果未定,且不渲染 R 值", async () => {
    await renderWithOutcome({ status: "timeout_open", pnl_r: null });
    const block = within(auditBlock());
    const badge = block.getByText("持有未结");
    expect(badge.className).not.toContain("text-pos");
    expect(badge.className).not.toContain("text-neg");
    expect(block.getByText(/窗口结束时止损\/止盈均未触及/)).toBeInTheDocument();
    expect(block.queryByText(/-?\d+\.\d{2} R/)).not.toBeInTheDocument();
  });

  it("outcome 缺失 → 整段不渲染(不出现空占位)", async () => {
    await renderWithOutcome(undefined);
    expect(screen.queryByTestId("pa-outcome-audit")).not.toBeInTheDocument();
    expect(screen.queryByText(/结果复盘 · OUTCOME/)).not.toBeInTheDocument();
  });
});

describe("PaPanel — S4/Phase D 追问路由", () => {
  function makeSendAgentChatPending() {
    return () => new Promise<{ sessionId?: string }>(() => {});
  }

  // 无 input_id 的记录:锚点不存在,只能退回落通用聊天链路
  const NO_ID_META = { timestamp: "2026-09-10T12:00:00Z", session_id: "s-1" };

  it("记录带 input_id → 走锚定追问端点,不拼 [最近诊断上下文]", async () => {
    sendRecordFollowUpMock.mockResolvedValue({
      record_id: "pa-test-1",
      followups: [{ question: "为什么入场在100?", answer: "因为突破阻力位", at: 1 }],
    });
    await renderWithDecision("trade");

    const inputArea = screen.getByPlaceholderText(/对着这条诊断追问/);
    fireEvent.change(inputArea, { target: { value: "为什么入场在100?" } });
    fireEvent.keyDown(inputArea, { key: "Enter", shiftKey: false });

    await waitFor(() => expect(sendRecordFollowUpMock).toHaveBeenCalledTimes(1));
    expect(sendRecordFollowUpMock.mock.calls[0]).toEqual(["pa-test-1", "为什么入场在100?"]);
    // 锚定后不再走通用链路 —— 上下文由服务端从冻结快照组,前端不拼前缀
    expect(sendAgentChatMock).not.toHaveBeenCalled();

    // 追问记录折叠段渲染的是服务端返回的持久化真值
    await screen.findByTestId("pa-followup-section");
    expect(screen.getByText(/因为突破阻力位/)).toBeInTheDocument();
  });

  it("追问失败 → 错误行可见,且不退回通用链路(不静默假装成功)", async () => {
    sendRecordFollowUpMock.mockRejectedValue(new Error("boom"));
    await renderWithDecision("trade");

    const inputArea = screen.getByPlaceholderText(/对着这条诊断追问/);
    fireEvent.change(inputArea, { target: { value: "会失败吗?" } });
    fireEvent.keyDown(inputArea, { key: "Enter", shiftKey: false });

    await screen.findByTestId("pa-followup-error");
    expect(sendAgentChatMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("pa-followup-section")).not.toBeInTheDocument();
  });

  it("无追问记录 → 折叠段不渲染(单一真值来源为空即整段消失)", async () => {
    await renderWithDecision("trade");
    expect(screen.queryByTestId("pa-followup-section")).not.toBeInTheDocument();
  });

  it("记录无 input_id → 退回落通用链路:sendAgentChat 收到 [最近诊断上下文] 前缀 + 原文", async () => {
    sendAgentChatMock.mockImplementation(makeSendAgentChatPending());
    await renderWithDecision("trade", { meta: NO_ID_META });

    const inputArea = screen.getByPlaceholderText(/追问分析结果/);
    fireEvent.change(inputArea, { target: { value: "为什么入场在100?" } });
    fireEvent.keyDown(inputArea, { key: "Enter", shiftKey: false });

    await waitFor(() => expect(sendAgentChatMock).toHaveBeenCalledTimes(1));
    const sentText = sendAgentChatMock.mock.calls[0][0] as string;
    expect(sentText).toContain("[最近诊断上下文]");
    expect(sentText).toContain("BTC 1h");
    expect(sentText).toContain("方向:long");
    expect(sentText).toContain("entry:100");
    expect(sentText).toContain("SL:95");
    expect(sentText).toContain("TP:110");
    expect(sentText).toContain("现价:99");
    expect(sentText).toContain("[问题] 为什么入场在100?");
    // 第三参 context 恒 undefined(不触后端意图路由)
    expect(sendAgentChatMock.mock.calls[0][2]).toBeUndefined();
    // 聊天面板展示用户原文,无前缀
    expect(screen.getByText("为什么入场在100?")).toBeInTheDocument();
    expect(screen.queryByText(/\[最近诊断上下文\]/)).not.toBeInTheDocument();
    expect(sendRecordFollowUpMock).not.toHaveBeenCalled();
  });

  it("paRecord 为 null → 发送原文无前缀", async () => {
    sendAgentChatMock.mockImplementation(makeSendAgentChatPending());
    render(<PaPanel />);

    const inputArea = screen.getByPlaceholderText(/追问分析结果/);
    fireEvent.change(inputArea, { target: { value: "裸问题" } });
    fireEvent.keyDown(inputArea, { key: "Enter", shiftKey: false });

    await waitFor(() => expect(sendAgentChatMock).toHaveBeenCalledTimes(1));
    expect(sendAgentChatMock.mock.calls[0][0]).toBe("裸问题");
  });

  it("W3-C: used_live=true 的追问条目渲染'含实时读数'审计标签,false 缺省不渲染", async () => {
    sendRecordFollowUpMock.mockResolvedValue({
      record_id: "pa-test-1",
      followups: [
        { question: "现在还能进场吗?", answer: "引用了现价", at: 1, used_live: true },
        { question: "为什么入场在100?", answer: "纯记录锚定", at: 2 },
      ],
    });
    await renderWithDecision("trade");

    const inputArea = screen.getByPlaceholderText(/对着这条诊断追问/);
    fireEvent.change(inputArea, { target: { value: "现在还能进场吗?" } });
    fireEvent.keyDown(inputArea, { key: "Enter", shiftKey: false });

    await screen.findByTestId("pa-followup-section");
    // 条目1:服务端读了新鲜 bar → 审计标签可见
    expect(screen.getAllByTestId("pa-followup-used-live")).toHaveLength(1);
    expect(screen.getByText("含实时读数")).toBeInTheDocument();
    // 条目2:used_live 缺省(纯锚定)→ 不带标签
    expect(screen.getByText(/纯记录锚定/)).toBeInTheDocument();
  });
});

describe("PaPanel — S7f 到价提醒", () => {
  it("trade 态渲染'到价提醒'按钮;点击 POST body 含 symbol/interval/levels.entry;成功后变'已设置'且禁用", async () => {
    let resolveAlert!: (r: { id: string; status: string }) => void;
    createPriceAlertMock.mockImplementation(
      () => new Promise<{ id: string; status: string }>((resolve) => { resolveAlert = resolve; }),
    );

    await renderWithDecision("trade");
    const alertBtn = screen.getByRole("button", { name: /到价提醒/ });
    expect(alertBtn).toBeEnabled();

    fireEvent.click(alertBtn);
    await waitFor(() => expect(createPriceAlertMock).toHaveBeenCalledTimes(1));

    const body = createPriceAlertMock.mock.calls[0][0];
    expect(body.symbol).toBe("BTC/USDT");
    expect(body.interval).toBe("1h");
    expect(body.direction).toBe("long");
    expect(body.input_id).toBe("pa-test-1");
    expect(body.session_id).toBe("s-1");
    expect(body.levels.entry).toBe(100);
    expect(body.levels.stop_loss).toBe(95);
    expect(body.levels.take_profit).toBe(110);

    act(() => { resolveAlert({ id: "alert-1", status: "active" }); });
    await waitFor(() => expect(screen.getByRole("button", { name: /已设置/ })).toBeDisabled());
  });

  it("设置失败 → 错误文案出现,按钮恢复可点", async () => {
    createPriceAlertMock.mockRejectedValue(new Error("alert endpoint 500"));

    await renderWithDecision("trade");
    fireEvent.click(screen.getByRole("button", { name: /到价提醒/ }));

    await screen.findByText(/到价提醒设置失败: alert endpoint 500/);
    expect(screen.getByRole("button", { name: /到价提醒/ })).toBeEnabled();
  });

  it("wait 态(无交易指令)不渲染'到价提醒'按钮", async () => {
    await renderWithDecision("wait");
    expect(screen.queryByRole("button", { name: /到价提醒/ })).not.toBeInTheDocument();
  });
});

describe("PaPanel — T2a 回测挂诊断记录", () => {
  it("attached_to_record=true → 结果卡轻标注『已挂诊断记录』", async () => {
    runSetupBacktestMock.mockResolvedValue({ ...FULL_RESULT, attached_to_record: true });

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /回测该形态/ }));

    await screen.findByText(/历史回测 · 12 个样本/);
    expect(screen.getByText("已挂诊断记录")).toBeInTheDocument();
  });

  it("attached_to_record 缺省/false → 无挂载标注(未挂分支,不渲染标注文本)", async () => {
    runSetupBacktestMock.mockResolvedValue({ ...FULL_RESULT, attached_to_record: false });

    await renderWithDecision();
    fireEvent.click(screen.getByRole("button", { name: /回测该形态/ }));

    await screen.findByText(/历史回测 · 12 个样本/);
    expect(screen.queryByText("已挂诊断记录")).not.toBeInTheDocument();
  });
});

describe("PaPanel — W3 冻结图 + 保鲜差距接线", () => {
  it("分析发起瞬间 KfChart 收到 frozen=true;完成后 false", async () => {
    let resolveAnalyze!: (r: ReturnType<typeof makeRecord>) => void;
    analyzeTradingMock.mockImplementation(
      () => new Promise((resolve) => { resolveAnalyze = resolve; }),
    );
    render(<PaPanel />);

    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));
    // 冻结生效于挂起期间
    await waitFor(() => {
      const last = kfChartPropsMock.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined;
      expect(last?.frozen).toBe(true);
    });

    await act(async () => { resolveAnalyze(makeRecord("trade")); });
    await screen.findByText(/做多 · long/);
    // 解冻:AI 所见已定稿,图恢复跟随现实
    const last = kfChartPropsMock.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined;
    expect(last?.frozen).toBe(false);
  });

  it("记录带窗口末(ISO 字符串)→ staleAsOf 收到 epoch 秒", async () => {
    // fixture: window_end_iso "2026-09-10T12:00" (本地时区解析)
    await renderWithDecision("trade");
    const last = kfChartPropsMock.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined;
    const expected = Math.floor(Date.parse("2026-09-10T12:00") / 1000);
    expect(last?.staleAsOf).toBe(expected);
  });

  it("记录带窗口末(ms 数值)→ staleAsOf 收到 epoch 秒(/1000)", async () => {
    const ms = 1757505600000; // 2026-09-10T12:00:00Z
    await renderWithDecision("trade", {
      input_data: { summary: { symbol: "BTC/USDT", interval: "1h", bars_total: 120, window_end_iso: ms, last_close: 99, atr14: 5 } },
    });
    const last = kfChartPropsMock.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined;
    expect(last?.staleAsOf).toBe(ms / 1000);
  });

  it("无记录 → staleAsOf null(徽章条件不满足)", () => {
    render(<PaPanel />);
    const last = kfChartPropsMock.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined;
    expect(last?.staleAsOf).toBeNull();
  });

  it("window_end_iso 解析失败 → staleAsOf null(不抛、不显示)", async () => {
    await renderWithDecision("trade", {
      input_data: { summary: { symbol: "BTC/USDT", interval: "1h", bars_total: 120, window_end_iso: "not-a-date", last_close: 99, atr14: 5 } },
    });
    const last = kfChartPropsMock.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined;
    expect(last?.staleAsOf).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 收盘后自动增量分析(opt-in checkbox,默认关)
// KfChart 被 mock 掉了,倒计时归零用捕获到的 onCloseElapsed 直接触发 ——
// 本块验的是 pa-panel 的接线与语义,不是倒计时本身(那是 close-countdown 的测试)。
// ---------------------------------------------------------------------------

describe("PaPanel — 收盘后自动增量分析(opt-in)", () => {
  function lastKfProps(): Record<string, unknown> | undefined {
    return kfChartPropsMock.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined;
  }

  it("checkbox 默认未勾选,传给 KfChart 的 autoSubmit 为假", () => {
    render(<PaPanel />);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(lastKfProps()?.autoSubmit).toBeFalsy();
  });

  it("勾选后收盘归零 → 一次 incremental:true 的请求,且 checkbox 自动回未勾选", async () => {
    analyzeTradingMock.mockResolvedValue(makeRecord("trade"));
    render(<PaPanel />);
    const cb = screen.getByRole("checkbox");
    fireEvent.click(cb);
    expect(cb).toBeChecked();
    expect(lastKfProps()?.autoSubmit).toBe(true);

    await act(async () => {
      (lastKfProps()?.onCloseElapsed as () => void)();
    });

    expect(analyzeTradingMock).toHaveBeenCalledTimes(1);
    expect(analyzeTradingMock).toHaveBeenCalledWith(
      expect.objectContaining({ incremental: true, demo: false }),
      expect.anything(),
    );
    // 一次触发即熄火(A3 语义):不排队重试
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    await screen.findByText(/增量续接/);
  });

  it("分析进行中撞收盘 → 零新请求,并追加一条跳过告知", async () => {
    analyzeTradingMock.mockImplementation(() => new Promise(() => {})); // 永不 resolve
    render(<PaPanel />);
    const cb = screen.getByRole("checkbox");
    fireEvent.click(cb); // 先勾选(此时非加载态)
    fireEvent.click(screen.getByRole("button", { name: /分析当前图表/ }));
    await waitFor(() => expect(analyzeTradingMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(cb).toBeDisabled());

    act(() => {
      (lastKfProps()?.onCloseElapsed as () => void)();
    });

    expect(analyzeTradingMock).toHaveBeenCalledTimes(1); // 没有第二次请求
    expect(screen.getByText(/跳过自动增量提交/)).toBeTruthy();
    expect(screen.getByRole("checkbox")).not.toBeChecked(); // 熄火不受影响
  });

  it("无先验(422) → 文案渲染成可读错误,单次请求不刷屏", async () => {
    analyzeTradingMock.mockRejectedValue(new Error("无可用先验记录"));
    render(<PaPanel />);
    fireEvent.click(screen.getByRole("checkbox"));

    await act(async () => {
      (lastKfProps()?.onCloseElapsed as () => void)();
    });

    await screen.findByText(/无可用先验记录/);
    expect(analyzeTradingMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
  });
});

// ---------------------------------------------------------------------------
// B0 确认门 —— pa-panel 与其余 agent 面同源。历史缺陷:本面 onEvent 只认
// thinking/tool_call/tool_result/reflection,收到 tool_approval_request 直接丢
// —— 后端 producer 阻塞等裁决、流不关,人不审批就静默挂到 TTL 超时。
// ---------------------------------------------------------------------------

describe("PaPanel — B0 工具确认门(本面曾完全不处理审批事件)", () => {
  const APPROVAL_HEADING = /Agent 请求执行以下操作，需要确认/;

  /** 走"记录无 input_id → 通用 agent 链路"这条会真的调 sendAgentChat 的路径。 */
  async function driveFollowUp(script: Array<{ type: string; data: unknown }>) {
    sendAgentChatMock.mockReset();
    sendAgentChatMock.mockImplementation(
      async (_msg: string, onEvent: (e: { type: string; data: unknown }) => void) => {
        for (const ev of script) onEvent(ev);
        return { sessionId: "sess-pa" };
      },
    );
    await renderWithDecision("trade", { meta: { timestamp: "2026-09-10T12:00:00Z", session_id: "s-1" } });

    const inputArea = screen.getByPlaceholderText(/追问分析结果/);
    fireEvent.change(inputArea, { target: { value: "帮我下单" } });
    fireEvent.keyDown(inputArea, { key: "Enter", shiftKey: false });
    await waitFor(() => expect(sendAgentChatMock).toHaveBeenCalledTimes(1));
  }

  beforeEach(() => {
    approveToolCallMock.mockReset();
    approveToolCallMock.mockResolvedValue({ ok: true });
  });

  it("收到 tool_approval_request → 渲染确认卡(工具名+参数),同 id 只一张", async () => {
    const ev = {
      type: "tool_approval_request",
      data: { tool_call_id: "tc-pa", tool_name: "paper_place_order", arguments: { symbol: "BTCUSDT" } },
    };
    await driveFollowUp([ev, ev]);

    expect(await screen.findByText(APPROVAL_HEADING)).toBeInTheDocument();
    expect(screen.getByText("paper_place_order")).toBeInTheDocument();
    expect(screen.getByText(/BTCUSDT/)).toBeInTheDocument();
    expect(screen.getAllByText("paper_place_order")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "批准" })).toBeInTheDocument();
  });

  it("点『批准』→ 以 (sessionId, toolCallId, true) 调端点", async () => {
    await driveFollowUp([
      { type: "tool_approval_request", data: { tool_call_id: "tc-pa", tool_name: "paper_place_order", arguments: {} } },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "批准" }));

    await waitFor(() => expect(approveToolCallMock).toHaveBeenCalledTimes(1));
    expect(approveToolCallMock.mock.calls[0]).toEqual(["sess-pa", "tc-pa", true]);
  });

  it("点『拒绝』→ 以 approve=false 调端点", async () => {
    await driveFollowUp([
      { type: "tool_approval_request", data: { tool_call_id: "tc-pa", tool_name: "paper_place_order", arguments: {} } },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "拒绝" }));

    await waitFor(() => expect(approveToolCallMock).toHaveBeenCalledTimes(1));
    expect(approveToolCallMock.mock.calls[0]).toEqual(["sess-pa", "tc-pa", false]);
  });

  it("对照:无审批事件 → 不渲染确认卡", async () => {
    await driveFollowUp([{ type: "text_delta", data: { text: "好的" } }]);
    expect(screen.queryByText(APPROVAL_HEADING)).toBeNull();
  });
});
