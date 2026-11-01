"use client"

import { useCallback, useEffect, useState } from "react"
import { cn } from "@/lib/utils"
import { formatTime } from "@/lib/format"
import { getStrategyVersions } from "./api-bridge"
import { History, ChevronDown, RotateCcw } from "lucide-react"
import { useUiText } from "@/components/i18n/use-ui-text"

interface BacktestSummary {
  id: string
  total_return: number
  sharpe_ratio: number
  max_drawdown: number
  win_rate: number
  trades_count: number
  created_at: number
}

interface VersionSnapshot {
  id: string
  strategy_id: string
  version: number
  config_snapshot: Record<string, unknown>
  note: string
  created_at: number
  backtests?: BacktestSummary[]
}

export interface VersionTimelineProps {
  strategyId: string
  currentVersion: number
  onRevert?: (config: Record<string, unknown>, version: number) => void
  refreshKey?: number
}

function formatPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

/** 从回测列表选 total_return 最高的一条(空列表 → null)。纯函数。 */
export function pickBestBacktest<T extends { total_return: number }>(
  backtests: T[] | undefined,
): T | null {
  if (!backtests || backtests.length === 0) return null
  return backtests.reduce((a, b) => (a.total_return > b.total_return ? a : b))
}

export function VersionTimeline({
  strategyId,
  currentVersion,
  onRevert,
  refreshKey,
}: VersionTimelineProps) {
  const tr = useUiText()

  const [versions, setVersions] = useState<VersionSnapshot[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [empty, setEmpty] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getStrategyVersions(strategyId)
      if (res.data) {
        setVersions(res.data as unknown as VersionSnapshot[])
        setEmpty(false)
        return
      }
      // dev 代理偶发抖动(api-client 12s abort/ECONNRESET):空结果重试一次
      await new Promise((r) => setTimeout(r, 1500))
      const retry = await getStrategyVersions(strategyId)
      if (retry.data) {
        setVersions(retry.data as unknown as VersionSnapshot[])
        setEmpty(false)
      } else {
        setEmpty(true)
      }
    } finally {
      setLoading(false)
    }
  }, [strategyId])

  useEffect(() => {
    void load()
  }, [load, refreshKey])

  // 军规5:空结果显示为空+教学,不静默空白(首次保存后这里出现第一版)
  if (empty && !loading) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
        <History className="h-5 w-5 text-muted-foreground" />
        <p className="text-xs text-foreground">{tr("还没有版本记录")}</p>
        <p className="max-w-[280px] text-[11px] leading-relaxed text-muted-foreground">
          {tr("每次保存策略或 AI 修改参数都会留一个版本快照;版本多了可以在这里一键回滚到任一版。")}
        </p>
      </div>
    )
  }

  if (versions.length === 0) return null

  return (
    <div className="border-b border-border/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-muted/40"
      >
        <History className="h-3 w-3 text-muted-foreground" />
        <span className="font-medium text-muted-foreground">{tr("版本历史")}</span>
        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {versions.length}
        </span>
        <ChevronDown
          className={cn(
            "ml-auto h-3 w-3 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="max-h-48 overflow-y-auto px-3 pb-2">
          <div className="space-y-1">
            {versions.map((v) => {
              const isCurrent = v.version === currentVersion
              const best = pickBestBacktest(v.backtests)

              return (
                <div
                  key={v.id}
                  role={!isCurrent && onRevert ? "button" : undefined}
                  tabIndex={!isCurrent && onRevert ? 0 : undefined}
                  onClick={!isCurrent && onRevert ? () => onRevert(v.config_snapshot, v.version) : undefined}
                  onKeyDown={!isCurrent && onRevert ? (e) => { if (e.key === "Enter") onRevert(v.config_snapshot, v.version) } : undefined}
                  className={cn(
                    "group flex items-start gap-2 rounded-lg px-2 py-1.5 text-xs",
                    isCurrent
                      ? "border border-primary/20 bg-primary/5"
                      : "cursor-pointer hover:bg-muted/40",
                  )}
                >
                  <div className="flex flex-col items-center pt-0.5">
                    <div
                      className={cn(
                        "flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold",
                        isCurrent
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {v.version}
                    </div>
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate font-medium text-foreground">
                        {v.note || `v${v.version}`}
                      </span>
                      {isCurrent && (
                        <span className="shrink-0 rounded bg-primary/10 px-1 py-0.5 text-[9px] font-medium text-primary">
                          {tr("当前")}
                        </span>
                      )}
                      <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                        {formatTime(v.created_at)}
                      </span>
                    </div>

                    {best && (
                      <div className="mt-0.5 flex gap-2 text-[10px] text-muted-foreground">
                        <span className={best.total_return >= 0 ? "text-emerald-400" : "text-red-400"}>
                          {tr("收益")} {formatPct(best.total_return)}
                        </span>
                        <span>{tr("夏普")} {best.sharpe_ratio.toFixed(2)}</span>
                        <span>{tr("回撤")} {formatPct(best.max_drawdown)}</span>
                        <span>{tr("胜率")} {formatPct(best.win_rate)}</span>
                      </div>
                    )}
                  </div>

                  {!isCurrent && onRevert && (
                    <div className="hidden shrink-0 items-center pt-0.5 text-[9px] text-muted-foreground group-hover:flex">
                      <RotateCcw className="mr-0.5 h-2.5 w-2.5" />
                      {tr("加载")}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
          {loading && (
            <div className="py-2 text-center text-[10px] text-muted-foreground">{tr("加载中...")}</div>
          )}
        </div>
      )}
    </div>
  )
}
