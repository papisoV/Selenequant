import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { freshnessLabel, SessionStamp } from "./session-stamp";

// 交叉判据的由来:这里的每一条断言单独看都「显然」,但它们锁的是一组
// **互斥**的约定 —— 一旦有人为了「统一」把 SessionStamp 换成 AshareFreshness
// (那个原语新鲜时返回 null),A1-4 这条就会红。
describe("freshnessLabel", () => {
  it.each([
    // scope,       asOf,        期望
    ["provisional", "20261015", "盘中暂定 · 20261015"],
    ["settled", "20241031", "截至 20241031"],
    ["provisional", null, "盘中暂定"],
    ["settled", null, null],
    ["provisional", undefined, "盘中暂定"],
    ["settled", undefined, null],
    ["provisional", "", "盘中暂定"],
    ["settled", "", null],
  ] as const)("scope=%s asOf=%s → %s", (scope, asOf, expected) => {
    expect(freshnessLabel(asOf, scope)).toBe(expected);
  });

  it("默认 scope 是 settled", () => {
    expect(freshnessLabel("20241031")).toBe("截至 20241031");
    expect(freshnessLabel(null)).toBeNull();
  });

  it("空白字符串等同于没给日期", () => {
    // 后端漏字段时常见 `""`:`截至 ` 这种半截文案比不渲染更糟。
    expect(freshnessLabel("   ", "settled")).toBeNull();
    expect(freshnessLabel("   ", "provisional")).toBe("盘中暂定");
  });

  it("A1-4 回归: 会话日 = 今天, 日期照常显示", () => {
    // 这是与 AshareFreshness 的根本分歧点 —— 那个原语在新鲜时返 null
    // (`freshnessBadge("20261021","20261021") === null`)。照抄它会让
    // 「今天的数据」在所有卡片右上角失去日期,比现状更糟。
    expect(freshnessLabel("20261021", "settled")).toBe("截至 20261021");
  });
});

describe("SessionStamp", () => {
  it("A1-4 回归(组件层): 新鲜数据的日期不消失", () => {
    // today 根本不参与:SessionStamp 没有 today 参数,这正是防照抄的设计。
    const { container } = render(<SessionStamp asOf="20261021" />);
    expect(container.textContent).toContain("截至 20261021");
  });

  it("provisional 渲染盘中暂定 + 日期", () => {
    const { container } = render(
      <SessionStamp asOf="20261015" scope="provisional" />,
    );
    expect(container.textContent).toBe("盘中暂定 · 20261015");
  });

  it("A1-5 空 asOf + settled → 不渲染任何节点", () => {
    const { container } = render(<SessionStamp asOf={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("A1-5 空 asOf + provisional → 渲染无日期的盘中暂定", () => {
    const { container } = render(
      <SessionStamp asOf={null} scope="provisional" />,
    );
    expect(container.textContent).toBe("盘中暂定");
  });

  it("A1-6 不含任何颜色类 —— 颜色预算已满,时标只承担中性语义", () => {
    const { container } = render(<SessionStamp asOf="20241031" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain("text-ink-4");
    for (const hue of ["text-gold", "text-emerald", "text-red", "text-cyan"]) {
      expect(el.className).not.toContain(hue);
    }
  });

  it("A1-6 可与 AshareFreshness 并排而不争样式", () => {
    // 不写死字号:父级槽位(V17Card/BandHead)自带 text-[9px],两个
    // arbitrary value 打架的结果取决于样式表顺序,不可控。
    const { container } = render(<SessionStamp asOf="20241031" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).not.toMatch(/text-\[\d+px\]/);
  });

  it("className 透传,供确需覆盖的调用点使用", () => {
    const { container } = render(
      <SessionStamp asOf="20241031" className="ml-1" />,
    );
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain("ml-1");
    expect(el.className).toContain("text-ink-4");
  });
});
