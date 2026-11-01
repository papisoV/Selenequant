import { describe, it, expect } from "vitest";
import { filterByCategory, countByCategory, isRecord } from "./strategy-utils";

// 行为锁: 策略面板小工具契约。cleanroom 重写必须保持; 撤实现 → 必红。

const items = [
  { category: "trend", id: "a" },
  { category: "trend", id: "b" },
  { category: "mean", id: "c" },
];

describe("filterByCategory", () => {
  it("all → 全部(同一引用)", () => expect(filterByCategory(items, "all")).toBe(items));
  it("指定分类 → 过滤", () => {
    expect(filterByCategory(items, "trend").map((i) => i.id)).toEqual(["a", "b"]);
    expect(filterByCategory(items, "mean").map((i) => i.id)).toEqual(["c"]);
  });
  it("无命中 → 空", () => expect(filterByCategory(items, "x")).toEqual([]));
});

describe("countByCategory", () => {
  it("统计各分类数量", () => {
    expect(countByCategory(items)).toEqual({ trend: 2, mean: 1 });
  });
  it("空输入 → 空对象", () => expect(countByCategory([])).toEqual({}));
});

describe("isRecord", () => {
  it("普通对象 → true", () => expect(isRecord({})).toBe(true));
  it("null/数组/标量 → false", () => {
    expect(isRecord(null)).toBe(false);
    expect(isRecord([])).toBe(false);
    expect(isRecord("x")).toBe(false);
    expect(isRecord(3)).toBe(false);
  });
});
