import { describe, it, expect } from "vitest";
import {
  DEFAULT_ICT_DISPLAY,
  ICT_DISPLAY_KEY,
  parseIctDisplay,
  parseIctDisplayJson,
  serializeIctDisplay,
  sessionsParam,
} from "@/lib/chart/ict-display";

describe("parseIctDisplay — 坏字段只回退那一个", () => {
  it("空输入 → 默认值（深拷贝，不与常量共享引用）", () => {
    const a = parseIctDisplay(null);
    expect(a).toEqual(DEFAULT_ICT_DISPLAY);
    a.swings.tiers.st = true;
    expect(DEFAULT_ICT_DISPLAY.swings.tiers.st).toBe(false);
  });

  it("越界数值收敛到后端约束内，而不是丢整包", () => {
    const parsed = parseIctDisplay({
      swings: { maxCount: 9999 },
      dealingRange: { period: 1 },
    });
    expect(parsed.swings.maxCount).toBe(200);
    expect(parsed.dealingRange.period).toBe(2);
    // 同包里的合法字段不受牵连
    expect(parsed.events.breakOn).toBe("close");
  });

  it("非数值字符串回落默认，不产出 NaN", () => {
    const parsed = parseIctDisplay({ killzone: { tzOffsetHours: "abc" } });
    expect(parsed.killzone.tzOffsetHours).toBe(DEFAULT_ICT_DISPLAY.killzone.tzOffsetHours);
  });

  it("三个时段全关 → 回落默认（后端对空集会 400）", () => {
    const parsed = parseIctDisplay({
      killzone: { sessions: { london: false, new_york: false, asia: false } },
    });
    expect(parsed.killzone.sessions).toEqual(DEFAULT_ICT_DISPLAY.killzone.sessions);
  });

  it("溢价/折价阈值顺序反了 → 对调而不是丢配置", () => {
    const parsed = parseIctDisplay({
      dealingRange: { premiumThreshold: 0.2, discountThreshold: 0.8 },
    });
    expect(parsed.dealingRange.premiumThreshold).toBeGreaterThan(
      parsed.dealingRange.discountThreshold,
    );
    expect(parsed.dealingRange.premiumThreshold).toBeCloseTo(0.8);
    expect(parsed.dealingRange.discountThreshold).toBeCloseTo(0.2);
  });
});

describe("parseIctDisplayJson", () => {
  it("坏 JSON → 默认值，不抛", () => {
    expect(parseIctDisplayJson("{not json")).toEqual(DEFAULT_ICT_DISPLAY);
    expect(parseIctDisplayJson(null)).toEqual(DEFAULT_ICT_DISPLAY);
  });

  it("序列化后可原样读回", () => {
    const custom = parseIctDisplay({
      swings: { maxCount: 77, side: "low" },
      killzone: { sessions: { london: false, new_york: true, asia: true }, opacity: 0.3 },
    });
    expect(parseIctDisplayJson(serializeIctDisplay(custom))).toEqual(custom);
  });
});

describe("sessionsParam", () => {
  it("保持稳定顺序，便于 SWR key 复用", () => {
    expect(sessionsParam({ london: true, new_york: true, asia: false })).toBe(
      "london,new_york",
    );
    expect(sessionsParam({ london: false, new_york: true, asia: true })).toBe(
      "new_york,asia",
    );
    expect(sessionsParam({ london: true, new_york: true, asia: true })).toBe(
      "london,new_york,asia",
    );
  });

  it("全 false 也返回默认串（防御性，store 会先拒绝这种写入）", () => {
    expect(sessionsParam({ london: false, new_york: false, asia: false })).toBe(
      "london,new_york",
    );
  });
});

describe("ICT_DISPLAY_KEY", () => {
  it("与绘图/外观的 key 互不冲突", () => {
    expect(ICT_DISPLAY_KEY).not.toBe("selenespace-fib-drawings");
    expect(ICT_DISPLAY_KEY).not.toBe("selenespace-drawing-prefs");
    expect(ICT_DISPLAY_KEY).not.toBe("selenespace-chart-appearance");
  });
});
