import { enMessages } from "./messages/en"
import { zhCNMessages } from "./messages/zh-CN"
import type { Locale, MessageKey, TranslateParams } from "./types"
import { formatMessage } from "./format"

// 占位符替换独立成文件(纯模块经 types.ts 取用,不能连上整份词表);
// 此处再导出,保持 `@/lib/i18n` 既有导入面不变。
export { formatMessage }

export const LOCALE_STORAGE_KEY = "selenespace-locale"

/**
 * 默认语言 —— **zh-CN**(2026-10-16 全站双语改造时从 "en" 翻转)。
 *
 * 项目母语用户为中文(界面文案原本全硬编码中文,data/page-manifest.ts 的
 * 184 条页面标题即中文),故首访默认中文;英文由切换器/已存 localStorage 驱动。
 *
 * 连带效应:globals.css 的 `:root[data-locale="zh-CN"]` 块(**红涨绿跌**,
 * A 股惯例)从"默认不生效"变为"默认生效";en 用户看绿涨红跌。
 */
export const DEFAULT_LOCALE: Locale = "zh-CN"

// 已知 locale 及其词表(en 兼作回落源)。
const LOCALES = ["en", "zh-CN"] as const

export const messages = {
  en: enMessages,
  "zh-CN": zhCNMessages,
} as const

/** 类型守卫: 是否为本应用支持的 locale。 */
export function isLocale(value: string): value is Locale {
  return (LOCALES as readonly string[]).includes(value)
}

/**
 * 同步读取已持久化的 locale(SSR / 无 window 时回落 DEFAULT_LOCALE)。
 * 供无法使用 React context 的场景使用 —— 典型是 `app/global-error.tsx`:
 * 它自带 <html>/<body>，替换根布局，因此不在 I18nProvider 内。
 */
export function readStoredLocale(): Locale {
  if (typeof window === "undefined") return DEFAULT_LOCALE
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY)
    if (stored && isLocale(stored)) return stored
  } catch {
    // localStorage 不可用(隐私模式等) → 回落默认
  }
  return DEFAULT_LOCALE
}

/** 取某 key 的模板(缺失时回落默认 locale, 再回落 key 本身)并按 params 格式化。 */
export function resolveMessage(
  locale: Locale,
  key: MessageKey,
  params?: TranslateParams
): string {
  const table = messages[locale]
  const fallback = messages[DEFAULT_LOCALE]
  const template = table[key] ?? fallback[key] ?? key
  return formatMessage(template, params)
}
