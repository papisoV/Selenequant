import { describe, it, expect } from "vitest";
import {
  DEALING_RANGE_COLORS,
  DEFAULT_MAX_EVENTS,
  DEFAULT_MAX_SWINGS,
  EVENT_COLORS,
  KILLZONE_COLORS,
  SWING_COLORS,
  buildDealingRangeLines,
  buildKillzonePoints,
  buildStructureLines,
  buildSwingMarkers,
} from "@/lib/chart/ict-structure-overlay";
import { ictStructurePath } from "@/lib/api/ict-structure";
import type {
  DealingRange,
  KillzoneLayer,
  StructureEvent,
  StructureSwing,
} from "@/lib/api/ict-structure";

const T0 = 1_700_000_000;

function swing(over: Partial<StructureSwing> = {}): StructureSwing {
  return {
    index: 0,
    price: 100,
    kind: "high",
    tier: "st",
    time: T0,
    ...over,
  };
}

function event(over: Partial<StructureEvent> = {}): StructureEvent {
  return {
    kind: "BOS",
    direction: 1,
    level: 100,
    broken_bar_index: 1,
    swing_bar_index: 0,
    broken_time: T0 + 3600,
    swing_time: T0,
    displacement: { body_ratio: 0.6, has_fvg: false, fvg_range: 0 },
    ...over,
  };
}

describe("ictStructurePath", () => {
  it("带上四个主键 + limit + break_on", () => {
    const path = ictStructurePath({ symbol: "BTC/USDT", interval: "1h" });
    expect(path).toContain("/api/v1/chart/ict-structure-overlay?");
    expect(path).toContain("exchange=binance");
    expect(path).toContain("market_type=futures");
    expect(path).toContain("symbol=BTC%2FUSDT");
    expect(path).toContain("interval=1h");
    expect(path).toContain("limit=500");
    expect(path).toContain("break_on=close");
  });

  it("include_bars 仅在显式开启时出现", () => {
    expect(ictStructurePath({ symbol: "BTC/USDT", interval: "1h" })).not.toContain(
      "include_bars",
    );
    expect(
      ictStructurePath({ symbol: "BTC/USDT", interval: "1h", includeBars: true }),
    ).toContain("include_bars=true");
  });

  it("breakOn 与 limit 可覆盖", () => {
    const path = ictStructurePath({
      symbol: "ETH/USDT",
      interval: "4h",
      breakOn: "wick",
      limit: 200,
      marketType: "spot",
    });
    expect(path).toContain("break_on=wick");
    expect(path).toContain("limit=200");
    expect(path).toContain("market_type=spot");
  });
});

