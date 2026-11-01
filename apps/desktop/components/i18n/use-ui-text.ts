"use client"

import { useCallback, useEffect, useState } from "react"
import { DEFAULT_LOCALE, readStoredLocale, formatMessage } from "@/lib/i18n"
import { translateUiText } from "@/lib/i18n/ui-text"
import { translateLogicValue } from "@/lib/i18n/values"
import type { Locale, TranslateParams } from "@/lib/i18n/types"
import { useOptionalI18nContext } from "./i18n-provider"

/**
 * 源串翻译钩子 —— 在渲染点把中文源串译成当前 locale。
 *
 * 与 `useI18n().t()` 的区别：`t()` 收的是 `MessageKey`(命名键，须先登记)，
 * 本钩子收的是**中文源串本身**。页面标题/导航链名定义在数据文件里
 * (`page-manifest.ts` / `navigation-chains.ts`)，那些文件被其他副本改动中，
 * 不能改成 key 引用；用源串当键就能在渲染点翻译而不动数据层。
 *
 * provider 缺席时(测试裸渲染 `<Sidebar scene="research" />`)原样返回源串 ——
 * 既有断言中文的测试无需包 provider 即可继续通过。生产环境所有页面都在
 * `ClientProviders` 内的 `I18nProvider` 之下，故恒走真实翻译分支。
 *
 * provider 缺席时仍走 `formatMessage`：源串即中文展示，`{n}` 占位要照常替换。
 * 否则 `tr("已写入 {n} 根", { n: 5 })` 在裸渲染下会显示字面量 `{n}`
 * (en 表查不到时同理 —— `translateUiText` 内部也是先查表再统一 formatMessage)。
 */
export function useUiText() {
  const ctx = useOptionalI18nContext()
  const locale = ctx?.locale
  return useCallback(
    (source: string, params?: TranslateParams): string =>
      locale ? translateUiText(locale, source, params) : formatMessage(source, params),
    [locale],
  )
}

/**
 * provider 之外的源串翻译 —— 给 `app/global-error.tsx` 用。
 *
 * global-error 自带 <html>/<body>、替换根布局，因此**不在** I18nProvider 内，
 * 拿不到 context；改为同步读 localStorage。首帧先渲染 DEFAULT_LOCALE、挂载后
 * 再切到已存 locale('en'/'zh-CN')，与 `I18nProvider` 的恢复策略一致，
 * 避免 SSR/CSR 首帧不一致的 hydration 警告。
 */
export function useStoredUiText() {
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE)
  useEffect(() => {
    setLocale(readStoredLocale())
  }, [])
  return useCallback(
    (source: string, params?: TranslateParams): string =>
      translateUiText(locale, source, params),
    [locale],
  )
}

/**
 * 当前 locale —— provider 之外返回 DEFAULT_LOCALE(zh-CN)。
 *
 * 给「同一字段在两个 locale 下展示不同内容」的场景用:如指标卡同时显示
 * `name`(英文名)与 `nameZh`(中文名),en 模式下应隐藏 `nameZh` —— 这是
 * 「显不显示」而非「译成什么」,`tr()` 表达不了。测试裸渲染时回落 zh-CN,
 * 与既有断言(期望看到中文名)一致。
 */
export function useLocale(): Locale {
  return useOptionalI18nContext()?.locale ?? DEFAULT_LOCALE
}

/**
 * 逻辑值翻译钩子 —— 把**数据契约用的中文值**(如 `side === "多"`、`kind === "信号"`)
 * 译成展示文案;逻辑侧仍用中文值比较。
 *
 * 与 `useUiText()` 的分工:
 *   - `tr()`  收展示文案源串(`tr("策略工厂")`)—— 查不到原样返回
 *   - `tl()`  收逻辑值(`tl(row.side)`)—— 值域有限、枚举式,登记在 `lib/i18n/values.ts`
 * 两者输入域不重叠,刻意分表(见 values.ts 头部注释)。
 *
 * provider 缺席时原样返回 —— 与 `useUiText` 同策略,保证裸渲染测试不受影响
 * (中文逻辑值在 zh-CN 下本就应该原样显示)。
 */
export function useLogicText() {
  const ctx = useOptionalI18nContext()
  const locale = ctx?.locale
  return useCallback(
    (value: string): string =>
      locale ? translateLogicValue(locale, value) : value,
    [locale],
  )
}
