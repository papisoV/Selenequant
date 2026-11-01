import { describe, expect, it } from "vitest";

import {
  DRAWINGS_SCHEMA_VERSION,
  parseDrawings,
  parseDrawingsJson,
  parseGrid,
  serializeDrawings,
} from "@/lib/chart/drawing-persist";
import { HAND_DRAWN_LEVELS } from "@/lib/chart/fib-levels";
import { defaultTimeLevels } from "@/lib/chart/fib-time-levels";
import type { FibGrid } from "@/lib/chart/fib-object";

function valid(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "g1",
    groupId: null,
    start: { time: 1000, price: 100 },
    end: { time: 2000, price: 200 },
    levels: [0.382, 0.5, 0.618],
    autoExtend: false,
    extendLeft: false,
    extendRight: false,
    visible: true,
    locked: false,
    timeframe: "1h",
    symbol: "BTCUSDT",
    label: "",
    ...over,
  };
}

describe("parseGrid", () => {
  it("合法对象原样解析", () => {
    const g = parseGrid(valid())!;
    expect(g.id).toBe("g1");
    expect(g.levels).toEqual([0.382, 0.5, 0.618]);
    expect(g.symbol).toBe("BTCUSDT");
  });

  it("缺 id 直接丢弃", () => {
    expect(parseGrid(valid({ id: undefined }))).toBeNull();
    expect(parseGrid(valid({ id: "   " }))).toBeNull();
    expect(parseGrid(valid({ id: 42 }))).toBeNull();
  });

  it("锚点缺字段或非有限数时丢弃", () => {
    expect(parseGrid(valid({ start: { time: 1000 } }))).toBeNull();
    expect(parseGrid(valid({ end: { price: 200 } }))).toBeNull();
    expect(parseGrid(valid({ start: { time: "1000", price: 100 } }))).toBeNull();
    expect(parseGrid(valid({ end: { time: 2000, price: Number.NaN } }))).toBeNull();
    expect(parseGrid(valid({ start: null }))).toBeNull();
  });

  it("levels 里的坏值被剔除,余下排序去重", () => {
    const g = parseGrid(valid({ levels: [0.618, "x", Number.NaN, 0.382, 0.618, Infinity, 0.5] }))!;
    expect(g.levels).toEqual([0.382, 0.5, 0.618]);
  });

  it("levels 字段缺失 / 不是数组时回落到 trader 档位族", () => {
    // 这两种是"旧存档 / 坏壳",没有用户的意图可读 —— 给默认族最安全。
    expect(parseGrid(valid({ levels: "nope" }))!.levels).toHaveLength(HAND_DRAWN_LEVELS.length);
    expect(parseGrid(valid({ levels: undefined }))!.levels).toHaveLength(HAND_DRAWN_LEVELS.length);
    const legacy = valid();
    delete legacy.levels;
    expect(parseGrid(legacy)!.levels).toHaveLength(HAND_DRAWN_LEVELS.length);
  });

  it("空数组是用户的意图(把档位删光了),不回填默认族", () => {
    // 对象树支持逐档删除。若把 [] 当"缺省"回填 11 条,用户删完重开应用
    // 档位会自己长回来 —— 删除看上去没生效,且无从查起。
    expect(parseGrid(valid({ levels: [] }))!.levels).toEqual([]);
    expect(parseGrid(valid({ mode: "time", levels: [] }))!.levels).toEqual([]);
  });

  it("数组里全是坏值时回落默认族(数据坏了,不是用户删空了)", () => {
    expect(parseGrid(valid({ levels: ["x", Number.NaN] }))!.levels).toHaveLength(
      HAND_DRAWN_LEVELS.length,
    );
  });

  it("布尔字段缺省时取安全默认(可见、未锁、不自动延伸、不横跨全宽)", () => {
    const g = parseGrid(valid({ visible: undefined, locked: "yes", autoExtend: 1 }))!;
    expect(g.visible).toBe(true);
    expect(g.locked).toBe(false);
    expect(g.autoExtend).toBe(false);
    expect(g.extendLeft).toBe(false);
    expect(g.extendRight).toBe(false);
  });

  it("v1 旧数据(无 extendLeft/extendRight)读成线段默认", () => {
    const legacy = valid();
    delete legacy.extendLeft;
    delete legacy.extendRight;
    const g = parseGrid(legacy)!;
    expect(g.extendLeft).toBe(false);
    expect(g.extendRight).toBe(false);
  });

  it("extendLeft/extendRight 只接受真布尔", () => {
    const g = parseGrid(valid({ extendLeft: true, extendRight: "yes" }))!;
    expect(g.extendLeft).toBe(true);
    expect(g.extendRight).toBe(false);
  });

  it("groupId 空串归一为 null", () => {
    expect(parseGrid(valid({ groupId: "" }))!.groupId).toBeNull();
    expect(parseGrid(valid({ groupId: "  " }))!.groupId).toBeNull();
    expect(parseGrid(valid({ groupId: "grp" }))!.groupId).toBe("grp");
  });

  it("非对象输入返回 null", () => {
    for (const bad of [null, undefined, 1, "g", [], true]) {
      expect(parseGrid(bad)).toBeNull();
    }
  });

  it("mode 缺省(所有 v1/v2 存档)读成 price", () => {
    expect(parseGrid(valid())!.mode).toBe("price");
    expect(parseGrid(valid({ mode: undefined }))!.mode).toBe("price");
    expect(parseGrid(valid({ mode: "nope" }))!.mode).toBe("price");
    expect(parseGrid(valid({ mode: 1 }))!.mode).toBe("price");
  });

  it("mode=time 原样保留", () => {
    expect(parseGrid(valid({ mode: "time" }))!.mode).toBe("time");
  });

  it("时间网格缺 levels 时回落到时间倍数族,不是价格比例族", () => {
    const g = parseGrid(valid({ mode: "time", levels: undefined }))!;
    expect(g.levels).toEqual([...defaultTimeLevels()].sort((a, b) => a - b));
    // 关键:不能落进 0.382 那种比例,否则竖线会挤在腿起点附近
    expect(g.levels.every((k) => k >= 1)).toBe(true);
    expect(g.levels).not.toContain(0.5);
  });

  it("时间网格的坏 levels 同样剔除并排序", () => {
    const g = parseGrid(valid({ mode: "time", levels: [8, "x", 3, Number.NaN, 3] }))!;
    expect(g.levels).toEqual([3, 8]);
  });

  it("基础图形(趋势线 / 水平线 / 竖线 / 矩形)原样保留 mode", () => {
    for (const mode of ["trend", "horizontal", "vertical", "rect"] as const) {
      expect(parseGrid(valid({ mode }))!.mode).toBe(mode);
    }
  });

  it("基础图形缺 levels 时是空数组,不回落 trader 档位族", () => {
    // 关键回归:曾经无条件回落,结果一条趋势线凭空长出 9 条水平线。
    for (const mode of ["trend", "horizontal", "vertical", "rect"] as const) {
      expect(parseGrid(valid({ mode, levels: undefined }))!.levels).toEqual([]);
      expect(parseGrid(valid({ mode, levels: [] }))!.levels).toEqual([]);
      expect(parseGrid(valid({ mode, levels: "nope" }))!.levels).toEqual([]);
    }
  });

  it("基础图形即便存档里带着 levels 也照样保留 —— 解析层不擅自改用户的数", () => {
    // 几何渲染端(trend/horizontal/...)根本不看 levels,所以留着无害;
    // 解析层只管"合不合法",不管"有没有用"。
    expect(parseGrid(valid({ mode: "trend", levels: [0.5] }))!.levels).toEqual([0.5]);
  });
});

