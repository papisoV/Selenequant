import type { TranslateParams } from "./types"

/**
 * 把 `{name}` 占位按 params 替换；未提供的键保留占位。
 *
 * 独立成文件(而非留在 `index.ts`)：`types.ts` 的 `identityTranslate` 要用它,
 * 而 `types.ts` 被 `lib/indicators.ts` / `lib/monitor-feed.ts` 这些纯模块引用 ——
 * 从 `index.ts` 取会把这些纯模块连上整份词表(en/zh-CN messages)。
 * 本文件只依赖 `types.ts` 的**类型**(编译期擦除),运行时不回指,无环。
 */
export function formatMessage(template: string, params?: TranslateParams): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = params[key]
    return value === undefined ? `{${key}}` : String(value)
  })
}
