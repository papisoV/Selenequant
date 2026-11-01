import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
// vi.mock 工厂会被提升到文件顶部,跨工厂共享的 store 必须用 vi.hoisted 初始化
// (analysis-panel.test.tsx 先例)。
const { useFakeWorkflowStore } = vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { create } = require("zustand") as typeof import("zustand");
  interface FakeWorkflowState {
    tuning: Record<string, unknown> | null;
    setTuning: (patch: Record<string, unknown>) => void;
  }
  return {
    useFakeWorkflowStore: create<FakeWorkflowState>((set) => ({
      tuning: null,
      setTuning: (patch) =>
        set((s) => ({
          tuning: { ...(s.tuning ?? {}), ...patch },
        })),
    })),
  };
});

vi.mock("@/lib/store/workflow", () => ({
  useWorkflowStore: useFakeWorkflowStore,
}));

// === mock api-client =================================
const apiPostMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiPost: (url: string, body?: Record<string, unknown>) => apiPostMock(url, body),
}));

// === mock ws hook =================================
vi.mock("@/lib/ws/use-tuning-ws", () => ({
  useTuningWs: () => ({ taskState: null, streamState: null }),
}));

import { TuningControlPanel } from "@/components/research/tuning-control-panel";

beforeEach(() => {
  useFakeWorkflowStore.setState({ tuning: null });
  apiPostMock.mockReset();
  apiPostMock.mockResolvedValue({ task_id: "t-test" });
});

function openConfigForm() {
  // 组件默认 idle 且无 progress → 配置表单直接渲染
  render(<TuningControlPanel />);
}

async function clickStart() {
  fireEvent.click(screen.getByRole("button", { name: /开始优化/ }));
}

const exchangeSelect = () => screen.getAllByDisplayValue(/^(binance|okx|yahoo)$/)[0] as HTMLSelectElement;
const marketSelect = () => screen.getAllByDisplayValue(/^(futures|spot|us_stock)$/)[0] as HTMLSelectElement;

describe("TuningControlPanel 美股化 (US-002)", () => {
  it("AC2-1: 交易所 select 含 yahoo option,市场 select 含 us_stock option", () => {
    openConfigForm();
    const exOptions = Array.from(exchangeSelect().options).map(o => o.value);
    const mtOptions = Array.from(marketSelect().options).map(o => o.value);
    expect(exOptions).toContain("yahoo");
    expect(mtOptions).toContain("us_stock");
  });

  it("AC2-2: 选 yahoo → 市场自动 us_stock;选 us_stock → 交易所自动 yahoo", () => {
    openConfigForm();
    fireEvent.change(exchangeSelect(), { target: { value: "yahoo" } });
    expect((marketSelect() as HTMLSelectElement).value).toBe("us_stock");

    // 回到 binance 再从市场侧进 us_stock
    fireEvent.change(exchangeSelect(), { target: { value: "binance" } });
    expect((marketSelect() as HTMLSelectElement).value).toBe("futures"); // 联动回 futures
    fireEvent.change(marketSelect(), { target: { value: "us_stock" } });
    expect((exchangeSelect() as HTMLSelectElement).value).toBe("yahoo");
  });

  it("AC2-3: yahoo 模式出现 symbol input 且初值 AAPL;binance 模式是静态文本 BTC/USDT", () => {
    openConfigForm();
    expect(screen.getByText("BTC/USDT")).toBeTruthy(); // 静态展示
    expect(screen.queryByLabelText("美股代码")).toBeNull();

    fireEvent.change(exchangeSelect(), { target: { value: "yahoo" } });
    const input = screen.getByLabelText("美股代码") as HTMLInputElement;
    expect(input.value).toBe("AAPL");
    expect(screen.queryByText("BTC/USDT")).toBeNull();
  });

  it("AC2-4: 小写 aapl → 按钮禁用 + 红字;AAPL → 可点,body 为 yahoo/us_stock/AAPL", async () => {
    openConfigForm();
    fireEvent.change(exchangeSelect(), { target: { value: "yahoo" } });
    const input = screen.getByLabelText("美股代码") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "aapl" } });
    expect(screen.getByRole("button", { name: /开始优化/ })).toHaveProperty("disabled", true);
    expect(screen.getByText(/仅支持大写字母/)).toBeTruthy();

    fireEvent.change(input, { target: { value: "AAPL" } });
    const btn = screen.getByRole("button", { name: /开始优化/ });
    expect(btn).toHaveProperty("disabled", false);
    await clickStart();
    expect(apiPostMock).toHaveBeenCalledTimes(1);
    const [url, body] = apiPostMock.mock.calls[0];
    expect(url).toBe("/api/v1/research/tuning/start");
    expect(body).toMatchObject({ exchange: ["yahoo"], market_type: ["us_stock"], symbol: "AAPL" });
  });

  it("AC2-5: 默认 binance/futures 路径 body 与改造前一致", async () => {
    openConfigForm();
    await clickStart();
    const [, body] = apiPostMock.mock.calls[0];
    expect(body).toMatchObject({
      exchange: ["binance"],
      market_type: ["futures"],
      symbol: "BTC/USDT",
    });
  });

  it("AC2-6: yahoo→binance 切换后 symbol 还原 BTC/USDT(静态文本 + body)", async () => {
    openConfigForm();
    fireEvent.change(exchangeSelect(), { target: { value: "yahoo" } });
    expect((screen.getByLabelText("美股代码") as HTMLInputElement).value).toBe("AAPL");

    fireEvent.change(exchangeSelect(), { target: { value: "binance" } });
    expect(screen.getByText("BTC/USDT")).toBeTruthy(); // 还原
    expect(screen.queryByLabelText("美股代码")).toBeNull();
    await clickStart();
    const [, body] = apiPostMock.mock.calls[0];
    expect(body).toMatchObject({ exchange: ["binance"], market_type: ["futures"], symbol: "BTC/USDT" });
  });

  it("AC2-7: okx+us_stock → 自动回 futures;yahoo+futures → 自动回 binance;垃圾组合不出现", async () => {
    openConfigForm();
    // yahoo + us_stock 起步
    fireEvent.change(exchangeSelect(), { target: { value: "yahoo" } });
    // 切 okx(带 us_stock)→ marketType 自动回 futures,symbol 还原
    fireEvent.change(exchangeSelect(), { target: { value: "okx" } });
    expect((marketSelect() as HTMLSelectElement).value).toBe("futures");

    // 从 okx/futures 直接把市场改成 us_stock → exchange 自动回 yahoo
    fireEvent.change(marketSelect(), { target: { value: "us_stock" } });
    expect((exchangeSelect() as HTMLSelectElement).value).toBe("yahoo");

    // yahoo 状态下把市场改回 futures → exchange 自动回 binance
    fireEvent.change(marketSelect(), { target: { value: "futures" } });
    expect((exchangeSelect() as HTMLSelectElement).value).toBe("binance");

    // 无垃圾组合落到 body
    await clickStart();
    const [, body] = apiPostMock.mock.calls[0];
    expect(body).toMatchObject({ exchange: ["binance"], market_type: ["futures"] });
    expect(JSON.stringify(body)).not.toContain("yahoo");
  });
});