describe("parseDrawings", () => {
  it("逐条解析,坏对象只丢自己", () => {
    const grids = parseDrawings({
      version: 1,
      grids: [valid({ id: "a" }), valid({ id: "b", start: null }), valid({ id: "c" })],
    });
    expect(grids.map((g) => g.id)).toEqual(["a", "c"]);
  });

  it("重复 id 只保留第一个", () => {
    const grids = parseDrawings({ grids: [valid({ id: "dup" }), valid({ id: "dup" })] });
    expect(grids).toHaveLength(1);
  });

  it("裸数组也能解析(兼容早期写坏的格式)", () => {
    const grids = parseDrawings([valid({ id: "a" }), valid({ id: "b" })]);
    expect(grids.map((g) => g.id)).toEqual(["a", "b"]);
  });

  it("非对象 / 无 grids 字段返回空数组", () => {
    expect(parseDrawings(null)).toEqual([]);
    expect(parseDrawings({})).toEqual([]);
    expect(parseDrawings("nope")).toEqual([]);
    expect(parseDrawings({ grids: "nope" })).toEqual([]);
  });
});

describe("serializeDrawings", () => {
  it("写入当前 schema 版本", () => {
    expect(serializeDrawings([]).version).toBe(DRAWINGS_SCHEMA_VERSION);
    expect(serializeDrawings([]).version).toBe(4);
  });

  it("levels 拷贝成数组,不与源共享引用", () => {
    const src: FibGrid = {
      id: "g",
      groupId: null,
      start: { time: 0, price: 1 },
      end: { time: 1, price: 2 },
      mode: "price",
      levels: [0.5],
      autoExtend: false,
      extendLeft: false,
      extendRight: false,
      visible: true,
      locked: false,
      timeframe: "1h",
      symbol: "BTCUSDT",
      label: "",
    };
    const out = serializeDrawings([src]);
    expect(out.grids[0].levels).toEqual([0.5]);
    expect(out.grids[0].levels).not.toBe(src.levels);
  });

  it("序列化 → 解析往返不丢信息", () => {
    const src: FibGrid[] = [
      {
        id: "g1",
        groupId: "grp",
        start: { time: 1000, price: 100.5 },
        end: { time: 2000, price: 200.25 },
        mode: "price",
        levels: [0.382, 0.5, 1.272],
        autoExtend: true,
        extendLeft: true,
        extendRight: false,
        visible: false,
        locked: true,
        timeframe: "4h",
        symbol: "ETHUSDT",
        label: "主腿",
      },
    ];
    expect(parseDrawings(serializeDrawings(src))).toEqual(src);
  });

  it("时间网格往返后仍是时间网格(倍数不被当成比例改写)", () => {
    const src: FibGrid[] = [
      {
        id: "t1",
        groupId: null,
        start: { time: 1000, price: 100 },
        end: { time: 2000, price: 140 },
        mode: "time",
        levels: [2, 3, 5, 8],
        autoExtend: false,
        extendLeft: false,
        extendRight: false,
        visible: true,
        locked: false,
        timeframe: "1h",
        symbol: "BTCUSDT",
        label: "",
      },
    ];
    const back = parseDrawings(serializeDrawings(src));
    expect(back).toEqual(src);
    expect(back[0].mode).toBe("time");
    expect(back[0].levels).toEqual([2, 3, 5, 8]);
  });

  it("基础图形往返后 mode 与空 levels 都不变", () => {
    const shapes = ["trend", "horizontal", "vertical", "rect"] as const;
    const src: FibGrid[] = shapes.map((mode, i) => ({
      id: `s${i}`,
      groupId: null,
      start: { time: 1000 + i, price: 100 },
      end: { time: 2000 + i, price: 200 },
      mode,
      levels: [],
      autoExtend: false,
      extendLeft: false,
      extendRight: false,
      visible: true,
      locked: false,
      timeframe: "1h",
      symbol: "BTCUSDT",
      label: "",
    }));
    const back = parseDrawings(serializeDrawings(src));
    expect(back).toEqual(src);
    expect(back.map((g) => g.mode)).toEqual([...shapes]);
    for (const g of back) expect(g.levels).toEqual([]);
  });
});

