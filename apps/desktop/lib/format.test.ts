import { describe, it, expect } from "vitest";
import {
  formatTime,
  formatDateForInput,
  formatDateForConfig,
  formatMonthDay,
  formatDateTimeShort,
  fractionToPercent,
  formatFractionAsPercent,
  percentToFraction,
} from "./format";

// 行为锁: 日期/时间格式化契约。cleanroom 重写必须保持; 撤实现 → 必红。

describe("formatTime", () => {
  it("时间戳 → MM/DD HH:MM(零填充)", () => {
    // 2024-01-05 09:07(本地时区, 用构造保证)
    const d = new Date(2024, 0, 5, 9, 7);
    expect(formatTime(d.getTime())).toBe("01/05 09:07");
  });
});

describe("formatMonthDay", () => {
  it("从 ISO 串上取月日,不做时区换算", () => {
    // 取串:23:59 的 UTC 时间不能因为本地时区变成第二天
    expect(formatMonthDay("2026-10-05T23:59:59.000Z")).toBe("10-05");
    expect(formatMonthDay("2026-01-02T00:00:00")).toBe("01-02");
  });
});

describe("formatDateTimeShort", () => {
  it("本地 MM-DD HH:MM(零填充)", () => {
    const d = new Date(2024, 0, 5, 9, 7);
    expect(formatDateTimeShort(d.toISOString())).toBe("01-05 09:07");
  });
  it("解析失败原样返回,不渲染 Invalid Date", () => {
    expect(formatDateTimeShort("not-a-date")).toBe("not-a-date");
  });
});

describe("formatDateForInput", () => {
  it("YYYYMMDD → YYYY-MM-DD", () => {
    expect(formatDateForInput("20240131")).toBe("2024-01-31");
  });
  it("长度非 8 → 原样", () => {
    expect(formatDateForInput("2024-01-31")).toBe("2024-01-31");
    expect(formatDateForInput("abc")).toBe("abc");
    expect(formatDateForInput("")).toBe("");
  });
});

describe("formatDateForConfig", () => {
  it("YYYY-MM-DD → YYYYMMDD", () => {
    expect(formatDateForConfig("2024-01-31")).toBe("20240131");
  });
  it("无连字符 → 原样", () => {
    expect(formatDateForConfig("20240131")).toBe("20240131");
    expect(formatDateForConfig("")).toBe("");
  });
  it("往返幂等", () => {
    expect(formatDateForInput(formatDateForConfig("2024-06-15"))).toBe("2024-06-15");
  });
});

// 四档止盈面板:存储是小数比例(0.035),界面是百分比(3.5%)。
// 下面是"显示 / 滑块 / 数字框"三者共用的换算契约 —— 三者不一致时
// 同一行会同时出现 0.035 和 3.5%。

describe("fractionToPercent", () => {
  it("小数比例 → 百分比数字", () => {
    expect(fractionToPercent(0.035)).toBe(3.5);
    expect(fractionToPercent(0.5)).toBe(50);
    expect(fractionToPercent(0.0025)).toBe(0.25);
  });
  it("默认两位小数,尾零不保留", () => {
    expect(fractionToPercent(0.1)).toBe(10);
    expect(fractionToPercent(0.0205)).toBe(2.05);
  });
  it("滑块步长不被四舍五入吃掉", () => {
    // T1 阈值 step=0.0025(0.25%),界面若只留两位会把它抹成 0.25——
    // 恰好保留,但 0.0075 → 0.75、0.0125 → 1.25 也必须原样。
    expect(fractionToPercent(0.0075, 4)).toBe(0.75);
    expect(fractionToPercent(0.0125, 4)).toBe(1.25);
  });
});

describe("formatFractionAsPercent", () => {
  it("小数比例 → 展示串", () => {
    expect(formatFractionAsPercent(0.035)).toBe("3.5%");
    expect(formatFractionAsPercent(0.5)).toBe("50%");
    expect(formatFractionAsPercent(0.0025)).toBe("0.25%");
  });
  it("非有限值给破折号而不是 NaN%", () => {
    expect(formatFractionAsPercent(NaN)).toBe("—");
    expect(formatFractionAsPercent(Infinity)).toBe("—");
  });
  it("withSign 只给正数加号", () => {
    expect(formatFractionAsPercent(0.035, { withSign: true })).toBe("+3.5%");
    expect(formatFractionAsPercent(0, { withSign: true })).toBe("0%");
  });
});

describe("percentToFraction", () => {
  it("百分比数字 → 小数比例", () => {
    expect(percentToFraction(3.5)).toBe(0.035);
    expect(percentToFraction(50)).toBe(0.5);
    expect(percentToFraction(0.25)).toBe(0.0025);
  });
  it("与 fractionToPercent 往返幂等(存储值不被逐次拖动漂移)", () => {
    for (const stored of [0.005, 0.015, 0.02, 0.035, 0.06, 0.1, 0.2, 0.5, 0.9]) {
      expect(percentToFraction(fractionToPercent(stored))).toBeCloseTo(stored, 10);
    }
  });
});
