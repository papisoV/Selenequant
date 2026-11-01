import { describe, expect, it } from "vitest"

import {
  DRAWING_ID_PREFIX,
  INDICATOR_ID_PREFIX,
  drawingIdOf,
  drawingObjectId,
  filterGridsByScope,
  indicatorObjectId,
  isIndicatorObjectId,
} from "./chart-objects"
import type { FibGrid } from "./fib-object"

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
  }
}

describe("对象树 id 空间", () => {
  it("指标与绘图的 id 前缀不同,同一原始串不会互撞", () => {
    // 指标 key 叫 "fib" 也不会与绘图 id "fib" 撞 —— 前缀隔离
    expect(indicatorObjectId("fib")).not.toBe(drawingObjectId("fib"))
    expect(indicatorObjectId("ma")).toBe(INDICATOR_ID_PREFIX + "ma")
    expect(drawingObjectId("fib3")).toBe(DRAWING_ID_PREFIX + "fib3")
  })

  it("isIndicatorObjectId 只认指标前缀", () => {
    expect(isIndicatorObjectId(indicatorObjectId("ma"))).toBe(true)
    expect(isIndicatorObjectId(drawingObjectId("fib1"))).toBe(false)
    expect(isIndicatorObjectId("fib1")).toBe(false)
  })

  it("drawingIdOf 往返一致", () => {
    const objectId = drawingObjectId("fib7")
    expect(drawingIdOf(objectId)).toBe("fib7")
  })

  it("drawingIdOf 对指标 id / 裸 id / 空前缀都返回 null(不让调用方拿空串去查)", () => {
    expect(drawingIdOf(indicatorObjectId("ma"))).toBeNull()
    expect(drawingIdOf("fib1")).toBeNull()
    expect(drawingIdOf(DRAWING_ID_PREFIX)).toBeNull()
    expect(drawingIdOf("")).toBeNull()
  })
})

describe("filterGridsByScope", () => {
  const grids = [grid({ id: "a", symbol: "BTC" }), grid({ id: "b", symbol: "ETH" }), grid({ id: "c", symbol: "BTC" })]

  it("current 只留当前标的", () => {
    const out = filterGridsByScope(grids, "current", "BTC")
    expect(out.map((g) => g.id)).toEqual(["a", "c"])
  })

  it("all 保留全部标的", () => {
    const out = filterGridsByScope(grids, "all", "BTC")
    expect(out.map((g) => g.id)).toEqual(["a", "b", "c"])
  })

  it("hidden 的对象仍要出现在树里 —— 否则无法重新显示", () => {
    const hidden = [grid({ id: "h", visible: false, symbol: "BTC" })]
    expect(filterGridsByScope(hidden, "current", "BTC").map((g) => g.id)).toEqual(["h"])
  })

  it("不修改入参(纯函数)", () => {
    const before = JSON.stringify(grids)
    filterGridsByScope(grids, "all", "BTC")
    filterGridsByScope(grids, "current", "BTC")
    expect(JSON.stringify(grids)).toBe(before)
  })

  it("当前标的没有对象时返回空数组,不是 undefined", () => {
    expect(filterGridsByScope(grids, "current", "SOL")).toEqual([])
  })
})
