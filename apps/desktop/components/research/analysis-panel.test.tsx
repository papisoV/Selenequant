import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ApiError } from "@/lib/api-client";
import type { AnalyzeRequest, AnalysisRecord, MarketAnalysisEvent } from "@/lib/api/market-analysis";

// 保留真实模块的其它导出（chatMessage 等），只替换 streamAnalyzeMarket 的行为，
// 让 AnalysisPanel 里对 onEvent 的类型约束在编译期仍然生效。
// vi.mock 工厂会被提升到文件顶部，跨工厂共享的 mock 必须用 vi.hoisted 初始化。
const { streamMock } = vi.hoisted(() => ({ streamMock: vi.fn() }));
vi.mock("@/lib/api/market-analysis", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/market-analysis")>(
    "@/lib/api/market-analysis",
  );
  return { ...actual, streamAnalyzeMarket: streamMock };
});
vi.mock("@/lib/auth", () => ({
  getAccessToken: () => "test-token",
  clearAccessToken: () => {},
  refreshToken: () => Promise.resolve(false),
}));

import { AnalysisPanel } from "@/components/research/analysis-panel";

const streamRecord = (): AnalysisRecord => ({
  meta: { record_id: "ar-comp" },
  input_data: { exchange: "binance", market_type: "futures", symbol: "BTC/USDT", interval: "1h", klines: [] },
  stage1: {
    market_regime: "range",
    entry_gate: "wait",
    signal_patterns: [],
    trend_context: {},
    bar_features: {},
    evidence: [],
    diagnosis_summary: {},
  },
  stage2: null,
  dispatch_playbooks: [],
  usage_total: {},
});

beforeEach(() => {
  streamMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("US-003 流式渲染替换骨架屏", () => {
  it("分析中显示阶段状态 + LLM 文本增量，流结束后渲染结果卡片", async () => {
    let onEventCb: ((ev: MarketAnalysisEvent) => void) | undefined;
    let resolveStream!: (r: AnalysisRecord) => void;
    streamMock.mockImplementation((_req: AnalyzeRequest, onEvent: (ev: MarketAnalysisEvent) => void) =>
      new Promise<AnalysisRecord>((resolve) => {
        onEventCb = onEvent;
        resolveStream = resolve;
      }),
    );

    render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));

    // 流式阶段卡（替代旧骨架屏）
    expect(screen.getByText(/Stage 1 诊断中/)).toBeInTheDocument();
    expect(screen.getByText(/等待 LLM 输出/)).toBeInTheDocument();

    // LLM 文本增量滚动
    act(() => {
      onEventCb?.({ type: "stage1_text", delta: "市场趋势向上" });
    });
    expect(screen.getByText(/市场趋势向上/)).toBeInTheDocument();

    // 阶段切换 Stage 1 → Stage 2
    act(() => {
      onEventCb?.({ type: "stage2_start", attempt: 1 });
    });
    expect(screen.getByText(/Stage 2 决策中/)).toBeInTheDocument();

    // 流结束 → 完整结果卡片
    act(() => {
      resolveStream(streamRecord());
    });
    await waitFor(() => expect(screen.getByText(/Stage 1 诊断/)).toBeInTheDocument());
  });

  it("流式错误显示错误态且保留已流式文本", async () => {
    let onEventCb: ((ev: MarketAnalysisEvent) => void) | undefined;
    let rejectStream!: (e: unknown) => void;
    streamMock.mockImplementation((_req: AnalyzeRequest, onEvent: (ev: MarketAnalysisEvent) => void) =>
      new Promise<AnalysisRecord>((_resolve, reject) => {
        onEventCb = onEvent;
        rejectStream = reject;
      }),
    );

    render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));

    act(() => {
      onEventCb?.({ type: "stage1_text", delta: "部分内容" });
      onEventCb?.({ type: "analyze_fail", error: { error: "provider_unavailable", message: "provider down" } });
      rejectStream(new ApiError("provider down", 502, {
        code: "provider_unavailable",
        details: { error: "provider_unavailable", message: "provider down" },
      }));
    });

    await waitFor(() => expect(screen.getByText(/provider down/)).toBeInTheDocument());
    // 不清空已流式文本
    expect(screen.getByText(/部分内容/)).toBeInTheDocument();
    expect(screen.getByText(/已流式输出/)).toBeInTheDocument();
  });
});

describe("US-004 交易倾向输入参数", () => {
  it("提交区有交易倾向 Select（默认均衡），请求透传 trade_temperament", async () => {
    let lastReq: AnalyzeRequest | undefined;
    streamMock.mockImplementation((req: AnalyzeRequest) => {
      lastReq = req;
      return Promise.resolve(streamRecord());
    });

    render(<AnalysisPanel />);
    expect(screen.getByText("交易倾向")).toBeInTheDocument();
    // 默认均衡（Select 触发按钮显示当前值）
    expect(screen.getAllByText("均衡").length).toBeGreaterThanOrEqual(1);

    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));
    await waitFor(() => expect(streamMock).toHaveBeenCalled());
    expect(lastReq?.trade_temperament).toBe("balanced");
    // 结果卡展示所选交易倾向（默认均衡）
    await waitFor(() => expect(screen.getByText(/entry_gate=wait/)).toBeInTheDocument());
  });
});

