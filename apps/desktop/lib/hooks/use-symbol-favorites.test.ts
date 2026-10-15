import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { toPair, useSymbolFavorites, resetFavoritesStoreForTest } from "./use-symbol-favorites";

// === shared mock state (hoisted) ===========================================
const h = vi.hoisted(() => ({
  api: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  api: h.api,
  apiPost: h.apiPost,
  apiPut: h.apiPut,
}));

// === import AFTER mocks ====================================================

describe("toPair 归一化", () => {
  it("crypto 裸符号补 /USDT；完整对/带横线原样；非加密市场存裸符号；ambiguous 兜底 crypto", () => {
    expect(toPair("BTC")).toBe("BTC/USDT");
    expect(toPair("btc")).toBe("BTC/USDT");
    expect(toPair("SOL")).toBe("SOL/USDT");
    expect(toPair("BTC/USDT")).toBe("BTC/USDT");
    expect(toPair("ETH-USDT")).toBe("ETH-USDT");
    // 非加密市场路由：存裸符号
    expect(toPair("AAPL")).toBe("AAPL");
    expect(toPair("600519")).toBe("600519");
    expect(toPair("CU0")).toBe("CU0");
    // ambiguous（ATOM/LTC 等）兜底视为 crypto
    expect(toPair("ATOM")).toBe("ATOM/USDT");
    expect(toPair("  SOL ")).toBe("SOL/USDT");
    expect(toPair("")).toBe("");
  });
});

describe("useSymbolFavorites 存储完整对", () => {
  beforeEach(() => {
    localStorage.clear();
    h.api.mockReset();
    h.apiPost.mockReset();
    h.apiPut.mockReset();
    // zustand 共享 store 跨用例重置（模块级单例）
    resetFavoritesStoreForTest();
  });

  it("跨实例同步：一个实例 toggle，另一实例 favorites 立即可见", async () => {
    h.api.mockResolvedValue({ symbols: [] });
    h.apiPost.mockResolvedValue({ symbols: ["BTC/USDT"] });

    const a = renderHook(() => useSymbolFavorites());
    const b = renderHook(() => useSymbolFavorites());
    await waitFor(() => expect(a.result.current.isLoading).toBe(false));

    await act(async () => {
      await a.result.current.toggleFavorite("BTC");
    });

    // b 实例无需自己的请求即看到更新
    expect(b.result.current.favorites).toEqual(["BTC/USDT"]);
    expect(b.result.current.isFavorite("BTC")).toBe(true);
    a.unmount();
    b.unmount();
  });

  it("初始化只发一次后端 GET（多组件挂载不重复同步）", async () => {
    h.api.mockResolvedValue({ symbols: [] });

    renderHook(() => useSymbolFavorites());
    renderHook(() => useSymbolFavorites());
    renderHook(() => useSymbolFavorites());

    await waitFor(() => expect(h.api).toHaveBeenCalledTimes(1));
  });

  it("后端返回裸符号 → 归一化进 state + PUT 回写迁移", async () => {
    h.api.mockResolvedValue({ symbols: ["BTC", "ETH/USDT"] });
    h.apiPut.mockResolvedValue({ symbols: ["BTC/USDT", "ETH/USDT"] });

    const { result } = renderHook(() => useSymbolFavorites());

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.favorites).toEqual(["BTC/USDT", "ETH/USDT"]);
    // 覆盖式回写后端
    await waitFor(() =>
      expect(h.apiPut).toHaveBeenCalledWith("/api/v1/favorites", {
        symbols: ["BTC/USDT", "ETH/USDT"],
      }),
    );
  });

  it("后端已是完整对 → 不触发回写", async () => {
    h.api.mockResolvedValue({ symbols: ["BTC/USDT"] });

    const { result } = renderHook(() => useSymbolFavorites());

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.favorites).toEqual(["BTC/USDT"]);
    expect(h.apiPut).not.toHaveBeenCalled();
  });

  it("toggle 裸符号 → localStorage 与后端均存完整对", async () => {
    h.api.mockResolvedValue({ symbols: [] });
    h.apiPost.mockResolvedValue({ symbols: ["ETH/USDT"] });

    const { result } = renderHook(() => useSymbolFavorites());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.toggleFavorite("ETH");
    });

    expect(h.apiPost).toHaveBeenCalledWith("/api/v1/favorites/toggle", { symbol: "ETH/USDT" });
    expect(result.current.favorites).toEqual(["ETH/USDT"]);
    expect(JSON.parse(localStorage.getItem("selenespace-symbol-favorites") ?? "[]")).toEqual([
      "ETH/USDT",
    ]);
  });

  it("toggle toggle API 失败 → PUT 兜底覆盖式同步", async () => {
    h.api.mockResolvedValue({ symbols: ["BTC/USDT"] });
    h.apiPost.mockRejectedValue(new Error("toggle down"));
    h.apiPut.mockResolvedValue({ symbols: ["BTC/USDT", "ETH/USDT"] });

    const { result } = renderHook(() => useSymbolFavorites());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.toggleFavorite("ETH");
    });

    expect(h.apiPut).toHaveBeenCalledWith("/api/v1/favorites", {
      symbols: ["ETH/USDT", "BTC/USDT"],
    });
    expect(result.current.favorites).toEqual(["BTC/USDT", "ETH/USDT"]);
  });

  it("isFavorite 完整对判断（调用方传裸符号也命中）", async () => {
    h.api.mockResolvedValue({ symbols: ["BTC/USDT"] });

    const { result } = renderHook(() => useSymbolFavorites());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.isFavorite("BTC/USDT")).toBe(true);
    expect(result.current.isFavorite("BTC")).toBe(true);
    expect(result.current.isFavorite("ETH")).toBe(false);
  });

  it("toggle 非加密符号（AAPL）→ 存裸符号，不补 /USDT", async () => {
    h.api.mockResolvedValue({ symbols: [] });
    h.apiPost.mockResolvedValue({ symbols: ["AAPL"] });

    const { result } = renderHook(() => useSymbolFavorites());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.toggleFavorite("AAPL");
    });

    expect(h.apiPost).toHaveBeenCalledWith("/api/v1/favorites/toggle", { symbol: "AAPL" });
    expect(result.current.favorites).toEqual(["AAPL"]);
    expect(result.current.isFavorite("AAPL")).toBe(true);
  });
});
