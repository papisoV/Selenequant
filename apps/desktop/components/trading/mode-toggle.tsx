"use client"

import { useState, useEffect } from "react"
import { api, apiPost } from "@/lib/api-client"
import { cn } from "@/lib/utils"
import { useUiText } from "@/components/i18n/use-ui-text"

export function ModeToggle() {
  const tr = useUiText()

  const [mode, setMode] = useState<"paper" | "live">("paper")
  const [loading, setLoading] = useState(false)
  const [liveWarning, setLiveWarning] = useState(false)

  useEffect(() => {
    api<{ mode: string }>("/api/v1/trading/mode").then((data) => {
      if (data) setMode(data.mode as "paper" | "live")
    }).catch(() => { /* ignore */ })
  }, [])

  async function handleToggle(target: "paper" | "live") {
    if (target === mode || loading) return

    if (target === "live") {
      setLiveWarning(true)
      setTimeout(() => setLiveWarning(false), 3000)
      return
    }

    setLoading(true)
    try {
      const data = await apiPost<{ mode: string }>("/api/v1/trading/mode", { mode: target })
      if (data) setMode(data.mode as "paper" | "live")
    } catch {
      setLiveWarning(true)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative flex items-center gap-2">
      <div className="inline-flex rounded border border-border-soft p-0.5 bg-surface-2">
        <button
          onClick={() => handleToggle("paper")}
          disabled={loading}
          className={cn(
            "px-2 py-1 rounded text-[10px] font-medium transition-colors",
            mode === "paper"
              ? "bg-gold/20 text-gold"
              : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
          )}
        >
          {tr("模拟")}
        </button>
        <button
          onClick={() => handleToggle("live")}
          disabled={loading}
          className={cn(
            "px-2 py-1 rounded text-[10px] font-medium transition-colors",
            mode === "live"
              ? "bg-red-500/20 text-red-400"
              : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
          )}
        >
          {tr("实盘")}
        </button>
      </div>
      {liveWarning && (
        <span className="absolute top-full left-0 mt-1 z-20 whitespace-nowrap text-[10px] text-amber-400 animate-in fade-in">
          Live trading is not available in v0.1
        </span>
      )}
    </div>
  )
}
