/**
 * 策略面板共用的小工具(纯函数)。
 */

/** 取某分类下的条目; category 为 "all" 时返回全部。 */
export function filterByCategory<T extends { category: string }>(
  items: T[],
  category: string,
): T[] {
  if (category === "all") return items
  return items.filter((item) => item.category === category)
}

/** 统计各分类的条目数。 */
export function countByCategory<T extends { category: string }>(
  items: T[],
): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const item of items) {
    counts[item.category] = (counts[item.category] ?? 0) + 1
  }
  return counts
}

/** 是否为普通对象记录(非 null、非数组)。 */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}
