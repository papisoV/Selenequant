"use client"

import { useI18n } from "./use-i18n"
import { Globe } from "lucide-react"

export function LocaleSwitcherCompact() {
  const { locale, setLocale } = useI18n()

  const toggle = () => {
    setLocale(locale === "en" ? "zh-CN" : "en")
  }

  return (
    <button
      onClick={toggle}
      className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium transition-colors hover:bg-muted hover:border-primary/40"
      title={locale === "en" ? "切换到中文" : "Switch to English"}
    >
      <Globe className="h-3.5 w-3.5 text-primary" />
      <span>{locale === "en" ? "中文" : "EN"}</span>
    </button>
  )
}