describe("buildSwingMarkers", () => {
  it("高点在 bar 上方画向下箭头，低点在下方画向上箭头", () => {
    const markers = buildSwingMarkers({
      swings: [
        swing({ index: 0, kind: "high", price: 110 }),
        swing({ index: 5, kind: "low", price: 90 }),
      ],
      events: [],
    });
    expect(markers).toHaveLength(2);
    expect(markers[0]).toMatchObject({ position: "aboveBar", shape: "arrowDown" });
    expect(markers[1]).toMatchObject({ position: "belowBar", shape: "arrowUp" });
  });

  it("按 bar 序号升序输出（lightweight-charts 要求有序）", () => {
    const markers = buildSwingMarkers({
      swings: [
        swing({ index: 9, time: T0 + 9 * 3600 }),
        swing({ index: 2, time: T0 + 2 * 3600 }),
        swing({ index: 5, time: T0 + 5 * 3600 }),
      ],
      events: [],
    });
    expect(markers.map((m) => m.time)).toEqual([
      T0 + 2 * 3600,
      T0 + 5 * 3600,
      T0 + 9 * 3600,
    ]);
  });

  it("层级越强颜色越重、尺寸越大", () => {
    const markers = buildSwingMarkers({
      swings: [
        swing({ index: 0, tier: "st" }),
        swing({ index: 1, tier: "it" }),
        swing({ index: 2, tier: "lt" }),
      ],
      events: [],
    });
    expect(markers[0].size).toBeLessThan(markers[1].size!);
    expect(markers[1].size).toBeLessThan(markers[2].size!);
    expect(markers[2].color).toBe(SWING_COLORS.high.lt);
  });

  it("只有中高层级带文字标签，STH 不标（避免图面噪音）", () => {
    const markers = buildSwingMarkers({
      swings: [
        swing({ index: 0, tier: "st" }),
        swing({ index: 1, tier: "it" }),
        swing({ index: 2, tier: "lt" }),
      ],
      events: [],
    });
    expect(markers[0].text).toBeUndefined();
    expect(markers[1].text).toBe("ITH");
    expect(markers[2].text).toBe("LTH");
  });

  it("超出上限时保留最近的 swing，丢弃远端", () => {
    const swings = Array.from({ length: 10 }, (_, i) =>
      swing({ index: i, time: T0 + i * 3600 }),
    );
    const markers = buildSwingMarkers({ swings, events: [], maxSwings: 3 });
    expect(markers).toHaveLength(3);
    expect(markers.map((m) => m.time)).toEqual([
      T0 + 7 * 3600,
      T0 + 8 * 3600,
      T0 + 9 * 3600,
    ]);
  });

  it("同 bar 竞争时高层级优先留下", () => {
    const markers = buildSwingMarkers({
      swings: [
        swing({ index: 0, tier: "st" }),
        swing({ index: 1, tier: "lt" }),
      ],
      events: [],
      maxSwings: 1,
    });
    expect(markers).toHaveLength(1);
    expect(markers[0].text).toBe("LTH");
  });

  it("过滤非有限价格（NaN 不能进 chart API）", () => {
    const markers = buildSwingMarkers({
      swings: [
        swing({ index: 0, price: Number.NaN }),
        swing({ index: 1, price: 100 }),
      ],
      events: [],
    });
    expect(markers).toHaveLength(1);
  });

  it("默认上限生效", () => {
    const swings = Array.from({ length: DEFAULT_MAX_SWINGS + 25 }, (_, i) =>
      swing({ index: i, time: T0 + i * 3600 }),
    );
    expect(buildSwingMarkers({ swings, events: [] })).toHaveLength(DEFAULT_MAX_SWINGS);
  });

  it("空输入 → 空输出，不抛", () => {
    expect(buildSwingMarkers({ swings: [], events: [] })).toEqual([]);
  });
});

