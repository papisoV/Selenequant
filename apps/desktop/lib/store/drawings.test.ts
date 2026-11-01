import { beforeEach, describe, expect, it } from "vitest";

import { EXTENSION_LEVELS } from "@/lib/chart/fib-levels";
import { groupRows, useDrawingsStore } from "@/lib/store/drawings";

const INPUT = {
  start: { time: 1000, price: 100 },
  end: { time: 2000, price: 200 },
  symbol: "BTCUSDT",
  timeframe: "1h",
};

const store = () => useDrawingsStore.getState();

beforeEach(() => {
  store().resetForTest();
  localStorage.clear();
});

describe("useDrawingsStore", () => {
  it("创建后自动选中并落盘", () => {
    const id = store().createFromDrag(INPUT);
    expect(store().grids).toHaveLength(1);
    expect([...store().selectedIds]).toEqual([id]);
    expect(JSON.parse(localStorage.getItem("selenespace-fib-drawings")!).grids).toHaveLength(1);
  });

  it("默认档位不含腿两端,且与 trader 口径一致", () => {
    store().createFromDrag(INPUT);
    const levels = store().grids[0].levels;
    expect(levels).not.toContain(0);
    expect(levels).not.toContain(1);
    expect(levels).toContain(0.5);
  });

  it("删除会清掉选中态并落盘", () => {
    const id = store().createFromDrag(INPUT);
    store().remove(id);
    expect(store().grids).toHaveLength(0);
    expect(store().selectedIds.size).toBe(0);
    expect(JSON.parse(localStorage.getItem("selenespace-fib-drawings")!).grids).toHaveLength(0);
  });

  it("批量删除一次落盘", () => {
    const a = store().createFromDrag(INPUT);
    const b = store().createFromDrag(INPUT);
    store().selectAllIn([a, b]);
    expect(store().selectedIds.size).toBe(2);
    store().removeMany([a, b]);
    expect(store().grids).toHaveLength(0);
    expect(store().selectedIds.size).toBe(0);
    expect(JSON.parse(localStorage.getItem("selenespace-fib-drawings")!).grids).toHaveLength(0);
  });

  it("多选 / 区间选择 / 清空", () => {
    const a = store().createFromDrag(INPUT);
    const b = store().createFromDrag(INPUT);
    const c = store().createFromDrag(INPUT);
    store().select(a);
    expect([...store().selectedIds]).toEqual([a]);
    store().toggleSelect(b);
    expect([...store().selectedIds].sort()).toEqual([a, b].sort());
    store().selectRangeTo([a, b, c], a, c);
    expect([...store().selectedIds].sort()).toEqual([a, b, c].sort());
    store().clearSelection();
    expect(store().selectedIds.size).toBe(0);
  });

  it("批量显隐是绝对值,且落盘", () => {
    const a = store().createFromDrag(INPUT);
    const b = store().createFromDrag(INPUT);
    store().setVisibleOf([b], false);
    store().setVisibleOf([a, b], false);
    expect(store().grids.map((g) => g.visible)).toEqual([false, false]);
    const saved = JSON.parse(localStorage.getItem("selenespace-fib-drawings")!);
    expect(saved.grids.map((g: { visible: boolean }) => g.visible)).toEqual([false, false]);
  });

  it("批量锁定把对象移出选中集", () => {
    const a = store().createFromDrag(INPUT);
    const b = store().createFromDrag(INPUT);
    store().selectAllIn([a, b]);
    store().setLockedOf([a], true);
    expect(store().grids.map((g) => g.locked)).toEqual([true, false]);
    expect([...store().selectedIds]).toEqual([b]);
  });

  it("移动锚点后落盘反映新位置", () => {
    const id = store().createFromDrag(INPUT);
    store().move(id, 0, { time: 1200, price: 90 });
    expect(store().grids[0].start).toEqual({ time: 1200, price: 90 });
    const saved = JSON.parse(localStorage.getItem("selenespace-fib-drawings")!);
    expect(saved.grids[0].start).toEqual({ time: 1200, price: 90 });
  });

  it("锁定后不可移动", () => {
    const id = store().createFromDrag(INPUT);
    store().toggleLockOf(id);
    store().move(id, 0, { time: 1200, price: 90 });
    expect(store().grids[0].start).toEqual({ time: 1000, price: 100 });
  });

  it("切换自动延伸并入/剥离延伸位", () => {
    const id = store().createFromDrag(INPUT);
    store().toggleExtend(id);
    for (const lv of EXTENSION_LEVELS) expect(store().grids[0].levels).toContain(lv);
    store().toggleExtend(id);
    for (const lv of EXTENSION_LEVELS) expect(store().grids[0].levels).not.toContain(lv);
  });

  it("改名 / 复制 / 分组", () => {
    const id = store().createFromDrag(INPUT);
    store().rename(id, " 主腿 ");
    expect(store().grids[0].label).toBe("主腿");

    store().duplicate(id, { time: 100, price: 0 });
    expect(store().grids).toHaveLength(2);

    store().addGroupNamed("第一组");
    const gid = store().groups[0].id;
    store().assignGroup(id, gid);
    expect(store().grids[0].groupId).toBe(gid);

    store().collapseGroup(gid);
    expect(store().groups[0].collapsed).toBe(true);

    store().removeGroupById(gid);
    expect(store().grids.filter((g) => g.id === id)[0].groupId).toBeNull();
  });

  it("hydrate 从 localStorage 恢复", () => {
    localStorage.setItem(
      "selenespace-fib-drawings",
      JSON.stringify({
        version: 1,
        grids: [{ ...INPUT, id: "restored", levels: [0.5], autoExtend: false, visible: true, locked: false, groupId: null, label: "" }],
      }),
    );
    store().hydrate();
    expect(store().grids.map((g) => g.id)).toEqual(["restored"]);
    expect(store().hydrated).toBe(true);
  });

  it("hydrate 遇到坏 JSON 收敛为空树,不抛", () => {
    localStorage.setItem("selenespace-fib-drawings", "{broken");
    store().hydrate();
    expect(store().grids).toEqual([]);
    expect(store().hydrated).toBe(true);
  });

  it("重复 hydrate 不会覆盖已有对象", () => {
    const id = store().createFromDrag(INPUT);
    store().hydrate();
    expect(store().grids.map((g) => g.id)).toEqual([id]);
  });

  it("单点工具(水平线 / 竖线)把两个锚点压成落笔处那一点", () => {
    for (const mode of ["horizontal", "vertical"] as const) {
      store().resetForTest();
      store().createFromDrag({ ...INPUT, mode });
      const g = store().grids[0];
      expect(g.mode).toBe(mode);
      expect(g.start).toEqual(g.end);
      expect(g.end).toEqual({ time: 2000, price: 200 });
      // 几何只认 end,但存档要能原样读回同一点
      expect(g.levels).toEqual([]);
    }
  });

  it("两点基础图形保留两个锚点,档位为空", () => {
    for (const mode of ["trend", "rect"] as const) {
      store().resetForTest();
      store().createFromDrag({ ...INPUT, mode });
      const g = store().grids[0];
      expect(g.mode).toBe(mode);
      expect(g.start).toEqual({ time: 1000, price: 100 });
      expect(g.end).toEqual({ time: 2000, price: 200 });
      expect(g.levels).toEqual([]);
    }
  });

  it("基础图形落盘后重新 hydrate 仍是基础图形", () => {
    store().createFromDrag({ ...INPUT, mode: "trend" });
    // 只清内存,不清 localStorage —— 这里要验证的正是存档能读回来
    useDrawingsStore.setState({ grids: [], hydrated: false });
    store().hydrate();
    expect(store().grids[0].mode).toBe("trend");
    expect(store().grids[0].levels).toEqual([]);
  });
});

