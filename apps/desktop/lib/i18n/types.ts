import type { enMessages } from "./messages/en"
import { formatMessage } from "./format"

export type Locale = "en" | "zh-CN"

export type MessageKey = keyof typeof enMessages
export type MessageMap = Record<MessageKey, string>
export type TranslateParams = Record<string, string | number>

/**
 * 源串翻译函数 —— 收中文源串,返回当前 locale 的文案。
 *
 * 由纯函数层(lib/monitor-feed.ts、lib/indicators.ts)声明为可选参数收:
 * 它们产出的是**拼好的动态串**(如 `涨停 600519 茅台 @1786.50`),
 * 整串随数据变化,源串查表命中不了,只能把可译片段在拼接时就地译好。
 * 组件层由 `useUiText()` 提供;纯函数测试不传,回落中文原文。
 *
 * `params` 可选:纯函数拼的串里若带数据(如 `{n} 项`),译文需要按占位符回填,
 * 单收源串的旧签名表达不了。只写 `(s) => …` 的实现照旧可赋值(少参函数兼容)。
 */
export type Translate = (source: string, params?: TranslateParams) => string

/**
 * 待译消息 —— state 里存**源串(+ 参数)**,渲染点再用当前 locale 的 tr()。
 *
 * 为什么不能直接把 `tr(...)` 的结果存进 state:`tr()` 在 setter 调用那一刻
 * 求值一次,state 不会因 locale 变化而重算 —— 用户切语言后,错误/提示会
 * 停在旧语言(真渲染扫描在 /research/alpha_mine 实测抓到)。
 *
 * 形状取联合而非统一对象:无参数的提示直接存字符串(绝大多数),只有带
 * `{param}` 的模板才需要 `{ source, params }` 保留参数。渲染统一走
 * `formatPending`。
 */
export type PendingMessage = string | { source: string; params?: TranslateParams }

/** 渲染 `PendingMessage` —— 纯函数,由组件在渲染点传入当前 locale 的 tr。 */
export function formatPending(
  message: PendingMessage,
  tr: (source: string, params?: TranslateParams) => string,
): string {
  return typeof message === "string" ? tr(message) : tr(message.source, message.params)
}

/**
 * 恒等翻译器 —— 纯函数默认值,保证既有中文断言测试不受影响。
 *
 * 仍按 `{param}` 回填:源串是中文模板(如 `现价 {p}`),不译不等于不插值 ——
 * 直接返回源串会把 `{p}` 字面量漏进展示。语义与 `translateUiText(locale, …)`
 * 在「查不到译文」时一致(先返回源串,再统一 formatMessage)。
 */
export const identityTranslate: Translate = (s, params) => formatMessage(s, params)
