import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useRiskAwareSubmit } from "./use-risk-aware-submit";
import * as apiClientModule from "@/lib/api-client";
import * as toastModule from "@/lib/toast";

type TestParams = {
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  confirm_risk?: boolean;
};

interface TestResult {
  has_risk_alerts?: boolean;
  risk_alerts?: { level: string; message: string }[];
  order?: { id: string };
}

function mockApiPostOnce(value: TestResult | Error) {
  if (value instanceof Error) {
    return vi
      .spyOn(apiClientModule, "apiPost")
      .mockRejectedValueOnce(value);
  }
  return vi
    .spyOn(apiClientModule, "apiPost")
    .mockResolvedValueOnce(value);
}

describe("useRiskAwareSubmit", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens risk dialog when response has risk alerts", async () => {
    const spy = mockApiPostOnce({
      has_risk_alerts: true,
      risk_alerts: [{ level: "warning", message: "高杠杆" }],
    });
    const onSuccess = vi.fn();

    const { result } = renderHook(() =>
      useRiskAwareSubmit<TestParams, TestResult>({
        url: "/api/v1/trading/orders",
        onSuccess,
      })
    );

    await act(async () => {
      await result.current.submit({ symbol: "BTC", side: "buy", quantity: 1 });
    });

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith("/api/v1/trading/orders", {
      symbol: "BTC",
      side: "buy",
      quantity: 1,
    });
    expect(result.current.showRiskDialog).toBe(true);
    expect(result.current.riskAlerts).toEqual([
      { level: "warning", message: "高杠杆" },
    ]);
    expect(result.current.submitting).toBe(false);
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("confirm() retries with confirm_risk:true forced", async () => {
    const spy = vi
      .spyOn(apiClientModule, "apiPost")
      .mockResolvedValueOnce({
        has_risk_alerts: true,
        risk_alerts: [{ level: "warning", message: "高杠杆" }],
      })
      .mockResolvedValueOnce({
        has_risk_alerts: false,
        order: { id: "ord-1" },
      });
    const onSuccess = vi.fn();

    const { result } = renderHook(() =>
      useRiskAwareSubmit<TestParams, TestResult>({
        url: "/api/v1/trading/orders",
        onSuccess,
      })
    );

    await act(async () => {
      await result.current.submit({ symbol: "BTC", side: "buy", quantity: 1 });
    });

    await act(async () => {
      result.current.confirm();
    });

    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(2);
    });

    // 第二次调用必须强制 confirm_risk:true，调用方无法干预
    const secondCall = spy.mock.calls[1];
    expect(secondCall[0]).toBe("/api/v1/trading/orders");
    expect(secondCall[1]).toMatchObject({
      symbol: "BTC",
      side: "buy",
      quantity: 1,
      confirm_risk: true,
    });
    expect(result.current.showRiskDialog).toBe(false);
    expect(onSuccess).toHaveBeenCalledWith({ has_risk_alerts: false, order: { id: "ord-1" } });
  });

  it("cancel() clears pending state and does not call apiPost again", async () => {
    const spy = mockApiPostOnce({
      has_risk_alerts: true,
      risk_alerts: [{ level: "warning", message: "高杠杆" }],
    });
    const onSuccess = vi.fn();

    const { result } = renderHook(() =>
      useRiskAwareSubmit<TestParams, TestResult>({
        url: "/api/v1/trading/orders",
        onSuccess,
      })
    );

    await act(async () => {
      await result.current.submit({ symbol: "BTC", side: "buy", quantity: 1 });
    });

    act(() => {
      result.current.cancel();
    });

    expect(result.current.showRiskDialog).toBe(false);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("rejects confirm() when pending params expired (>30s) and shows toast", async () => {
    vi.useFakeTimers();
    const now = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(now);

    const spy = vi
      .spyOn(apiClientModule, "apiPost")
      .mockResolvedValueOnce({
        has_risk_alerts: true,
        risk_alerts: [{ level: "warning", message: "高杠杆" }],
      });
    // 第二次 apiPost 不应被调用
    const toastSpy = vi.spyOn(toastModule, "toastError");

    const { result } = renderHook(() =>
      useRiskAwareSubmit<TestParams, TestResult>({
        url: "/api/v1/trading/orders",
      })
    );

    await act(async () => {
      await result.current.submit({ symbol: "BTC", side: "buy", quantity: 1 });
    });

    // 推进 31s
    vi.spyOn(Date, "now").mockReturnValue(now + 31_000);

    await act(async () => {
      result.current.confirm();
    });

    expect(spy).toHaveBeenCalledTimes(1); // 没有第二次调用
    expect(toastSpy).toHaveBeenCalledWith("参数已过期请重新下单");
    expect(result.current.showRiskDialog).toBe(false);
    vi.useRealTimers();
  });

  it("calls onError when apiPost rejects", async () => {
    const err = new Error("network down");
    mockApiPostOnce(err);
    const onError = vi.fn();

    const { result } = renderHook(() =>
      useRiskAwareSubmit<TestParams, TestResult>({
        url: "/api/v1/trading/orders",
        onError,
      })
    );

    await act(async () => {
      await result.current.submit({ symbol: "BTC", side: "buy", quantity: 1 });
    });

    expect(onError).toHaveBeenCalledWith(err);
    expect(result.current.submitting).toBe(false);
    expect(result.current.showRiskDialog).toBe(false);
  });

  it("submit() resets prior risk state", async () => {
    const spy = vi
      .spyOn(apiClientModule, "apiPost")
      .mockResolvedValueOnce({
        has_risk_alerts: true,
        risk_alerts: [{ level: "warning", message: "x" }],
      })
      .mockResolvedValueOnce({
        has_risk_alerts: false,
        order: { id: "ord-2" },
      });

    const { result } = renderHook(() =>
      useRiskAwareSubmit<TestParams, TestResult>({
        url: "/api/v1/trading/orders",
      })
    );

    await act(async () => {
      await result.current.submit({ symbol: "BTC", side: "buy", quantity: 1 });
    });
    expect(result.current.showRiskDialog).toBe(true);

    // 第二次 submit 应清掉旧 riskAlerts / dialog
    await act(async () => {
      await result.current.submit({ symbol: "ETH", side: "sell", quantity: 2 });
    });

    expect(spy).toHaveBeenCalledTimes(2);
    expect(result.current.showRiskDialog).toBe(false);
    expect(result.current.riskAlerts).toEqual([]);
  });
});
