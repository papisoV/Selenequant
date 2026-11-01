import { beforeEach, describe, expect, it } from "vitest";

import { EXTENSION_LEVELS, defaultVisibleLevels } from "@/lib/chart/fib-levels";
import { defaultTimeLevels } from "@/lib/chart/fib-time-levels";
import {
  EMPTY_TREE,
  addGrid,
  addGroup,
  addLevel,
  clearSelection,
  createGrid,
  duplicateGrid,
  moveAnchor,
  removeGrid,
  removeGrids,
  removeGroup,
  removeLevel,
  renameGrid,
  resetIdCounterForTest,
  resetLevels,
  selectAllInRange,
  selectGrid,
  selectRange,
  setGridGroup,
  setLevelStyle,
  setLockedOf,
  setVisibilityOf,
  toggleAutoExtend,
  toggleExtendSide,
  toggleGroupCollapsed,
  toggleLevel,
  toggleLock,
  toggleSelection,
  toggleVisibility,
  updateGrid,
  visibleGrids,
  type DrawingTree,
} from "@/lib/chart/drawing-tree";

const BASE = { start: { time: 1000, price: 100 }, end: { time: 2000, price: 200 }, symbol: "BTCUSDT", timeframe: "1h" };

beforeEach(() => resetIdCounterForTest());

function treeWithOne(): DrawingTree {
  return addGrid(EMPTY_TREE, createGrid({ ...BASE, id: "g1" }));
}

/**
 * 建一棵含若干对象的树并**清空选中**。
 *
 * addGrid 每次都把选中集合替换成新对象,连续 addGrid 之后只剩最后一个;
 * 批量用例需要自己从"什么都没选"开始播种,否则 toggle 会先把最后一个摘掉。
 */
function treeOf(...ids: string[]): DrawingTree {
  let t = EMPTY_TREE;
  for (const id of ids) t = addGrid(t, createGrid({ ...BASE, id }));
  return clearSelection(t);
}

describe("createGrid", () => {
  it("默认档位是 trader 口径,且不含腿两端", () => {
    const g = createGrid(BASE);
    expect(g.levels).toEqual([...defaultVisibleLevels()].sort((a, b) => a - b));
    expect(g.levels).not.toContain(0);
    expect(g.levels).not.toContain(1);
  });

  it("自定义档位会被归一(排序去重剔坏值)", () => {
    const g = createGrid({ ...BASE, levels: [0.618, 0.382, 0.618, Number.NaN, 0.5] });
    expect(g.levels).toEqual([0.382, 0.5, 0.618]);
  });

  it("默认可见、未锁、未自动延伸", () => {
    const g = createGrid(BASE);
    expect([g.visible, g.locked, g.autoExtend]).toEqual([true, false, false]);
  });

  it("未给 id 时自增生成,不重复", () => {
    const a = createGrid(BASE);
    const b = createGrid(BASE);
    expect(a.id).not.toBe(b.id);
  });

  it("基础图形的默认档位是空数组 —— 趋势线不该长出 9 条水平线", () => {
    for (const mode of ["trend", "horizontal", "vertical", "rect"] as const) {
      const g = createGrid({ ...BASE, mode });
      expect(g.mode).toBe(mode);
      expect(g.levels).toEqual([]);
    }
  });

  it("斐波那契家族的默认档位不受影响", () => {
    expect(createGrid({ ...BASE, mode: "price" }).levels).toEqual(
      [...defaultVisibleLevels()].sort((a, b) => a - b),
    );
    expect(createGrid({ ...BASE, mode: "time" }).levels).toEqual(defaultTimeLevels());
  });
});

