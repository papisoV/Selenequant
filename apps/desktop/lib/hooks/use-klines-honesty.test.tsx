import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useKlines } from "./use-klines";
import { getKlineWithCache } from "@/lib/api/markets";

// Task A 核心契约:**前端不再造假 bar**。
// 历史病根:use-klines 曾用上一根收盘价复制最多 100 根假 bar 填平缺口,
// 让 9.4 天真洞在图上"看着连着"。这些测试钉死"诚实稀疏"。

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
  return { timestamp: ts, open_time: ts, open: close, high: close, low: close, close, volume: 1 };
}

beforeEach(() => {
  vi.clearAllMocks();
});

const H = 3_600_000; // ms(klines 的 ts 是毫秒)

describe("useKlines — 不造假 bar(诚实稀疏)", () => {
  it("★带洞数据原样返回:不插入任何占位 bar", async () => {
    // 3 根真实 bar,中间跳了 5 根 1h
    const holed = [makeKline(1000, 90), makeKline(1000 + H, 95), makeKline(1000 + 7 * H, 100)];
    vi.mocked(getKlineWithCache).mockResolvedValueOnce({
      klines: holed,
      fromCache: false,
      source: "local",
    });

    const { result } = renderHook(() => useKlines("BTCUSDT", "1h"));
    await act(async () => {
      await vi.waitFor(() => expect(result.current.klines.length).toBeGreaterThan(0));
    });

    // 必须正好 3 根(不是 3+5=8 根假 bar)
    expect(result.current.klines.length).toBe(3);
    // 每根时间戳都是真实输入,无合成值
    const ts = result.current.klines.map((k) => k.timestamp ?? k.open_time);
    expect(ts).toEqual([1000, 1000 + H, 1000 + 7 * H]);
    // 无 volume=0 的伪造 bar(旧填平法用 volume:0 标记)
    expect(result.current.klines.some((k) => k.volume === 0)).toBe(false);
  });

  it("小缺口不触发清缓存重拉(诚实稀疏,不折腾)", async () => {
    const smallGap = [makeKline(1000, 90), makeKline(1000 + 10 * H, 95)]; // 缺 9 根
    vi.mocked(getKlineWithCache).mockResolvedValue({
      klines: smallGap,
      fromCache: false,
      source: "local",
    });
    const { result } = renderHook(() => useKlines("ETHUSDT", "1h"));
    await act(async () => {
      await vi.waitFor(() => expect(result.current.klines.length).toBeGreaterThan(0));
    });
    // 小缺口只拉一次,不清缓存重拉
    expect(getKlineWithCache).toHaveBeenCalledTimes(1);
    expect(result.current.klines.length).toBe(2);
  });

  it("超大缺口(>100 根)触发清缓存重拉(缓存带洞 → 重拉)", async () => {
    const hugeGap = [makeKline(1000, 90), makeKline(1000 + 200 * H, 95)]; // 缺 199 根
    vi.mocked(getKlineWithCache).mockResolvedValue({
      klines: hugeGap,
      fromCache: false,
      source: "local",
    });
    const { result } = renderHook(() => useKlines("SOLUSDT", "1h"));
    await act(async () => {
      await vi.waitFor(() => expect(result.current.klines.length).toBeGreaterThan(0));
    });
    // 首次 + 清缓存后重拉 ≥2 次
    expect(vi.mocked(getKlineWithCache).mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
