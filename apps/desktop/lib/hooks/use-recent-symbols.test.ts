import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRecentSymbols, type RecentSymbol } from "./use-recent-symbols";

const RECENTS_KEY = "selenespace-recent-symbols";

function item(symbol: string, market = "us"): RecentSymbol {
  return { symbol, market, name: symbol };
}

describe("useRecentSymbols", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("starts empty when localStorage has nothing", () => {
    const { result } = renderHook(() => useRecentSymbols());
    expect(result.current.recents).toEqual([]);
  });

  it("recordRecent prepends, dedupes by symbol, and persists", () => {
    const { result } = renderHook(() => useRecentSymbols());

    act(() => result.current.recordRecent(item("BTC", "crypto")));
    act(() => result.current.recordRecent(item("AAPL")));
    act(() => result.current.recordRecent(item("BTC", "crypto"))); // dedupe

    expect(result.current.recents.map((r) => r.symbol)).toEqual(["BTC", "AAPL"]);
    const persisted = JSON.parse(localStorage.getItem(RECENTS_KEY) ?? "[]");
    expect(persisted.map((r: RecentSymbol) => r.symbol)).toEqual(["BTC", "AAPL"]);
  });

  it("caps at MAX_RECENTS (10), newest first", () => {
    const { result } = renderHook(() => useRecentSymbols());
    for (let i = 0; i < 12; i += 1) {
      act(() => result.current.recordRecent(item(`S${i}`)));
    }
    expect(result.current.recents).toHaveLength(10);
    // S11 is newest (prepended last), S1..S9 remain; S0,S1 dropped
    expect(result.current.recents[0].symbol).toBe("S11");
    expect(result.current.recents.map((r) => r.symbol)).not.toContain("S0");
  });

  it("hydrates from existing localStorage on mount", () => {
    localStorage.setItem(RECENTS_KEY, JSON.stringify([item("600519", "ashare")]));
    const { result } = renderHook(() => useRecentSymbols());
    expect(result.current.recents).toEqual([item("600519", "ashare")]);
  });

  it("clearRecents empties state and localStorage", () => {
    const { result } = renderHook(() => useRecentSymbols());
    act(() => result.current.recordRecent(item("BTC", "crypto")));
    act(() => result.current.clearRecents());
    expect(result.current.recents).toEqual([]);
    expect(localStorage.getItem(RECENTS_KEY)).toBe("[]");
  });
});