describe("addGrid / removeGrid", () => {
  it("加入后自动选中", () => {
    const t = treeWithOne();
    expect(t.grids).toHaveLength(1);
    expect([...t.selectedIds]).toEqual(["g1"]);
  });

  it("重复 id 不加", () => {
    const t = treeWithOne();
    expect(addGrid(t, createGrid({ ...BASE, id: "g1" }))).toBe(t);
  });

  it("删除后清掉选中态", () => {
    const t = removeGrid(treeWithOne(), "g1");
    expect(t.grids).toHaveLength(0);
    expect(t.selectedIds.size).toBe(0);
  });

  it("删除不存在的 id 返回同一引用", () => {
    const t = treeWithOne();
    expect(removeGrid(t, "nope")).toBe(t);
  });

  it("删除非选中项保留原选中", () => {
    let t = treeWithOne();
    t = addGrid(t, createGrid({ ...BASE, id: "g2" }));
    t = removeGrid(t, "g1");
    expect([...t.selectedIds]).toEqual(["g2"]);
  });

  it("批量删除只剔掉被删的选中项", () => {
    let t = treeOf("g1", "g2");
    t = toggleSelection(t, "g1");
    t = toggleSelection(t, "g2");
    expect([...t.selectedIds].sort()).toEqual(["g1", "g2"]);
    t = removeGrids(t, ["g1", "g2"]);
    expect(t.grids).toHaveLength(0);
    expect(t.selectedIds.size).toBe(0);
  });

  it("批量删除一个不存在的 id 不改变树", () => {
    const t = treeWithOne();
    expect(removeGrids(t, ["nope"])).toBe(t);
    expect(removeGrids(t, [])).toBe(t);
  });

  it("不修改入参", () => {
    const t = treeWithOne();
    const before = t.grids.length;
    addGrid(t, createGrid({ ...BASE, id: "g2" }));
    expect(t.grids).toHaveLength(before);
  });
});

describe("updateGrid", () => {
  it("局部更新,其余字段保留", () => {
    const t = updateGrid(treeWithOne(), "g1", { label: "主腿" });
    expect(t.grids[0].label).toBe("主腿");
    expect(t.grids[0].symbol).toBe("BTCUSDT");
  });

  it("改 levels 时重新归一", () => {
    const t = updateGrid(treeWithOne(), "g1", { levels: [0.7, 0.3, 0.7] });
    expect(t.grids[0].levels).toEqual([0.3, 0.7]);
  });

  it("找不到时返回同一引用", () => {
    const t = treeWithOne();
    expect(updateGrid(t, "nope", { label: "x" })).toBe(t);
  });

  it("只替换被改对象的引用", () => {
    let t = treeWithOne();
    t = addGrid(t, createGrid({ ...BASE, id: "g2" }));
    const untouched = t.grids.find((g) => g.id === "g2");
    const next = updateGrid(t, "g1", { label: "x" });
    expect(next.grids.find((g) => g.id === "g2")).toBe(untouched);
    expect(next.grids.find((g) => g.id === "g1")).not.toBe(t.grids.find((g) => g.id === "g1"));
  });
});

describe("moveAnchor", () => {
  it("移动 start", () => {
    const t = moveAnchor(treeWithOne(), "g1", 0, { time: 1500, price: 120 });
    expect(t.grids[0].start).toEqual({ time: 1500, price: 120 });
    expect(t.grids[0].end).toEqual({ time: 2000, price: 200 });
  });

  it("移动 end", () => {
    const t = moveAnchor(treeWithOne(), "g1", 1, { time: 2500, price: 220 });
    expect(t.grids[0].end).toEqual({ time: 2500, price: 220 });
  });

  it("锁定的对象拖不动", () => {
    const locked = updateGrid(treeWithOne(), "g1", { locked: true });
    expect(moveAnchor(locked, "g1", 0, { time: 1500, price: 120 })).toBe(locked);
  });

  it("锚点对象不与入参共享引用", () => {
    const anchor = { time: 1500, price: 120 };
    const t = moveAnchor(treeWithOne(), "g1", 0, anchor);
    anchor.price = 999;
    expect(t.grids[0].start.price).toBe(120);
  });

  it("未知 id 返回同一引用", () => {
    const t = treeWithOne();
    expect(moveAnchor(t, "nope", 0, { time: 0, price: 0 })).toBe(t);
  });
});