describe("US-005 增量分析按钮", () => {
  it("点击增量分析 → 请求带 incremental: true", async () => {
    let lastReq: AnalyzeRequest | undefined;
    streamMock.mockImplementation((req: AnalyzeRequest) => {
      lastReq = req;
      return Promise.resolve(streamRecord());
    });

    render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /增量分析/ }));
    await waitFor(() => expect(streamMock).toHaveBeenCalled());
    expect(lastReq?.incremental).toBe(true);
  });

  it("增量结果含 prev_record_id 时展示 prev 关联", async () => {
    const rec = streamRecord();
    rec.meta.prev_record_id = "ar-prev-123456789012";
    streamMock.mockResolvedValue(rec);

    render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));
    await waitFor(() => expect(screen.getByText(/prev:/)).toBeInTheDocument());
  });
});

describe("US-006 等待收盘倒计时", () => {
  it("结果含未来收盘时刻时显示 mm:ss 实时倒计时", async () => {
    const rec = streamRecord();
    rec.input_data = {
      exchange: "binance",
      market_type: "futures",
      symbol: "BTC/USDT",
      interval: "1h",
      klines: [{ timestamp: Date.now() + 1_000_000_000, open: 1, high: 1, low: 1, close: 1, volume: 1 }],
    };
    streamMock.mockResolvedValue(rec);

    render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));
    await waitFor(() => expect(screen.getByText(/后收盘/)).toBeInTheDocument());
    // mm:ss 格式（替代一次性分钟提示）
    expect(screen.getByText(/后收盘/).textContent).toMatch(/\d{2}:\d{2} 后收盘/);
  });
});

describe("US-006 调试 / 原始视图", () => {
  it("折叠面板展示 prompt / raw / json_repaired 标记", async () => {
    const rec = streamRecord();
    (rec as unknown as { prompt: unknown }).prompt = {
      stage1: [{ role: "user", content: "S1" }],
      stage2: [{ role: "user", content: "S2" }],
    };
    (rec as unknown as { raw: unknown }).raw = { stage1: "raw-1" };
    (rec.meta as Record<string, unknown>).json_repaired = true;
    streamMock.mockResolvedValue(rec);

    render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));
    await waitFor(() => expect(screen.getByText(/调试 \/ 原始视图/)).toBeInTheDocument());
    expect(screen.getByText("Stage 1 prompt")).toBeInTheDocument();
    expect(screen.getByText("Stage 2 prompt")).toBeInTheDocument();
    expect(screen.getByText(/raw-1/)).toBeInTheDocument();
    expect(screen.getByText("JSON 修复")).toBeInTheDocument();
  });

  it("无 prompt/raw/exception 时显示占位", async () => {
    streamMock.mockResolvedValue(streamRecord()); // streamRecord 无 prompt/raw/exception

    render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));
    await waitFor(() => expect(screen.getByText(/调试 \/ 原始视图/)).toBeInTheDocument());
    expect(screen.getByText(/无调试数据/)).toBeInTheDocument();
  });
});

describe("US-007 K线图增强：EMA20 + 交易决策线", () => {
  it("≥20 根 K 线时画 EMA20 折线与决策线，SVG 保持 id=ma-kline-chart", async () => {
    const rec = streamRecord();
    const base = 50_000;
    rec.input_data = {
      exchange: "binance",
      market_type: "futures",
      symbol: "BTC/USDT",
      interval: "1h",
      klines: Array.from({ length: 24 }, (_, i) => ({
        timestamp: 1_000_000_000 + i * 3_600_000,
        open: base + i * 100,
        high: base + i * 100 + 300,
        low: base + i * 100 - 200,
        close: base + i * 100 + 50,
        volume: 10,
      })),
    };
    rec.stage2 = {
      decision: "limit",
      dispatch_playbooks: [],
      decision_summary: {
        entry_price: base + 1200,
        stop_loss: base + 800,
        take_profit: base + 2000,
      },
      evidence: [],
    };
    streamMock.mockResolvedValue(rec);

    const { container } = render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));
    await waitFor(() => expect(screen.getByRole("img", { name: "K线图" })).toBeInTheDocument());
    const svg = container.querySelector("#ma-kline-chart");
    expect(svg).not.toBeNull();
    // EMA20 折线
    expect(svg?.querySelectorAll("polyline").length).toBe(1);
    // 决策水平虚线 + 标签
    expect(screen.getByText(/入场/)).toBeInTheDocument();
    expect(screen.getByText(/止损/)).toBeInTheDocument();
    expect(screen.getByText(/止盈/)).toBeInTheDocument();
    // A2: 无 trade_setup 时不渲染 ▲▼ 方向标记（方向来自 validator 强制字段，不再从 decision 推断）
    expect(screen.queryByText(/看多|看空/)).not.toBeInTheDocument();
  });

  it("< 20 根 K 线时 EMA20 与决策线均不画", async () => {
    const rec = streamRecord();
    rec.input_data = {
      ...rec.input_data,
      klines: Array.from({ length: 10 }, (_, i) => ({
        timestamp: 1_000_000_000 + i,
        open: 1,
        high: 2,
        low: 0.5,
        close: 1.5,
        volume: 1,
      })),
    };
    rec.stage2 = { decision: "no_trade", dispatch_playbooks: [], decision_summary: {}, evidence: [] };
    streamMock.mockResolvedValue(rec);

    const { container } = render(<AnalysisPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开始分析/ }));
    await waitFor(() => expect(screen.getByRole("img", { name: "K线图" })).toBeInTheDocument());
    const svg = container.querySelector("#ma-kline-chart");
    expect(svg?.querySelectorAll("polyline").length).toBe(0);
    expect(screen.queryByText(/入场/)).not.toBeInTheDocument();
  });
});