describe("parseDrawingsJson", () => {
  it("坏 JSON 返回空数组,不抛", () => {
    expect(parseDrawingsJson("{not json")).toEqual([]);
    expect(parseDrawingsJson("")).toEqual([]);
    expect(parseDrawingsJson(null)).toEqual([]);
  });

  it("往返 JSON 字符串", () => {
    const grids = parseDrawingsJson(JSON.stringify({ version: 1, grids: [valid()] }));
    expect(grids).toHaveLength(1);
    expect(grids[0].id).toBe("g1");
  });
});

describe("levelStyles 持久化", () => {
  it("合法覆盖原样往返", () => {
    const g = parseGrid(valid({ levelStyles: [{ coeff: 0.618, color: "#ff0000", width: 2, dashed: true, text: "TP1" }] }))!;
    expect(g.levelStyles).toEqual([{ coeff: 0.618, color: "#ff0000", width: 2, dashed: true, text: "TP1" }]);
  });

  it("没写覆盖时不塞空数组 —— 保持与旧数据往返相等", () => {
    expect(parseGrid(valid())!.levelStyles).toBeUndefined();
  });

  it("非十六进制颜色被丢弃,其余合法项保留", () => {
    const g = parseGrid(valid({ levelStyles: [{ coeff: 0.5, color: "red", width: 2 }] }))!;
    expect(g.levelStyles).toEqual([{ coeff: 0.5, width: 2 }]);
  });

  it("坏项单独丢弃,不影响同网格其它档位", () => {
    const g = parseGrid(valid({ levelStyles: [{ coeff: "x" }, { coeff: 0.382, color: "#00ff00" }, null, 42] }))!;
    expect(g.levelStyles).toEqual([{ coeff: 0.382, color: "#00ff00" }]);
  });

  it("重复 coeff 只保留第一个", () => {
    const g = parseGrid(valid({ levelStyles: [{ coeff: 0.5, color: "#ff0000" }, { coeff: 0.5, color: "#00ff00" }] }))!;
    expect(g.levelStyles).toEqual([{ coeff: 0.5, color: "#ff0000" }]);
  });

  it("越界线宽被夹回区间", () => {
    const g = parseGrid(valid({ levelStyles: [{ coeff: 0.5, width: 99 }] }))!;
    expect(g.levelStyles![0].width).toBe(6);
  });

  it("序列化深拷 levelStyles,不与源共享引用", () => {
    const src = parseGrid(valid({ levelStyles: [{ coeff: 0.5, color: "#ff0000" }] }))!;
    const out = serializeDrawings([src]);
    expect(out.grids[0].levelStyles).toEqual(src.levelStyles);
    expect(out.grids[0].levelStyles).not.toBe(src.levelStyles);
    expect(out.grids[0].levelStyles![0]).not.toBe(src.levelStyles![0]);
  });
});