describe("selectGrid", () => {
  it("选中已存在的对象", () => {
    expect([...selectGrid(treeWithOne(), "g1").selectedIds]).toEqual(["g1"]);
  });

  it("选中不存在的 id 等价清空", () => {
    expect(selectGrid(treeWithOne(), "nope").selectedIds.size).toBe(0);
  });

  it("重复选中同一对象返回同一引用(避免无谓重绘)", () => {
    const t = treeWithOne();
    expect(selectGrid(t, "g1")).toBe(t);
  });

  it("null 清空选择", () => {
    expect(selectGrid(treeWithOne(), null).selectedIds.size).toBe(0);
  });

  it("单选替换整个集合,不是加选", () => {
    let t = treeOf("g1", "g2");
    t = toggleSelection(t, "g1");
    t = toggleSelection(t, "g2");
    expect(t.selectedIds.size).toBe(2);
    expect([...selectGrid(t, "g1").selectedIds]).toEqual(["g1"]);
  });
});

describe("toggleSelection", () => {
  it("加选后再 toggle 一次即取消", () => {
    let u = treeWithOne();
    u = toggleSelection(u, "g1");
    expect(u.selectedIds.size).toBe(0);
    u = toggleSelection(u, "g1");
    expect([...u.selectedIds]).toEqual(["g1"]);
  });

  it("树里没有的 id 返回同一引用", () => {
    const t = treeWithOne();
    expect(toggleSelection(t, "g2")).toBe(t);
  });

  it("多次加选累积", () => {
    let t = treeOf("g1", "g2");
    t = toggleSelection(t, "g1");
    t = toggleSelection(t, "g2");
    expect([...t.selectedIds].sort()).toEqual(["g1", "g2"]);
  });

  it("不修改入参集合", () => {
    const t = treeWithOne();
    const before = t.selectedIds;
    toggleSelection(t, "g1");
    expect(t.selectedIds).toBe(before);
    expect(t.selectedIds.size).toBe(1);
  });
});

describe("selectRange", () => {
  const ORDER = ["g1", "g2", "g3"];

  function three(): DrawingTree {
    let t = treeWithOne();
    t = addGrid(t, createGrid({ ...BASE, id: "g2" }));
    t = addGrid(t, createGrid({ ...BASE, id: "g3" }));
    return t;
  }

  it("正向区间包含两端", () => {
    expect([...selectRange(three(), ORDER, "g1", "g2").selectedIds].sort()).toEqual(["g1", "g2"]);
  });

  it("反向区间等价(锚点在目标之后)", () => {
    expect([...selectRange(three(), ORDER, "g3", "g2").selectedIds].sort()).toEqual(["g2", "g3"]);
  });

  it("按给定顺序而不是存储顺序取区间", () => {
    const shuffled = ["g3", "g1", "g2"];
    expect([...selectRange(three(), shuffled, "g3", "g1").selectedIds].sort()).toEqual(["g1", "g3"]);
  });

  it("锚点不在顺序里退化为单选目标", () => {
    expect([...selectRange(three(), ORDER, "gone", "g2").selectedIds]).toEqual(["g2"]);
  });

  it("目标不在树里返回同一引用", () => {
    const t = three();
    expect(selectRange(t, ORDER, "g1", "nope")).toBe(t);
  });
});

