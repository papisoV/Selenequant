import { describe, expect, it } from "vitest";

import type { FibGrid } from "@/lib/chart/fib-object";
import {
  type DrawingKeyContext,
  createKeyOwner,
  isEditableTarget,
  planDrawingKey,
} from "@/lib/chart/drawing-keys";

const grid = (id: string, locked = false): FibGrid => ({
  id,
  groupId: null,
  start: { time: 1, price: 100 },
  end: { time: 10, price: 110 },
  mode: "price",
  levels: [],
  autoExtend: false,
  extendLeft: false,
  extendRight: false,
  visible: true,
  locked,
  timeframe: "1m",
  symbol: "BTCUSDT",
  label: id,
});

const ctx = (over: Partial<DrawingKeyContext> = {}): DrawingKeyContext => ({
  grids: [grid("a"), grid("b"), grid("c")],
  selectedIds: new Set<string>(),
  activeTool: "select",
  havePending: false,
  ...over,
});

const key = (k: string, over: Partial<KeyboardEvent> = {}): KeyboardEvent =>
  ({ key: k, metaKey: false, ctrlKey: false, altKey: false, target: null, ...over }) as KeyboardEvent;

describe("planDrawingKey — Delete", () => {
  it("删掉整个选中集合", () => {
    const a = planDrawingKey(key("Delete"), ctx({ selectedIds: new Set(["a", "c"]) }));
    expect(a).toEqual({ kind: "delete", ids: ["a", "c"] });
  });

  it("Backspace 等价于 Delete(Mac 键盘没有 Del 键)", () => {
    const a = planDrawingKey(key("Backspace"), ctx({ selectedIds: new Set(["b"]) }));
    expect(a).toEqual({ kind: "delete", ids: ["b"] });
  });

  it("没有选中 → 不管,让默认行为过去", () => {
    expect(planDrawingKey(key("Delete"), ctx())).toBeNull();
  });

  it("锁定的对象留在原地,其余照删", () => {
    const a = planDrawingKey(
      key("Delete"),
      ctx({ grids: [grid("a"), grid("b", true)], selectedIds: new Set(["a", "b"]) }),
    );
    expect(a).toEqual({ kind: "delete", ids: ["a"] });
  });

  it("选中的全是锁定的 → 什么都不做,也不拦截按键", () => {
    const a = planDrawingKey(
      key("Delete"),
      ctx({ grids: [grid("a", true)], selectedIds: new Set(["a"]) }),
    );
    expect(a).toBeNull();
  });

  it("选中集合里有已被删掉的 id 时,只按现存对象来", () => {
    const a = planDrawingKey(key("Delete"), ctx({ selectedIds: new Set(["a", "ghost"]) }));
    expect(a).toEqual({ kind: "delete", ids: ["a"] });
  });

  it("带 ⌘/Ctrl/Alt 的组合不归绘图管(留给系统剪切)", () => {
    for (const mod of [{ metaKey: true }, { ctrlKey: true }, { altKey: true }]) {
      expect(planDrawingKey(key("Delete", mod), ctx({ selectedIds: new Set(["a"]) }))).toBeNull();
    }
  });
});

describe("planDrawingKey — Escape 级联", () => {
  it("第一级:先丢掉画了一半的对象,工具和选中都还在", () => {
    expect(
      planDrawingKey(key("Escape"), ctx({ havePending: true, activeTool: "fib", selectedIds: new Set(["a"]) })),
    ).toEqual({ kind: "cancel-pending" });
  });

  it("第二级:没有半成品 → 卸下工具", () => {
    expect(
      planDrawingKey(key("Escape"), ctx({ activeTool: "trend", selectedIds: new Set(["a"]) })),
    ).toEqual({ kind: "cancel-tool" });
  });

  it("第三级:已经是选择态 → 清空选中", () => {
    expect(planDrawingKey(key("Escape"), ctx({ selectedIds: new Set(["a"]) }))).toEqual({
      kind: "clear-selection",
    });
  });

  it("什么都没有就什么都不做", () => {
    expect(planDrawingKey(key("Escape"), ctx())).toBeNull();
  });
});

describe("planDrawingKey — 焦点守卫", () => {
  it("输入框 / 文本域 / 下拉框里按键属于控件", () => {
    for (const tag of ["INPUT", "TEXTAREA", "SELECT"]) {
      const target = { tagName: tag, isContentEditable: false } as unknown as HTMLElement;
      expect(planDrawingKey(key("Delete", { target }), ctx({ selectedIds: new Set(["a"]) }))).toBeNull();
      expect(planDrawingKey(key("Escape", { target }), ctx({ selectedIds: new Set(["a"]) }))).toBeNull();
    }
  });

  it("contentEditable 的 div 同理", () => {
    const target = { tagName: "DIV", isContentEditable: true } as unknown as HTMLElement;
    expect(planDrawingKey(key("Delete", { target }), ctx({ selectedIds: new Set(["a"]) }))).toBeNull();
  });

  it("普通 div / canvas 不挡", () => {
    const target = { tagName: "CANVAS", isContentEditable: false } as unknown as HTMLElement;
    expect(planDrawingKey(key("Delete", { target }), ctx({ selectedIds: new Set(["a"]) }))).toEqual({
      kind: "delete",
      ids: ["a"],
    });
  });

  it("isEditableTarget 对 null 安全", () => {
    expect(isEditableTarget(null)).toBe(false);
  });

  it("无关按键直接放行", () => {
    for (const k of ["a", "Enter", "ArrowUp", " "]) {
      expect(planDrawingKey(key(k), ctx({ selectedIds: new Set(["a"]), havePending: true }))).toBeNull();
    }
  });
});

describe("createKeyOwner", () => {
  it("点过谁,谁拥有键盘", () => {
    const owner = createKeyOwner();
    const one = {};
    const two = {};
    owner.register(one);
    owner.register(two);
    owner.focus(two);
    expect(owner.owns(two)).toBe(true);
    expect(owner.owns(one)).toBe(false);
    owner.focus(one);
    expect(owner.owns(one)).toBe(true);
    expect(owner.owns(two)).toBe(false);
  });

  it("从没点过时,最后挂载的那张拥有(刷新后不点鼠标也能按 Delete)", () => {
    const owner = createKeyOwner();
    const one = {};
    const two = {};
    owner.register(one);
    expect(owner.owns(one)).toBe(true);
    owner.register(two);
    expect(owner.owns(two)).toBe(true);
    expect(owner.owns(one)).toBe(false);
  });

  it("聚焦的图卸载 → 让给剩下最后挂载的一张", () => {
    const owner = createKeyOwner();
    const one = {};
    const two = {};
    owner.register(one);
    owner.register(two);
    owner.focus(two);
    owner.unregister(two);
    expect(owner.owns(one)).toBe(true);
  });

  it("一张图都没挂载时谁都不拥有", () => {
    const owner = createKeyOwner();
    expect(owner.owns({})).toBe(false);
  });

  it("重复 register 不会在名单里留重复项", () => {
    const owner = createKeyOwner();
    const one = {};
    const two = {};
    owner.register(one);
    owner.register(two);
    owner.register(one); // 重新挂载 = 变成最新的
    owner.unregister(one);
    owner.unregister(one);
    expect(owner.owns(one)).toBe(false);
    expect(owner.owns(two)).toBe(true);
  });
});
