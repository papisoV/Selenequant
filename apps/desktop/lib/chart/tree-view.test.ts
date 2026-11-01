import { describe, expect, it } from "vitest";

import {
  filterRowsByQuery,
  gridMatches,
  groupLockedState,
  groupVisibleState,
  nextGroupFlag,
  normalizeQuery,
  triState,
  type RowLike,
} from "./tree-view";
import type { FibGrid } from "./fib-object";

function grid(over: Partial<FibGrid> = {}): FibGrid {
  return {
    id: "fib1",
    groupId: null,
    start: { time: 1_700_000_000, price: 100 },
    end: { time: 1_700_003_600, price: 110 },
    mode: "price",
    levels: [0, 0.5, 1],
    autoExtend: false,
    extendLeft: false,
    extendRight: false,
    visible: true,
    locked: false,
    timeframe: "1h",
    symbol: "BTC",
    label: "",
    ...over,
  };
}

function row(name: string | null, grids: FibGrid[]): RowLike {
  return { group: name === null ? null : { name }, grids };
}

describe("normalizeQuery", () => {
  it("去首尾空白并转小写", () => {
    expect(normalizeQuery("  BOLL  ")).toBe("boll");
  });

  it("空串与纯空白都归一为空串", () => {
    expect(normalizeQuery("")).toBe("");
    expect(normalizeQuery("   \t\n ")).toBe("");
  });
});

describe("gridMatches", () => {
  it("空查询恒命中(调用方不必特判)", () => {
    expect(gridMatches(grid({ label: "α" }), "")).toBe(true);
    expect(gridMatches(grid({ label: "α" }), "   ")).toBe(true);
  });

  it("按标签命中,大小写不敏感", () => {
    expect(gridMatches(grid({ label: "Breakout" }), "break")).toBe(true);
    expect(gridMatches(grid({ label: "Breakout" }), "BREAK")).toBe(true);
    expect(gridMatches(grid({ label: "Breakout" }), "retrace")).toBe(false);
  });

  it("未命名对象用兜底名参与搜索(中英都认)", () => {
    const unnamed = grid({ label: "" });
    expect(gridMatches(unnamed, "斐波那契")).toBe(true);
    expect(gridMatches(unnamed, "fibonacci")).toBe(true);
    expect(gridMatches(unnamed, "nothing")).toBe(false);
  });

  it("纯空白的标签按未命名处理,不是「命中一切」", () => {
    expect(gridMatches(grid({ label: "   " }), "fibonacci")).toBe(true);
  });

  it("标的与周期也参与搜索", () => {
    expect(gridMatches(grid({ symbol: "ETHUSDT" }), "eth")).toBe(true);
    expect(gridMatches(grid({ label: "α", timeframe: "4h" }), "4h")).toBe(true);
    expect(gridMatches(grid({ label: "α", symbol: "BTC", timeframe: "1h" }), "sol")).toBe(false);
  });
});

describe("filterRowsByQuery", () => {
  const a = grid({ id: "a", label: "α" });
  const b = grid({ id: "b", label: "β" });
  const c = grid({ id: "c", label: "γ" });

  it("空查询原样返回,但给的是新数组", () => {
    const rows = [row(null, [a, b]), row("G1", [c])];
    const out = filterRowsByQuery(rows, "");
    expect(out.map((r) => r.grids.length)).toEqual([2, 1]);
    expect(out).not.toBe(rows);
  });

  it("成员命中 → 只留命中的成员", () => {
    const out = filterRowsByQuery([row("G1", [a, b, c])], "β");
    expect(out.length).toBe(1);
    expect(out[0].grids.map((g) => g.id)).toEqual(["b"]);
  });

  it("分组名命中 → 整组成员都保留", () => {
    const out = filterRowsByQuery([row("趋势线", [a, b]), row("其他", [c])], "趋势");
    expect(out.length).toBe(1);
    expect(out[0].grids.map((g) => g.id)).toEqual(["a", "b"]);
  });

  it("都不命中 → 整行丢弃(搜索里不留空壳组)", () => {
    const out = filterRowsByQuery([row("G1", [a]), row("G2", [b])], "zzz");
    expect(out).toEqual([]);
  });

  it("顶层行(group=null)按成员过滤,不会因组名缺失而误命中", () => {
    const out = filterRowsByQuery([row(null, [a, b])], "α");
    expect(out.length).toBe(1);
    expect(out[0].group).toBeNull();
    expect(out[0].grids.map((g) => g.id)).toEqual(["a"]);
  });

  it("空成员分组在无查询时保留,有查询时丢弃", () => {
    const empty = row("空组", []);
    expect(filterRowsByQuery([empty], "").length).toBe(1);
    expect(filterRowsByQuery([empty], "空").length).toBe(1); // 组名命中
    expect(filterRowsByQuery([empty], "没这个").length).toBe(0);
  });
});

describe("triState", () => {
  it("空 → none", () => {
    expect(triState([])).toBe("none");
  });

  it("全 true → all,全 false → none", () => {
    expect(triState([true, true])).toBe("all");
    expect(triState([false, false])).toBe("none");
  });

  it("混杂 → mixed", () => {
    expect(triState([true, false])).toBe("mixed");
    expect(triState([false, true, true])).toBe("mixed");
  });
});

describe("groupVisibleState / groupLockedState", () => {
  it("按成员算汇总", () => {
    expect(groupVisibleState([grid({ visible: true }), grid({ visible: true })])).toBe("all");
    expect(groupVisibleState([grid({ visible: true }), grid({ visible: false })])).toBe("mixed");
  });

  it("空组 → none(眼睛显示未显示、锁显示未锁)", () => {
    expect(groupVisibleState([])).toBe("none");
    expect(groupLockedState([])).toBe("none");
  });

  it("锁定态与可见性互不影响", () => {
    const g = [grid({ visible: true, locked: true })];
    expect(groupVisibleState(g)).toBe("all");
    expect(groupLockedState(g)).toBe("all");
  });
});

describe("nextGroupFlag", () => {
  it("已全开 → 关", () => {
    expect(nextGroupFlag("all")).toBe(false);
  });

  it("全关或混合 → 开(混合时统一到开,不各翻各的)", () => {
    expect(nextGroupFlag("none")).toBe(true);
    expect(nextGroupFlag("mixed")).toBe(true);
  });
});