describe("selectAllInRange / clearSelection", () => {
  it("全选加入范围内所有对象并保留范围外的选中", () => {
    let t = treeOf("g1", "g2", "g3");
    t = toggleSelection(t, "g1");
    t = toggleSelection(t, "g3");
    expect([...t.selectedIds].sort()).toEqual(["g1", "g3"]);
    const next = selectAllInRange(t, ["g1", "g2"]);
    expect([...next.selectedIds].sort()).toEqual(["g1", "g2", "g3"]);
  });

  it("空范围不改变树", () => {
    const t = treeWithOne();
    expect(selectAllInRange(t, [])).toBe(t);
  });

  it("已全选返回同一引用", () => {
    let t = treeOf("g1", "g2");
    t = selectAllInRange(t, ["g1", "g2"]);
    expect(selectAllInRange(t, ["g1", "g2"])).toBe(t);
  });

  it("清空空集合返回同一引用", () => {
    const t = selectGrid(treeWithOne(), null);
    expect(clearSelection(t)).toBe(t);
  });
});

describe("batch visibility / lock", () => {
  function trio(): DrawingTree {
    return treeOf("g1", "g2", "g3");
  }

  it("批量隐藏是绝对值,不是 toggle", () => {
    // 让 g2 先隐藏,再整组隐藏 —— g2 不该被翻回可见
    let t = setVisibilityOf(trio(), ["g2"], false);
    t = setVisibilityOf(t, ["g1", "g2", "g3"], false);
    expect(t.grids.map((g) => g.visible)).toEqual([false, false, false]);
  });

  it("批量显示同理", () => {
    let t = setVisibilityOf(trio(), ["g1", "g3"], false);
    t = setVisibilityOf(t, ["g1", "g2", "g3"], true);
    expect(t.grids.every((g) => g.visible)).toBe(true);
  });

  it("状态已符合时返回同一引用", () => {
    const t = trio();
    expect(setVisibilityOf(t, ["g1"], true)).toBe(t);
    expect(setLockedOf(t, ["g1"], false)).toBe(t);
  });

  it("批量锁定把被锁对象移出选中集,未锁对象保留", () => {
    let t = treeOf("g1", "g2", "g3");
    t = selectAllInRange(t, ["g1", "g2", "g3"]);
    t = setLockedOf(t, ["g1", "g2"], true);
    expect([...t.selectedIds]).toEqual(["g3"]);
    expect(t.grids.map((g) => g.locked)).toEqual([true, true, false]);
  });

  it("批量解锁不自动选中", () => {
    let t = setLockedOf(trio(), ["g1", "g2"], true);
    t = setLockedOf(t, ["g1"], false);
    expect(t.selectedIds.size).toBe(0);
    expect(t.grids.map((g) => g.locked)).toEqual([false, true, false]);
  });

  it("空 id 列表返回同一引用", () => {
    const t = trio();
    expect(setVisibilityOf(t, [], false)).toBe(t);
    expect(setLockedOf(t, [], true)).toBe(t);
  });
});

describe("toggleVisibility / toggleLock", () => {
  it("切换可见性", () => {
    const t = toggleVisibility(treeWithOne(), "g1");
    expect(t.grids[0].visible).toBe(false);
    expect(toggleVisibility(t, "g1").grids[0].visible).toBe(true);
  });

  it("锁定时取消选中", () => {
    const t = toggleLock(treeWithOne(), "g1");
    expect(t.grids[0].locked).toBe(true);
    expect(t.selectedIds.size).toBe(0);
  });

  it("解锁不自动选中", () => {
    const locked = toggleLock(treeWithOne(), "g1");
    expect(toggleLock(locked, "g1").selectedIds.size).toBe(0);
  });
});

describe("toggleAutoExtend", () => {
  it("开启时并入延伸位", () => {
    const t = toggleAutoExtend(treeWithOne(), "g1", EXTENSION_LEVELS);
    expect(t.grids[0].autoExtend).toBe(true);
    for (const lv of EXTENSION_LEVELS) expect(t.grids[0].levels).toContain(lv);
  });

  it("关闭时只去掉延伸位,内部档位不受影响", () => {
    const on = toggleAutoExtend(treeWithOne(), "g1", EXTENSION_LEVELS);
    const off = toggleAutoExtend(on, "g1", EXTENSION_LEVELS);
    expect(off.grids[0].autoExtend).toBe(false);
    for (const lv of EXTENSION_LEVELS) expect(off.grids[0].levels).not.toContain(lv);
    expect(off.grids[0].levels).toEqual(on.grids[0].levels.filter((lv) => !EXTENSION_LEVELS.includes(lv)));
  });

  it("重复开关不会累积重复档位", () => {
    let t = treeWithOne();
    for (let i = 0; i < 3; i++) {
      t = toggleAutoExtend(t, "g1", EXTENSION_LEVELS);
      t = toggleAutoExtend(t, "g1", EXTENSION_LEVELS);
    }
    expect(new Set(t.grids[0].levels).size).toBe(t.grids[0].levels.length);
  });
});

