import { describe, expect, it, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { BlockComposer } from "./block-composer";

// PageRenderer 走 lazy 加载 + SWR 预载,与本次布局断言无关——替换为占位节点
vi.mock("./page-renderer", () => ({
  PageRenderer: ({ meta }: { meta: { slug: string } }) => <div data-testid="rendered">{meta.slug}</div>,
}));

const gridOf = (root: HTMLElement) => root.querySelector(".grid.min-h-0.flex-1") as HTMLElement;
const tabsOf = (root: HTMLElement) => Array.from(root.querySelectorAll("[role=tab]")) as HTMLElement[];

const compose = (defaultTab?: string) => (
  <BlockComposer
    panels={[{ slug: "pos", label: "持仓总览" }]}
    tabs={[{ slug: "portfolio", label: "组合构建" }]}
    defaultTab={defaultTab}
  />
);

describe("BlockComposer 布局", () => {
  it("defaultTab 指向 panel slug → 不激活任何 tab,panel 独占整行", () => {
    // trade/pos 的实况:defaultTab="pos" 是 panel 名,不是 tab 名
    const { container } = render(compose("pos"));
    // 两行会把 647px 内容塞进 368px 的格子,导致 Block 越过栅格线压在下一行上
    expect(gridOf(container).style.gridTemplateRows).toBe("minmax(0, 1fr)");
    expect(tabsOf(container)[0].getAttribute("aria-selected")).toBe("false");
  });

  it("defaultTab 命中 tab slug → 双行布局,tab 激活", () => {
    const { container } = render(compose("portfolio"));
    expect(gridOf(container).style.gridTemplateRows).toBe("minmax(0, 1fr) minmax(0, 1fr)");
    expect(tabsOf(container)[0].getAttribute("aria-selected")).toBe("true");
  });

  it("未指定 defaultTab → 回退首个 tab", () => {
    const { container } = render(compose());
    expect(gridOf(container).style.gridTemplateRows).toBe("minmax(0, 1fr) minmax(0, 1fr)");
  });

  it("每个栅格单元都裁剪溢出 —— Block 不得压到相邻行", () => {
    const { container } = render(compose("portfolio"));
    const cells = Array.from(gridOf(container).children) as HTMLElement[];
    expect(cells.length).toBe(2); // panel 格 + tab 格
    for (const cell of cells) {
      // 无 overflow-hidden 时,内容高的 Block 会画出格子外(实测 652px 内容 / 368px 格)
      expect(cell.className).toContain("overflow-hidden");
      expect(cell.className).toContain("min-h-0");
    }
  });

  it("点击已激活 tab(有 panels 时) → 取消激活,收回双行", () => {
    const { container } = render(compose("portfolio"));
    fireEvent.click(tabsOf(container)[0]);
    expect(gridOf(container).style.gridTemplateRows).toBe("minmax(0, 1fr)");
  });
});
