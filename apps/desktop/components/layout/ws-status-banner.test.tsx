import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// === mock store（组件经 selector 订阅 zustand）===
const h = vi.hoisted(() => ({
  state: {
    marketsStreamState: "recovering" as "live" | "degraded" | "recovering",
    marketsEverLive: false,
  },
}));

vi.mock("@/lib/store/market", () => ({
  useMarketStore: (selector: (s: typeof h.state) => unknown) => selector(h.state),
}));

import { WsStatusIndicator } from "./ws-status-banner";

describe("WsStatusIndicator（CtxBar 行情状态位）", () => {
  beforeEach(() => {
    h.state.marketsStreamState = "recovering";
    h.state.marketsEverLive = false;
  });

  it("冷启动噪音门：首次 live 之前不渲染", () => {
    const { container } = render(<WsStatusIndicator />);
    expect(container).toBeEmptyDOMElement();
  });

  it("degraded 不受冷启动门：从未连上（everLive=false）也显示延迟警告", () => {
    // 2026-09-30 WS 全瘫事故盲区：从未连上时状态位静默两周，degraded 是强信号
    h.state.marketsEverLive = false;
    h.state.marketsStreamState = "degraded";
    render(<WsStatusIndicator />);
    expect(screen.getByText("延迟")).toBeTruthy();
    expect(document.querySelector(".conn-dot.warn")).toBeTruthy();
  });

  it("live：绿点 + 实时", () => {
    h.state.marketsEverLive = true;
    h.state.marketsStreamState = "live";
    render(<WsStatusIndicator />);
    expect(screen.getByText("实时")).toBeTruthy();
    expect(document.querySelector(".conn-dot.live")).toBeTruthy();
  });

  it("degraded：warn 点 + 延迟 + tooltip 说明 REST 轮询", () => {
    h.state.marketsEverLive = true;
    h.state.marketsStreamState = "degraded";
    render(<WsStatusIndicator />);
    expect(screen.getByText("延迟")).toBeTruthy();
    expect(document.querySelector(".conn-dot.warn")).toBeTruthy();
    expect(screen.getByTitle(/REST 轮询/)).toBeTruthy();
  });

  it("recovering（曾连上后断开）：warn 点 + 重连", () => {
    h.state.marketsEverLive = true;
    h.state.marketsStreamState = "recovering";
    render(<WsStatusIndicator />);
    expect(screen.getByText("重连")).toBeTruthy();
    expect(document.querySelector(".conn-dot.warn")).toBeTruthy();
  });
});
