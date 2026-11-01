// 对象树的**视图层归一** —— 纯函数(零 React / 零图表库依赖,jsdom 可测)。
//
// 为什么需要这一层:对象树里要同时出现两种东西 ——
//   指标:页面级布尔开关(`tools`),作用于全部图格,没有"实例"概念;
//   绘图:按标的的独立对象(`FibGrid`),每个都有自己的锚点 / 档位 / 锁定态。
// 它们的存储形状完全不同,但**在树里是同一类东西**:有名字、能隐藏、能删除。
//
// 本模块只做两件事:
//   1. 给两种对象一个**互不冲突的 id 空间**(第 3 步多选要按 id 批量操作)。
//   2. 按"当前图 / 全部标的"过滤绘图。
//
// 刻意**不做**:把两者塞进同一个扁平数组。指标没有 anchor/prices,
// 强行归一会得到一堆恒为 null 的字段,渲染端仍要按 kind 分流 ——
// 那不如就让树按两个区渲染,只共享 id 规则。

import type { FibGrid } from "./fib-object"

/** 指标在对象树里的 id 前缀。用 `:` 分隔,避免与 `fib`/`fib0` 这类自增 id 撞。 */
export const INDICATOR_ID_PREFIX = "ind:"
/** 绘图在对象树里的 id 前缀。 */
export const DRAWING_ID_PREFIX = "drawing:"

/** 对象树范围:只看当前图,还是列出全部标的。 */
export type TreeScope = "current" | "all"

/** `ma` → `ind:ma` */
export function indicatorObjectId(key: string): string {
  return INDICATOR_ID_PREFIX + key
}

/** `fib3` → `drawing:fib3` */
export function drawingObjectId(id: string): string {
  return DRAWING_ID_PREFIX + id
}

export function isIndicatorObjectId(objectId: string): boolean {
  return objectId.startsWith(INDICATOR_ID_PREFIX)
}

/**
 * 从对象树 id 取回 FibGrid 的主键。
 *
 * 不是绘图 id(指标 id、或根本不合规则)时返回 null —— 调用方据此跳过,
 * 而不是拿到一个 `"drawing:"` 之类的空串去 store 里查。
 */
export function drawingIdOf(objectId: string): string | null {
  if (!objectId.startsWith(DRAWING_ID_PREFIX)) return null
  const id = objectId.slice(DRAWING_ID_PREFIX.length)
  return id.length > 0 ? id : null
}

/**
 * 按范围过滤绘图。
 *
 * `current` 只留当前标的 —— 这是默认值,贴 TradingView 的对象树直觉
 * (树是当前图表私有)。`all` 保留全部,用于跨标的批量清理:9 图格
 * 同屏时误画的线不必切 9 次图才能删。
 *
 * 不过滤 `visible`:被隐藏的对象**仍要出现在树里**,否则用户无法把它重新显示。
 */
export function filterGridsByScope(
  grids: readonly FibGrid[],
  scope: TreeScope,
  symbol: string,
): FibGrid[] {
  if (scope === "all") return [...grids]
  return grids.filter((g) => g.symbol === symbol)
}
