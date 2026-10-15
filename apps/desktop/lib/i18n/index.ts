import { enMessages } from "./messages/en"
import { zhCNMessages } from "./messages/zh-CN"
import type { Locale, MessageKey, TranslateParams } from "./types"

export const LOCALE_STORAGE_KEY = "selenespace-locale"
export const DEFAULT_LOCALE: Locale = "en"

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

/** 把 {name} 占位按 params 替换; 未提供的键保留占位。 */
export function formatMessage(template: string, params?: TranslateParams): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = params[key]
    return value === undefined ? `{${key}}` : String(value)
  })
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
