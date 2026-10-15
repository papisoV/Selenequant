import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { useState } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, fireEvent } from "@testing-library/react";
import { WorkbenchTabBar, GuideStrip, WbPane } from "@/components/research/alpha-mining-tabs";

// vitest 会把 `?raw` CSS import stub 成空串;直接读文件拿真实样式契约
// (jsdom 下 import.meta.url 非文件协议,用 cwd 相对路径定位,vitest 从 apps/desktop 起跑)
const cssText = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

/** 五 pane + 共享 active 的最小 harness,复刻 AlphaMiningPanel 的显隐矩阵接线。 */
function Harness() {
  const [active, setActive] = useState(0);
  return (
    <>
      <WorkbenchTabBar active={active} onChange={setActive} />
      <GuideStrip active={active} onChange={setActive} />
      <WbPane active={active} index={0} testId="pane-train">
        <button>训练内容按钮</button>
      </WbPane>
      <WbPane active={active} index={1} testId="pane-validate">
        <button>验证内容按钮</button>
      </WbPane>
      <WbPane active={active} index={2} testId="pane-backtest">
        <button>回测内容按钮</button>
      </WbPane>
      <WbPane active={active} index={3} testId="pane-monitor">
        <button>监控内容按钮</button>
      </WbPane>
      <WbPane active={active} index={4} testId="pane-governance">
        <button>治理内容按钮</button>
      </WbPane>
    </>
  );
}

// jsdom 不加载全局样式表,getByRole 的 display:none 语义需要把显隐规则注入文档
beforeEach(() => {
  const style = document.createElement("style");
  style.dataset.testid = "wb-vis-rules";
  style.textContent = ".wb-pane{display:none}.wb-pane.on{display:block}";
  document.head.appendChild(style);
});

afterEach(() => {
  document.querySelector('style[data-testid="wb-vis-rules"]')?.remove();
});

describe("Alpha 工作台 tab 骨架", () => {
  it("五 tab 显隐矩阵:仅活动 pane 可被 role 命中,其余 display:none 不可见", () => {
    render(<Harness />);
    // 活动 pane(训练)内容可点
    expect(screen.getByRole("button", { name: /训练内容按钮/ })).toBeInTheDocument();
    // 非活动 pane 内容被 display:none 隐藏 → getByRole 过滤
    expect(screen.queryByRole("button", { name: /回测内容按钮/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /监控内容按钮/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /治理内容按钮/ })).toBeNull();
    // 但 DOM 节点都在(getByText 不滤 visibility)
    expect(screen.getByText(/回测内容按钮/)).toBeInTheDocument();
    expect(screen.queryAllByRole("tab").length).toBe(5);
  });

  it("切换动画类:点 tab 后 .on 类在 pane 间迁移,动画 keyframes + reduced-motion 降级存在于全局样式", () => {
    render(<Harness />);
    const backtestPane = () => document.querySelector('[data-testid="pane-backtest"]') as HTMLElement;
    const monitorPane = () => document.querySelector('[data-testid="pane-monitor"]') as HTMLElement;
    expect(backtestPane().classList.contains("on")).toBe(false);
    fireEvent.click(screen.getByRole("tab", { name: "回测" }));
    expect(backtestPane().classList.contains("on")).toBe(true);
    fireEvent.click(screen.getByRole("tab", { name: "监控" }));
    expect(backtestPane().classList.contains("on")).toBe(false);
    expect(monitorPane().classList.contains("on")).toBe(true);
    // 全局样式契约:120ms fade+slide 动画 + prefers-reduced-motion 降级
    expect(cssText).toContain("@keyframes wbPaneIn");
    expect(cssText).toContain("animation: wbPaneIn 120ms");
    expect(cssText).toContain("prefers-reduced-motion");
    expect(cssText).toContain(".wb-pane {");
    expect(cssText).toContain("display: none");
  });

  it("guide 双向联动:点 guide 步切 tab,切 tab 同步 step-on", () => {
    render(<Harness />);
    // 默认 active=0 → guide-step-0 高亮
    expect(screen.getByTestId("guide-step-0").classList.contains("step-on")).toBe(true);
    // 点 guide 第 3 步(回测)→ active=2
    fireEvent.click(screen.getByTestId("guide-step-2"));
    expect(screen.getByTestId("guide-step-2").classList.contains("step-on")).toBe(true);
    expect(screen.getByRole("tab", { name: "回测" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("guide-step-0").classList.contains("step-on")).toBe(false);
    // 反向:点 tab(监控)→ guide-step-3 同步 step-on
    fireEvent.click(screen.getByRole("tab", { name: "监控" }));
    expect(screen.getByTestId("guide-step-3").classList.contains("step-on")).toBe(true);
    expect(screen.getByRole("tab", { name: "监控" }).getAttribute("aria-selected")).toBe("true");
  });
});