describe("toggleLevel / resetLevels", () => {
  it("取消已勾选的档位", () => {
    const t = toggleLevel(treeWithOne(), "g1", 0.5);
    expect(t.grids[0].levels).not.toContain(0.5);
  });

  it("勾选未选的档位", () => {
    // 默认档位隐藏了腿两端的 0 与 1,所以这里用 1 作探针
    expect(treeWithOne().grids[0].levels).not.toContain(1);
    const t = toggleLevel(treeWithOne(), "g1", 1);
    expect(t.grids[0].levels).toContain(1);
  });

  it("恢复默认档位会去掉延伸位", () => {
    const on = toggleAutoExtend(treeWithOne(), "g1", EXTENSION_LEVELS);
    const reset = resetLevels(on, "g1");
    for (const lv of EXTENSION_LEVELS) expect(reset.grids[0].levels).not.toContain(lv);
  });
});

describe("addLevel / removeLevel(自定义档位)", () => {
  it("加一条任意比例,并保持升序", () => {
    const t = addLevel(treeWithOne(), "g1", 0.45);
    expect(t.grids[0].levels).toContain(0.45);
    expect([...t.grids[0].levels]).toEqual([...t.grids[0].levels].sort((a, b) => a - b));
  });

  it("比例可以是负数或大于 1(延伸位本来就是这么画的)", () => {
    let t = addLevel(treeWithOne(), "g1", -0.5);
    t = addLevel(t, "g1", 3.14);
    expect(t.grids[0].levels).toContain(-0.5);
    expect(t.grids[0].levels).toContain(3.14);
  });

  it("非有限数被拒(不能往 levels 里塞 NaN)", () => {
    const base = treeWithOne();
    for (const bad of [Number.NaN, Infinity, -Infinity]) {
      expect(addLevel(base, "g1", bad)).toBe(base);
    }
  });

  it("重复比例是空操作,不动引用", () => {
    const base = treeWithOne();
    expect(addLevel(base, "g1", 0.5)).toBe(base);
  });

  it("未知对象是空操作", () => {
    const base = treeWithOne();
    expect(addLevel(base, "nope", 0.45)).toBe(base);
    expect(removeLevel(base, "nope", 0.5)).toBe(base);
  });

  it("删一条档位", () => {
    const t = removeLevel(treeWithOne(), "g1", 0.5);
    expect(t.grids[0].levels).not.toContain(0.5);
  });

  it("删不存在的档位不动引用", () => {
    const base = treeWithOne();
    expect(removeLevel(base, "g1", 0.45)).toBe(base);
  });

  it("删档位时连带清掉它的样式覆盖 —— 否则加回来会带回旧颜色", () => {
    let t = addLevel(treeWithOne(), "g1", 0.45);
    t = setLevelStyle(t, "g1", 0.45, { color: "#ff0000" });
    t = removeLevel(t, "g1", 0.45);
    expect(t.grids[0].levelStyles ?? []).toEqual([]);
    t = addLevel(t, "g1", 0.45);
    expect(t.grids[0].levelStyles ?? []).toEqual([]);
  });

  it("删档位不影响别的档位的样式", () => {
    let t = addLevel(treeWithOne(), "g1", 0.45);
    t = setLevelStyle(t, "g1", 0.45, { color: "#ff0000" });
    t = setLevelStyle(t, "g1", 0.5, { color: "#00ff00" });
    t = removeLevel(t, "g1", 0.45);
    expect(t.grids[0].levelStyles).toEqual([{ coeff: 0.5, color: "#00ff00" }]);
  });

  it("可以删到一条都不剩 —— 空档位是合法状态", () => {
    let t = treeWithOne();
    for (const lv of [...t.grids[0].levels]) t = removeLevel(t, "g1", lv);
    expect(t.grids[0].levels).toEqual([]);
  });
});

