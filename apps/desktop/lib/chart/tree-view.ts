// 对象树的**视图计算** —— 搜索过滤与分组级三态聚合。纯函数,jsdom 可测。
//
// 为什么单独成文件而不是塞进组件:这两件事都是"给一堆行算出一个显示结果",
// 与 React 无关,也不需要 localStorage。放组件里就只能靠渲染结果间接断言,
// 边界(空查询、零成员分组、名字命中但成员不命中)很难钉死。

import type { FibGrid } from "./fib-object";

/** 分组内某个布尔属性的汇总态。 */
export type TriState = "all" | "none" | "mixed";

/** 分组头渲染所需的最小形状(与 store 的 TreeRow 结构兼容,但不耦合)。 */
export interface RowLike {
  group: { name: string } | null;
  grids: readonly FibGrid[];
}

/**
 * 未命名对象的显示名。
 *
 * 同时收中英两种写法:界面在英文模式下显示 `Fibonacci grid`,搜索框却是
 * 同一个 —— 只认中文会让英文用户搜不到自己那批未命名的网格。
 */
const FALLBACK_NAMES = ["斐波那契网格", "fibonacci grid"];

/** 归一化查询串。空串/纯空白 → 空串(调用方据此判断"未搜索")。 */
export function normalizeQuery(query: string): string {
  return query.trim().toLowerCase();
}

/**
 * 单个对象是否命中查询。
 *
 * 空查询恒命中 —— 调用方不必在循环外特判。
 */
export function gridMatches(grid: FibGrid, query: string): boolean {
  const q = normalizeQuery(query);
  if (q === "") return true;

  const named = grid.label.trim().toLowerCase();
  const haystack = named === "" ? FALLBACK_NAMES.join(" ") : named;
  if (haystack.includes(q)) return true;
  if (grid.symbol.toLowerCase().includes(q)) return true;
  return grid.timeframe.toLowerCase().includes(q);
}

/**
 * 按查询过滤分组行。
 *
 * 三种命中方式:
 *   · 分组名命中 → 整组保留(成员全显示,便于"搜到组就看整组");
 *   · 成员命中 → 只留命中的成员;
 *   · 都不命中 → 整行丢弃(搜索时不留空组,否则结果里全是空壳)。
 *
 * 空查询原样返回(新数组,不改入参)。返回值保持入参的泛型,调用方拿回的
 * 仍是自己的行类型。
 */
export function filterRowsByQuery<T extends RowLike>(rows: readonly T[], query: string): T[] {
  const q = normalizeQuery(query);
  if (q === "") return [...rows];

  const out: T[] = [];
  for (const row of rows) {
    const groupHit = row.group !== null && row.group.name.toLowerCase().includes(q);
    const grids = groupHit ? [...row.grids] : row.grids.filter((g) => gridMatches(g, q));
    if (grids.length > 0 || groupHit) out.push({ ...row, grids });
  }
  return out;
}

/**
 * 汇总一组布尔值。空数组 → `"none"`(没有成员,眼睛显示为"未显示"、锁显示为"未锁")。
 *
 * 用来给分组头画 ◉ / ◐ / ○ 与 ▣ / ◢ / ▢:用户一眼看出整组是不是统一状态,
 * 而不是靠数行。
 */
export function triState(values: readonly boolean[]): TriState {
  if (values.length === 0) return "none";
  const first = values[0];
  return values.every((v) => v === first) ? (first ? "all" : "none") : "mixed";
}

/** 分组内可见性的汇总。 */
export function groupVisibleState(grids: readonly FibGrid[]): TriState {
  return triState(grids.map((g) => g.visible));
}

/** 分组内锁定态的汇总。 */
export function groupLockedState(grids: readonly FibGrid[]): TriState {
  return triState(grids.map((g) => g.locked));
}

/**
 * 点分组头的眼睛/锁时该设成什么。
 *
 * 只有"全部已开"才关,其余(全关 / 混合)一律开 —— 与单行的做法一致:
 * 混合状态下用户的意图总是"让它们统一到我要的状态",而不是各翻各的。
 */
export function nextGroupFlag(state: TriState): boolean {
  return state !== "all";
}
