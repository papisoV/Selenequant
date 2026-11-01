import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { FavoritesPage } from "./favorites-page";
import { useMarketStore } from "@/lib/store/market";
import { useSymbolFavorites } from "@/lib/hooks/use-symbol-favorites";
import { DEFAULT_SYMBOLS } from "@/lib/monitor-feed";

vi.mock("@/lib/hooks/use-symbol-favorites", () => ({
  useSymbolFavorites: vi.fn(),
}));

const mockHook = vi.mocked(useSymbolFavorites);

beforeEach(() => {
  useMarketStore.setState({ pageFilter: { tf: "1h", symbol: "BTC", exchange: "all" } });
});

function stubFavorites(favorites: string[]) {
  mockHook.mockReturnValue({
    favorites,
    toggleFavorite: vi.fn(),
    isFavorite: (s: string) => favorites.includes(s),
    isLoading: false,
  });
}

describe("FavoritesPage(自选页)", () => {
  it("空态:注入 DEFAULT_SYMBOLS demo 12 行 + 演示徽标", () => {
    stubFavorites([]);
    const { container } = render(<FavoritesPage />);
    expect(container.querySelectorAll(".fav-row").length).toBe(DEFAULT_SYMBOLS.length);
    expect(screen.getAllByText("演示").length).toBe(DEFAULT_SYMBOLS.length);
    expect(screen.getAllByRole("button", { name: "+ 收藏" }).length).toBe(DEFAULT_SYMBOLS.length);
  });

  it("有自选:只显自选行,无演示徽标", () => {
    stubFavorites(["BTC", "ETH"]);
    const { container } = render(<FavoritesPage />);
    expect(container.querySelectorAll(".fav-row").length).toBe(2);
    expect(screen.queryByText("演示")).toBeNull();
    expect(screen.getAllByRole("button", { name: "× 删除" }).length).toBe(2);
  });

  it("点行 → setSymBase 同步 symBase 与 pageFilter.symbol(无分叉)", () => {
    stubFavorites(["ETH"]);
    const { container } = render(<FavoritesPage />);
    fireEvent.click(container.querySelector(".fav-row")!);
    const s = useMarketStore.getState();
    expect(s.symBase).toBe("ETH");
    expect(s.pageFilter.symbol).toBe("ETH");
  });

  it("自选行点删除 → toggleFavorite(该符号)", () => {
    const toggle = vi.fn();
    mockHook.mockReturnValue({ favorites: ["BTC"], toggleFavorite: toggle, isFavorite: () => true, isLoading: false });
    const { container } = render(<FavoritesPage />);
    fireEvent.click(container.querySelector(".fav-row button")!);
    expect(toggle).toHaveBeenCalledWith("BTC");
  });

  it("demo 行点收藏 → toggleFavorite(加入自选)", () => {
    const toggle = vi.fn();
    mockHook.mockReturnValue({ favorites: [], toggleFavorite: toggle, isFavorite: () => false, isLoading: false });
    const { container } = render(<FavoritesPage />);
    fireEvent.click(container.querySelector(".fav-row button")!);
    expect(toggle).toHaveBeenCalledWith(DEFAULT_SYMBOLS[0]);
  });
});
