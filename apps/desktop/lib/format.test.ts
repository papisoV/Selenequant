import { describe, it, expect } from "vitest";
import { formatTime, formatDateForInput, formatDateForConfig } from "./format";

// 行为锁: 日期/时间格式化契约。cleanroom 重写必须保持; 撤实现 → 必红。

describe("formatTime", () => {
  it("时间戳 → MM/DD HH:MM(零填充)", () => {
    // 2024-01-05 09:07(本地时区, 用构造保证)
    const d = new Date(2024, 0, 5, 9, 7);
    expect(formatTime(d.getTime())).toBe("01/05 09:07");
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
