"use client"

import React, { createContext, useContext, useEffect, useMemo, useState } from "react"
import {
  DEFAULT_LOCALE,
  LOCALE_STORAGE_KEY,
  isLocale,
  resolveMessage,
} from "@/lib/i18n"
import type { Locale, MessageKey, TranslateParams } from "@/lib/i18n/types"

type I18nContextValue = {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: MessageKey, params?: TranslateParams) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

/** 首次挂载时从 localStorage 恢复已存 locale。 */
function useRestoredLocale(setLocale: (l: Locale) => void): void {
  useEffect(() => {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY)
    if (stored && isLocale(stored)) {
      setLocale(stored)
    }
  }, [setLocale])
}

/** locale 变化时写回 localStorage 并同步 <html> 的 lang/data-locale。 */
function usePersistLocale(locale: Locale): void {
  useEffect(() => {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale)
    document.documentElement.lang = locale
    document.documentElement.dataset.locale = locale
  }, [locale])
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

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE)

  useRestoredLocale(setLocale)
  usePersistLocale(locale)

  const value = useMemo<I18nContextValue>(
    () => makeI18nValue(locale, setLocale),
    [locale]
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
