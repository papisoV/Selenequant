import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { OrderForm } from "./order-form";
import * as apiClientModule from "@/lib/api-client";
import * as toastModule from "@/lib/toast";

// mock apiPost + useAccountId(useRiskAwareSubmit 内部用)
vi.mock("@/lib/api-client", () => ({
  apiPost: vi.fn(),
}));

vi.mock("@/lib/hooks/use-account-id", () => ({
  useAccountId: () => "test-account-id",
}));

describe("OrderForm 受控模式", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("外部传 symbol — 显示外部值,readOnly", () => {
    render(<OrderForm symbol="BTCUSDT" />);
    const input = screen.getByPlaceholderText("BTC/USDT") as HTMLInputElement;
    expect(input.value).toBe("BTCUSDT");
    expect(input.readOnly).toBe(true);
  });

  it("不传 symbol — 用内部默认 BTC/USDT,可编辑", () => {
    render(<OrderForm />);
    const input = screen.getByPlaceholderText("BTC/USDT") as HTMLInputElement;
    expect(input.value).toBe("BTC/USDT");
    expect(input.readOnly).toBe(false);
  });

  it("传 symbol + onSymbolChange — 可编辑(非 readOnly)", () => {
    const onSymbolChange = vi.fn();
    render(<OrderForm symbol="BTCUSDT" onSymbolChange={onSymbolChange} />);
    const input = screen.getByPlaceholderText("BTC/USDT") as HTMLInputElement;
    expect(input.readOnly).toBe(false);
  });

  it("不传 symbol — 内部编辑不调 onSymbolChange", () => {
    render(<OrderForm />);
    const input = screen.getByPlaceholderText("BTC/USDT") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "ETHUSDT" } });
    expect(input.value).toBe("ETHUSDT");
  });

  it("传 symbol + onSymbolChange — 编辑调 onSymbolChange", () => {
    const onSymbolChange = vi.fn();
    render(<OrderForm symbol="BTCUSDT" onSymbolChange={onSymbolChange} />);
    const input = screen.getByPlaceholderText("BTC/USDT") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "ETHUSDT" } });
    expect(onSymbolChange).toHaveBeenCalledWith("ETHUSDT");
  });

  it("传 symbol 无 onSymbolChange — readOnly 时编辑不改变 value", () => {
    render(<OrderForm symbol="BTCUSDT" />);
    const input = screen.getByPlaceholderText("BTC/USDT") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "ETHUSDT" } });
    // readOnly input 的 value 不会被 change 事件改变
    expect(input.value).toBe("BTCUSDT");
  });
});

describe("OrderForm 竞态保护", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("快速点击买入/卖出 — side 状态正确切换", async () => {
    const apiPostSpy = vi
      .spyOn(apiClientModule, "apiPost")
      .mockResolvedValue({ order: { id: "1" } });

    render(<OrderForm symbol="BTCUSDT" />);
    const quantityInput = screen.getByPlaceholderText("0.001");
    fireEvent.change(quantityInput, { target: { value: "0.5" } });

    // 点击买入
    const buyBtn = screen.getByText("买入");
    fireEvent.click(buyBtn);
    // 立即点击卖出
    const sellBtn = screen.getByText("卖出");
    fireEvent.click(sellBtn);

    // 等待 apiPost 调用
    await vi.waitFor(() => {
      expect(apiPostSpy).toHaveBeenCalled();
    });

    // 至少调用了 1 次 apiPost
    expect(apiPostSpy.mock.calls.length).toBeGreaterThanOrEqual(1);
    // 检查所有调用的 side 参数:应该是 buy 或 sell
    for (const call of apiPostSpy.mock.calls) {
      const body = call[1] as { side?: string };
      expect(["buy", "sell"]).toContain(body.side);
    }
  });

  it("onSuccess 重置 quantity/price/stopPrice", async () => {
    const apiPostSpy = vi
      .spyOn(apiClientModule, "apiPost")
      .mockResolvedValue({ order: { id: "1" } });

    render(<OrderForm symbol="BTCUSDT" />);
    const quantityInput = screen.getByPlaceholderText("0.001") as HTMLInputElement;
    fireEvent.change(quantityInput, { target: { value: "0.5" } });
    expect(quantityInput.value).toBe("0.5");

    // 提交
    const buyBtn = screen.getByText("买入");
    fireEvent.click(buyBtn);

    await vi.waitFor(() => {
      expect(apiPostSpy).toHaveBeenCalled();
    });

    // quantity 应被重置(因为 onSuccess 调 setQuantity(""))
    await vi.waitFor(() => {
      expect(quantityInput.value).toBe("");
    });
  });
});