describe("groupRows", () => {
  it("无分组时只有顶层一行", () => {
    const id = store().createFromDrag(INPUT);
    const rows = groupRows(store().grids, store().groups);
    expect(rows).toHaveLength(1);
    expect(rows[0].group).toBeNull();
    expect(rows[0].grids.map((g) => g.id)).toEqual([id]);
  });

  it("分组行按 groups 顺序排在顶层之后", () => {
    const id = store().createFromDrag(INPUT);
    store().addGroupNamed("A");
    store().addGroupNamed("B");
    const [a, b] = store().groups.map((g) => g.id);
    store().assignGroup(id, b);
    const rows = groupRows(store().grids, store().groups);
    expect(rows.map((r) => r.group?.name ?? null)).toEqual(["A", "B"]);
    expect(rows[1].grids.map((g) => g.id)).toEqual([id]);
  });

  it("指向已删分组的孤儿对象回到顶层,不从树里消失", () => {
    const id = store().createFromDrag(INPUT);
    store().addGroupNamed("A");
    const gid = store().groups[0].id;
    store().assignGroup(id, gid);
    // 只删分组不经过 removeGroup 的话 groupId 会悬空;这里模拟这种坏状态
    useDrawingsStore.setState({ groups: [] });
    const rows = groupRows(store().grids, store().groups);
    expect(rows[0].group).toBeNull();
    expect(rows[0].grids.map((g) => g.id)).toEqual([id]);
  });

  it("空组也占一行", () => {
    store().addGroupNamed("空组");
    const rows = groupRows(store().grids, store().groups);
    expect(rows).toHaveLength(1);
    expect(rows[0].grids).toEqual([]);
  });
});
