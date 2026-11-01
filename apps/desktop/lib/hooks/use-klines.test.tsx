import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useKlines } from "./use-klines";
import { getKlineWithCache } from "@/lib/api/markets";

vi.mock("@/lib/api/markets", () => ({
  getKlineWithCache: vi.fn(),
}));

vi.mock("@/lib/kline-cache", () => ({
  clearCachedKlines: vi.fn(),
}));

vi.mock("@/lib/store/market", () => ({
  useMarketStore: (selector: (s: { marketsStreamState: string; lastWsKline: unknown }) => unknown) =>
    selector({ marketsStreamState: "live", lastWsKline: null }),
}));

function makeKline(ts: number, close = 100) {
  return {
    timestamp: ts,
    open_time: ts,
    open: close,
    high: close,
    low: close,
    close,
    volume: 1,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useKlines loadMore", () => {
  it("end_time 翻页:只合并更早数据,去重,保持时间升序", async () => {
    // 初始窗口:最近 3 根(1000, 2000, 3000)
    const initial = [makeKline(1000, 90), makeKline(2000, 95), makeKline(3000, 100)];
    vi.mocked(getKlineWithCache).mockResolvedValueOnce({
      klines: initial,
      fromCache: false,
      source: "local",
    });

    const { result } = renderHook(() => useKlines("BTCUSDT", "1h"));
    // 等待初始数据就绪(SWR fetcher resolve → setState)
    await act(async () => {
      await vi.waitFor(() => {
        expect(result.current.klines.length).toBe(3);
      });
    });

    // 翻页:end_time=1000 返回更早的 [100, 500, 1000] (1000 已存在,应去重)
    vi.mocked(getKlineWithCache).mockResolvedValueOnce({
      klines: [makeKline(100, 80), makeKline(500, 85), makeKline(1000, 90)],
      fromCache: false,
      source: "local",
    });

    let added = 0;
    await act(async () => {
      added = await result.current.loadMore();
    });

    expect(added).toBe(2); // 100, 500 新增;1000 已存在
    expect(getKlineWithCache).toHaveBeenLastCalledWith(
      "BTCUSDT",
      "1h",
      "binance",
      "futures",
      { endTime: 1000 },
    );
    // 合并后按时间升序,5 根
    await act(async () => {
      await vi.waitFor(() => {
        expect(result.current.klines.map((k) => k.timestamp ?? k.open_time)).toEqual([
          100, 500, 1000, 2000, 3000,
        ]);
      });
    });
  });

  it("没有更早数据时返回 0", async () => {
    const initial = [makeKline(1000, 90)];
    vi.mocked(getKlineWithCache).mockResolvedValueOnce({
      klines: initial,
      fromCache: false,
      source: "local",
    });
    const { result } = renderHook(() => useKlines("ETHUSDT", "1h"));
    await act(async () => {
      await vi.waitFor(() => expect(result.current.klines.length).toBe(1));
    });

    vi.mocked(getKlineWithCache).mockResolvedValueOnce({
      klines: [],
      fromCache: false,
      source: "local",
    });
    let added: number | null = null;
    await act(async () => {
      added = await result.current.loadMore();
    });
    expect(added).toBe(0);
  });

  it("翻页数据部分已存在时只返回真正新增数", async () => {
    const initial = [makeKline(1000, 90), makeKline(2000, 95)];
    vi.mocked(getKlineWithCache).mockResolvedValueOnce({
      klines: initial,
      fromCache: false,
      source: "local",
    });
    const { result } = renderHook(() => useKlines("SOLUSDT", "1h"));
    await act(async () => {
      await vi.waitFor(() => expect(result.current.klines.length).toBe(2));
    });

    vi.mocked(getKlineWithCache).mockResolvedValueOnce({
      klines: [makeKline(1000, 90), makeKline(500, 80)],
      fromCache: false,
      source: "local",
    });
    // 500 是新的,应 +1
    let added: number | null = null;
    await act(async () => {
      added = await result.current.loadMore();
    });
    expect(added).toBe(1);
  });
});