describe("renameGrid", () => {
  it("裁剪空白", () => {
    expect(renameGrid(treeWithOne(), "g1", "  主腿  ").grids[0].label).toBe("主腿");
  });

  it("空名允许", () => {
    expect(renameGrid(treeWithOne(), "g1", "   ").grids[0].label).toBe("");
  });
});

describe("duplicateGrid", () => {
  it("新 id、新引用,可按 offset 错开", () => {
    const t = duplicateGrid(treeWithOne(), "g1", { time: 100, price: 5 });
    expect(t.grids).toHaveLength(2);
    const copy = t.grids[1];
    expect(copy.id).not.toBe("g1");
    expect(copy.start).toEqual({ time: 1100, price: 105 });
    expect(copy.levels).not.toBe(t.grids[0].levels);
    expect([...t.selectedIds]).toEqual([copy.id]);
  });

  it("未知 id 返回同一引用", () => {
    const t = treeWithOne();
    expect(duplicateGrid(t, "nope")).toBe(t);
  });
});

describe("visibleGrids", () => {
  it("按标的过滤", () => {
    let t = treeWithOne();
    t = addGrid(t, createGrid({ ...BASE, id: "g2", symbol: "ETHUSDT" }));
    expect(visibleGrids(t, "BTCUSDT").map((g) => g.id)).toEqual(["g1"]);
  });

  it("隐藏的对象不返回", () => {
    const t = toggleVisibility(treeWithOne(), "g1");
    expect(visibleGrids(t, "BTCUSDT")).toHaveLength(0);
  });

  it("跨周期仍显示(锚点与周期无关)", () => {
    let t = treeWithOne();
    t = updateGrid(t, "g1", { timeframe: "4h" });
    expect(visibleGrids(t, "BTCUSDT")).toHaveLength(1);
  });
});

describe("分组", () => {
  it("加组 / 归组 / 折叠", () => {
    let t = addGroup(treeWithOne(), " 主网格 ");
    const gid = t.groups[0].id;
    expect(t.groups[0].name).toBe("主网格");
    t = setGridGroup(t, "g1", gid);
    expect(t.grids[0].groupId).toBe(gid);
    t = toggleGroupCollapsed(t, gid);
    expect(t.groups[0].collapsed).toBe(true);
  });

  it("删除分组不删组内对象,只把它们放回顶层", () => {
    let t = addGroup(treeWithOne(), "grp");
    const gid = t.groups[0].id;
    t = setGridGroup(t, "g1", gid);
    t = removeGroup(t, gid);
    expect(t.grids).toHaveLength(1);
    expect(t.grids[0].groupId).toBeNull();
    expect(t.groups).toHaveLength(0);
  });

  it("归入不存在的分组被拒绝", () => {
    const t = treeWithOne();
    expect(setGridGroup(t, "g1", "nope")).toBe(t);
  });

  it("空组名给默认名", () => {
    expect(addGroup(EMPTY_TREE, "   ").groups[0].name).toBe("未命名分组");
  });
});