describe("buildStructureLines", () => {
  it("BOS 与 MSS 用不同色系（趋势延续 vs 反转）", () => {
    const lines = buildStructureLines({
      swings: [],
      events: [
        event({ kind: "BOS", direction: 1, level: 110 }),
        event({ kind: "MSS", direction: -1, level: 90 }),
      ],
    });
    expect(lines).toHaveLength(2);
    const bos = lines.find((l) => l.title.startsWith("BOS"))!;
    const mss = lines.find((l) => l.title.startsWith("MSS"))!;
    expect(bos.color).toBe(EVENT_COLORS.BOS[1]);
    expect(mss.color).toBe(EVENT_COLORS.MSS[-1]);
  });

  it("线画在被破的 swing 价位上，不是破位根的收盘价", () => {
    const [line] = buildStructureLines({
      swings: [],
      events: [event({ level: 84_910.5, broken_bar_index: 49 })],
    });
    expect(line.price).toBe(84_910.5);
  });

  it("同价同向同类的重复破位只留最新一次", () => {
    const lines = buildStructureLines({
      swings: [],
      events: [
        event({ level: 100, broken_time: T0 + 3600 }),
        event({ level: 100, broken_time: T0 + 7200 }),
        event({ level: 100, broken_time: T0 + 10800 }),
      ],
    });
    expect(lines).toHaveLength(1);
    expect(lines[0].time).toBe(T0 + 10800);
  });

  it("同价位不同方向算两条（上破与下破是不同结构）", () => {
    const lines = buildStructureLines({
      swings: [],
      events: [
        event({ level: 100, direction: 1 }),
        event({ level: 100, direction: -1 }),
      ],
    });
    expect(lines).toHaveLength(2);
  });

  it("超出上限时保留最近的破位", () => {
    const events = Array.from({ length: 10 }, (_, i) =>
      event({ level: 100 + i, broken_time: T0 + i * 3600 }),
    );
    const lines = buildStructureLines({ swings: [], events, maxEvents: 2 });
    expect(lines).toHaveLength(2);
    expect(lines.map((l) => l.price).sort()).toEqual([108, 109]);
  });

  it("过滤非有限价位", () => {
    const lines = buildStructureLines({
      swings: [],
      events: [event({ level: Number.NaN }), event({ level: 120, broken_time: T0 + 9999 })],
    });
    expect(lines).toHaveLength(1);
  });

  it("默认上限生效", () => {
    const events = Array.from({ length: DEFAULT_MAX_EVENTS + 12 }, (_, i) =>
      event({ level: 100 + i, broken_time: T0 + i * 3600 }),
    );
    expect(buildStructureLines({ swings: [], events })).toHaveLength(DEFAULT_MAX_EVENTS);
  });

  it("空输入 → 空输出，不抛", () => {
    expect(buildStructureLines({ swings: [], events: [] })).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Killzone (timing layer)
// ---------------------------------------------------------------------------

function killzoneLayer(over: Partial<KillzoneLayer> = {}): KillzoneLayer {
  return {
    sessions: ["london", "new_york"],
    tz_offset_hours: -5,
    windows: [
      { name: "london", start_hour: 2, end_hour: 5 },
      { name: "new_york", start_hour: 7, end_hour: 10 },
    ],
    clock_source: "timestamp",
    bars: [
      { t: T0, v: 0 },
      { t: T0 + 3600, v: 1 },
      { t: T0 + 7200, v: 1 },
      { t: T0 + 10800, v: 0 },
    ],
    inside_count: 2,
    ...over,
  };
}

describe("buildKillzonePoints", () => {
  it("把 0/1 membership 映射成带色的直方点", () => {
    const pts = buildKillzonePoints(killzoneLayer());
    expect(pts).toHaveLength(4);
    expect(pts.map((p) => p.value)).toEqual([0, 1, 1, 0]);
    expect(pts[1].color).toBe(KILLZONE_COLORS.inside);
    expect(pts[0].color).toBe(KILLZONE_COLORS.outside);
    expect(pts[0].time).toBe(T0);
  });

  it("clock_source 为 null（不可读时钟）→ 不画任何点", () => {
    // 空 bars 与 "从不在时段内" 是两回事：前者是无时钟，后者是事实陈述。
    // 画成一片 0 会把"没读到时"谎报成"全程在时段外"。
    expect(buildKillzonePoints(killzoneLayer({ clock_source: null }))).toEqual([]);
  });

  it("无图层 / undefined → 空输出，不抛", () => {
    expect(buildKillzonePoints(null)).toEqual([]);
    expect(buildKillzonePoints(undefined)).toEqual([]);
  });

  it("丢弃既非 0 也非 1 的脏值，不猜", () => {
    const pts = buildKillzonePoints(
      killzoneLayer({
        bars: [
          { t: T0, v: 1 },
          { t: T0 + 3600, v: 2 as unknown as 0 | 1 },
          { t: T0 + 7200, v: Number.NaN as unknown as 0 | 1 },
          { t: T0 + 10800, v: 0 },
        ],
      }),
    );
    expect(pts.map((p) => p.value)).toEqual([1, 0]);
  });

  it("丢弃非有限时间戳", () => {
    const pts = buildKillzonePoints(
      killzoneLayer({
        bars: [
          { t: Number.NaN, v: 1 },
          { t: T0, v: 1 },
        ],
      }),
    );
    expect(pts).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Dealing range (positioning layer)
// ---------------------------------------------------------------------------

function dealingRange(over: Partial<DealingRange> = {}): DealingRange {
  return {
    bar_index: 42,
    time: T0 + 42 * 3600,
    low: 100,
    high: 130,
    equilibrium: 115,
    premium_from: 120,
    discount_to: 110,
    close: 105,
    position: 0.1667,
    zone: "discount",
    ...over,
  };
}

describe("buildDealingRangeLines", () => {
  it("产出溢价/均衡/折价三条线，价格与配色对应", () => {
    const lines = buildDealingRangeLines(dealingRange());
    expect(lines).toHaveLength(3);
    expect(lines.map((l) => l.price)).toEqual([120, 115, 110]);
    expect(lines[0].color).toBe(DEALING_RANGE_COLORS.premium);
    expect(lines[1].color).toBe(DEALING_RANGE_COLORS.equilibrium);
    expect(lines[2].color).toBe(DEALING_RANGE_COLORS.discount);
  });

  it("标题带上当前分区，线本身语义不随分区变", () => {
    const discount = buildDealingRangeLines(dealingRange({ zone: "discount" }));
    const premium = buildDealingRangeLines(dealingRange({ zone: "premium" }));
    expect(discount[2].title).toContain("discount");
    expect(premium[2].title).toContain("premium");
    // 分区变了，三条线的价位不变——分区是读数，不是区间。
    expect(premium.map((l) => l.price)).toEqual(discount.map((l) => l.price));
  });

  it("zone 为 null 时标题不带分区后缀", () => {
    const lines = buildDealingRangeLines(dealingRange({ zone: null }));
    expect(lines).toHaveLength(3);
    expect(lines[0].title).not.toContain("null");
  });

  it("退化区间（low >= high）→ 不画", () => {
    expect(buildDealingRangeLines(dealingRange({ low: 130, high: 100 }))).toEqual([]);
    expect(buildDealingRangeLines(dealingRange({ low: 100, high: 100 }))).toEqual([]);
  });

  it("任一价位非有限 → 整层不画，不画半套", () => {
    expect(buildDealingRangeLines(dealingRange({ equilibrium: Number.NaN }))).toEqual([]);
    expect(buildDealingRangeLines(dealingRange({ premium_from: Number.POSITIVE_INFINITY }))).toEqual([]);
  });

  it("null / undefined → 空输出", () => {
    expect(buildDealingRangeLines(null)).toEqual([]);
    expect(buildDealingRangeLines(undefined)).toEqual([]);
  });
});

describe("ictStructurePath — ICT 图层参数", () => {
  it("默认不附带图层参数（保持 URL 稳定，SWR key 不变）", () => {
    const path = ictStructurePath({ symbol: "BTC/USDT", interval: "1h" });
    expect(path).not.toContain("sessions=");
    expect(path).not.toContain("tz_offset_hours=");
    expect(path).not.toContain("pd_period=");
  });

  it("显式覆盖时才附上", () => {
    const path = ictStructurePath({
      symbol: "BTC/USDT",
      interval: "1h",
      sessions: "london",
      tzOffsetHours: 0,
      pdPeriod: 20,
      premiumThreshold: 0.8,
      discountThreshold: 0.2,
    });
    expect(path).toContain("sessions=london");
    expect(path).toContain("tz_offset_hours=0");
    expect(path).toContain("pd_period=20");
    expect(path).toContain("premium_threshold=0.8");
    expect(path).toContain("discount_threshold=0.2");
  });

  it("tzOffsetHours=0 必须发出去，不能被 falsy 判空吞掉", () => {
    const path = ictStructurePath({ symbol: "BTC/USDT", interval: "1h", tzOffsetHours: 0 });
    expect(path).toContain("tz_offset_hours=0");
  });
});

// ---------------------------------------------------------------------------
// 显示参数：档位 / 方向 / 类型过滤
// ---------------------------------------------------------------------------

describe("buildSwingMarkers — 档位与方向过滤", () => {
  const swings = [
    swing({ index: 1, tier: "st", kind: "high" }),
    swing({ index: 2, tier: "it", kind: "high" }),
    swing({ index: 3, tier: "lt", kind: "low" }),
  ];

  it("关掉 ST 档后不再出 ST 标记", () => {
    const markers = buildSwingMarkers({
      swings,
      events: [],
      tiers: { st: false, it: true, lt: true },
    });
    expect(markers).toHaveLength(2);
    expect(markers.every((m) => m.color !== SWING_COLORS.high.st)).toBe(true);
  });

  it("只看高点时低点标记被剔除", () => {
    const markers = buildSwingMarkers({ swings, events: [], swingSide: "high" });
    expect(markers).toHaveLength(2);
    expect(markers.every((m) => m.position === "aboveBar")).toBe(true);
  });

  it("过滤必须先于截断：档位关掉后上限留给真正要画的点", () => {
    // 30 个 ST + 5 个 LT，上限 5；若先截断再过滤会一个 LT 都不剩。
    const many = [
      ...Array.from({ length: 30 }, (_, i) => swing({ index: i, tier: "st" })),
      ...Array.from({ length: 5 }, (_, i) => swing({ index: 100 + i, tier: "lt" })),
    ];
    const markers = buildSwingMarkers({
      swings: many,
      events: [],
      maxSwings: 5,
      tiers: { st: false, it: false, lt: true },
    });
    expect(markers).toHaveLength(5);
  });

  it("不传 tiers/side 时行为与改动前一致（向后兼容）", () => {
    const markers = buildSwingMarkers({ swings, events: [] });
    expect(markers).toHaveLength(3);
  });
});

describe("buildStructureLines — 类型与方向过滤", () => {
  const events = [
    event({ kind: "BOS", direction: 1, level: 100, broken_time: T0 + 3 }),
    event({ kind: "BOS", direction: -1, level: 90, broken_time: T0 + 2 }),
    event({ kind: "MSS", direction: 1, level: 80, broken_time: T0 + 1 }),
  ];

  it("只留 BOS", () => {
    const lines = buildStructureLines({
      swings: [],
      events,
      eventKinds: { BOS: true, MSS: false },
    });
    expect(lines).toHaveLength(2);
    expect(lines.every((l) => l.title.startsWith("BOS"))).toBe(true);
  });

  it("只看向上破位", () => {
    const lines = buildStructureLines({ swings: [], events, eventSide: "up" });
    expect(lines).toHaveLength(2);
    expect(lines.every((l) => l.title.endsWith("↑"))).toBe(true);
  });

  it("过滤先于截断：关掉 MSS 后 BOS 不会被上限挤掉", () => {
    const lines = buildStructureLines({
      swings: [],
      events,
      maxEvents: 2,
      eventKinds: { BOS: true, MSS: false },
    });
    expect(lines).toHaveLength(2);
    expect(lines.every((l) => l.title.startsWith("BOS"))).toBe(true);
  });
});

describe("buildDealingRangeLines — 单条线开关", () => {
  it("关掉均衡线后只出两条", () => {
    const lines = buildDealingRangeLines(dealingRange(), {
      premium: true,
      equilibrium: false,
      discount: true,
    });
    expect(lines).toHaveLength(2);
    expect(lines.some((l) => l.title.startsWith("均衡"))).toBe(false);
  });

  it("三条全关 → 空;不传 lines → 三条全出（向后兼容）", () => {
    expect(
      buildDealingRangeLines(dealingRange(), {
        premium: false,
        equilibrium: false,
        discount: false,
      }),
    ).toEqual([]);
    expect(buildDealingRangeLines(dealingRange())).toHaveLength(3);
  });
});
