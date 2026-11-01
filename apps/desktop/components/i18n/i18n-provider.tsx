"use client"

import React, { createContext, useContext, useEffect, useMemo, useRef, useState } from "react"
import {
  DEFAULT_LOCALE,
  LOCALE_STORAGE_KEY,
  readStoredLocale,
  resolveMessage,
} from "@/lib/i18n"
import type { Locale, MessageKey, TranslateParams } from "@/lib/i18n/types"

type I18nContextValue = {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: MessageKey, params?: TranslateParams) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

/**
 * 挂载时从 localStorage 恢复已存 locale，随后把 locale 回写到
 * localStorage 与 <html> 的 lang/data-locale。
 *
 * 首次执行的特殊处理:若已存 locale 与当前值不同，只 setLocale 而不回写 ——
 * 否则会先用 DEFAULT_LOCALE 覆盖 layout 中内联脚本已设好的 data-locale，
 * 造成涨跌色/文案闪一下再翻回来(Chinese 用户每页都会看到)。已存值等于
 * 当前值时(含无存储的默认场景)照常回写，与既有行为锁测试一致。
 */
function useSyncedLocale(
  locale: Locale,
  setLocale: (l: Locale) => void,
): void {
  const firstRef = useRef(true)
  useEffect(() => {
    if (firstRef.current) {
      firstRef.current = false
      const stored = readStoredLocale()
      if (stored !== locale) {
        setLocale(stored)
        return
      }
    }
    try {
      localStorage.setItem(LOCALE_STORAGE_KEY, locale)
    } catch {
      // 隐私模式等 localStorage 不可用 → 仍同步 DOM 属性
    }
    document.documentElement.lang = locale
    document.documentElement.dataset.locale = locale
  }, [locale, setLocale])
}

/** 构造 context 值(含绑定当前 locale 的 t())。 */
function makeI18nValue(
  locale: Locale,
  setLocale: (l: Locale) => void,
): I18nContextValue {
  return {
    locale,
    setLocale,
    t: (key: MessageKey, params?: TranslateParams) => resolveMessage(locale, key, params),
  }
}

export function useI18nContext() {
  const value = useContext(I18nContext)
  if (!value) {
    throw new Error("useI18nContext must be used inside I18nProvider")
  }
  return value
}

/**
 * 不抛出的 context 消费者：provider 之外返回 null。
 * 供 `useUiText()` 使用 —— 组件在测试里裸渲染(无 provider)时应回落源串，
 * 而不是把既有测试全炸掉；生产环境所有渲染路径都经过 I18nProvider。
 */
export function useOptionalI18nContext(): I18nContextValue | null {
  return useContext(I18nContext)
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE)

  useSyncedLocale(locale, setLocale)

  const value = useMemo<I18nContextValue>(
    () => makeI18nValue(locale, setLocale),
    [locale]
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