describe("横向延伸(extendLeft / extendRight)", () => {
  it("默认两侧都不延伸 —— 新画的网格就是线段", () => {
    const g = treeWithOne().grids[0];
    expect(g.extendLeft).toBe(false);
    expect(g.extendRight).toBe(false);
  });

  it("两侧独立切换,互不影响", () => {
    let t = toggleExtendSide(treeWithOne(), "g1", "left");
    expect(t.grids[0].extendLeft).toBe(true);
    expect(t.grids[0].extendRight).toBe(false);
    t = toggleExtendSide(t, "g1", "right");
    expect(t.grids[0].extendLeft).toBe(true);
    expect(t.grids[0].extendRight).toBe(true);
    t = toggleExtendSide(t, "g1", "left");
    expect(t.grids[0].extendLeft).toBe(false);
    expect(t.grids[0].extendRight).toBe(true);
  });

  it("与 autoExtend 不联动 —— 一个改线的长度,一个改档位", () => {
    let t = toggleAutoExtend(treeWithOne(), "g1", EXTENSION_LEVELS);
    expect(t.grids[0].autoExtend).toBe(true);
    expect(t.grids[0].extendLeft).toBe(false);
    expect(t.grids[0].extendRight).toBe(false);
    t = toggleExtendSide(t, "g1", "right");
    expect(t.grids[0].autoExtend).toBe(true);
    expect(t.grids[0].levels).toEqual(expect.arrayContaining([...EXTENSION_LEVELS]));
  });

  it("未知网格返回原树(引用不变)", () => {
    const t = treeWithOne();
    expect(toggleExtendSide(t, "nope", "left")).toBe(t);
  });
});

describe("单档样式覆盖", () => {
  it("只影响被指定的那一档", () => {
    const t = setLevelStyle(treeWithOne(), "g1", 0.618, { color: "#ff0000", width: 3 });
    expect(t.grids[0].levelStyles).toEqual([{ coeff: 0.618, color: "#ff0000", width: 3 }]);
  });

  it("多次设置同一档是合并,不是覆盖整条", () => {
    let t = setLevelStyle(treeWithOne(), "g1", 0.618, { color: "#ff0000" });
    t = setLevelStyle(t, "g1", 0.618, { width: 2 });
    expect(t.grids[0].levelStyles).toEqual([{ coeff: 0.618, color: "#ff0000", width: 2 }]);
  });

  it("多档按比例升序排列", () => {
    let t = setLevelStyle(treeWithOne(), "g1", 0.786, { color: "#00ff00" });
    t = setLevelStyle(t, "g1", 0.382, { color: "#0000ff" });
    expect(t.grids[0].levelStyles!.map((s) => s.coeff)).toEqual([0.382, 0.786]);
  });

  it("空串 / null 清掉该项覆盖,全部清空后整条消失", () => {
    let t = setLevelStyle(treeWithOne(), "g1", 0.5, { color: "#ff0000" });
    t = setLevelStyle(t, "g1", 0.5, { color: "" });
    expect(t.grids[0].levelStyles).toEqual([]);
  });

  it("线宽被夹进合法区间,NaN 不落库", () => {
    let t = setLevelStyle(treeWithOne(), "g1", 0.5, { width: 40 });
    expect(t.grids[0].levelStyles![0].width).toBe(6);
    t = setLevelStyle(treeWithOne(), "g1", 0.5, { width: Number.NaN });
    expect(t.grids[0].levelStyles![0].width).toBe(1);
  });

  it("别名去空白后为空视为清除", () => {
    let t = setLevelStyle(treeWithOne(), "g1", 0.5, { text: "TP1" });
    expect(t.grids[0].levelStyles![0].text).toBe("TP1");
    t = setLevelStyle(t, "g1", 0.5, { text: "   " });
    expect(t.grids[0].levelStyles).toEqual([]);
  });

  it("恢复默认档位同时清空样式覆盖 —— 否则勾回同一档会带回旧颜色", () => {
    let t = setLevelStyle(treeWithOne(), "g1", 0.5, { color: "#ff0000" });
    t = resetLevels(t, "g1");
    expect(t.grids[0].levels).toEqual(defaultVisibleLevels());
    expect(t.grids[0].levelStyles).toEqual([]);
  });
});
