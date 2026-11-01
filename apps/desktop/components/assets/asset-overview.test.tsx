import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AssetOverview } from "@/components/assets/asset-overview";
import { useAssetsStore } from "@/lib/store/assets";

beforeEach(() => {
  useAssetsStore.setState({ mode: "empty", activeExchange: "binance" });
});

describe("AssetOverview(资产聚合壳)", () => {
  it("默认空态:三交易所 tab + 引导文案", () => {
    render(<AssetOverview />);
    expect(screen.getByRole("button", { name: "币安" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "OKX" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Web3钱包" })).toBeTruthy();
    expect(screen.getByText("暂无资产数据")).toBeTruthy();
    expect(screen.getByRole("button", { name: "查看演示数据" })).toBeTruthy();
  });

  it("点击查看演示 → 演示徽标 + binance 资产表", () => {
    render(<AssetOverview />);
    fireEvent.click(screen.getByRole("button", { name: "查看演示数据" }));
    expect(screen.getByText("演示")).toBeTruthy();
    expect(screen.getByText("总估值 (USD)")).toBeTruthy();
    for (const asset of ["USDT", "BTC", "ETH"]) {
      expect(screen.getByText(asset)).toBeTruthy();
    }
    expect(screen.queryByText("SOL")).toBeNull();
  });

  it("切到 OKX → 显示 okx 资产,不显示 binance", () => {
    render(<AssetOverview />);
    fireEvent.click(screen.getByRole("button", { name: "查看演示数据" }));
    fireEvent.click(screen.getByRole("button", { name: "OKX" }));
    for (const asset of ["USDT", "SOL", "BNB"]) {
      expect(screen.getByText(asset)).toBeTruthy();
    }
    expect(screen.queryByText("BTC")).toBeNull();
  });

  it("切到 Web3钱包 → 显示 XRP/ETH", () => {
    render(<AssetOverview />);
    fireEvent.click(screen.getByRole("button", { name: "查看演示数据" }));
    fireEvent.click(screen.getByRole("button", { name: "Web3钱包" }));
    expect(screen.getByText("XRP")).toBeTruthy();
    expect(screen.getByText("ETH")).toBeTruthy();
    expect(screen.queryByText("BNB")).toBeNull();
  });

  it("演示模式下返回 → 回到空态", () => {
    render(<AssetOverview />);
    fireEvent.click(screen.getByRole("button", { name: "查看演示数据" }));
    fireEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(screen.getByText("暂无资产数据")).toBeTruthy();
    expect(screen.queryByText("演示")).toBeNull();
  });
});
