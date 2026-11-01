"use client"

import { useMemo } from "react"
import { Activity, ArrowDown, ArrowUp, BarChart3, AlertTriangle, Wifi, WifiOff, TrendingUp } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { useUiText } from "@/components/i18n/use-ui-text"

// Adapted from old use-paper-ws to use new frontend's WS hooks
// Uses a simplified interface that consuming pages can wire up

/** 带符号的持仓偏差(修正量),例如 +0.5 / -1.25 */
function formatSignedDelta(v: number): string {
  const rounded = Math.abs(v) >= 1 ? v.toFixed(2) : v.toFixed(4)
  return v > 0 ? `+${rounded}` : rounded
}

interface WsSignal {
  ts: number
  strategy_id?: string
  deployment_id?: string
  signal: unknown
  price?: number
  symbol?: string
}

interface SlotStatus {
  symbol: string
  interval: string
  bar_count: number
  signals_emitted: number
  orders_placed: number
  position: string
  /** 对账修正计数(后端 position_corrections)—— 可选,旧端点无此字段 */
  position_corrections?: number
  last_correction_ts?: number
  last_correction_delta?: number
}

interface RunnerMonitorPanelProps {
  strategyId: string
  accountId: string | null
  deploymentId?: string | null
  compact?: boolean
  maxSignals?: number
  // New: inject WS state from parent (wired to use-intelligence-ws or use-markets-ws)
  connected?: boolean
  signals?: WsSignal[]
  slotStatus?: SlotStatus | null
}

export function RunnerMonitorPanel({
  strategyId,
  compact = false,
  maxSignals = 20,
  connected = false,
  signals: wsSignals = [],
  slotStatus = null,
}: RunnerMonitorPanelProps) {
  const tr = useUiText()

  const relevantSignals = useMemo(() => {
    return wsSignals
      .filter((s) => s.strategy_id === strategyId || s.deployment_id === strategyId)
      .slice(0, maxSignals)
  }, [wsSignals, strategyId, maxSignals])

  const allSignals = useMemo(() => {
    return relevantSignals.map((s) => {
      const sig = typeof s.signal === "object" ? s.signal as Record<string, unknown> : { side: "", reason: String(s.signal) }
      return {
        ts: s.ts,
        side: String(sig.side || ""),
        reason: String(sig.reason || ""),
        strength: sig.strength as number | undefined,
        price: s.price ?? 0,
        symbol: s.symbol ?? "",
      }
    })
  }, [relevantSignals])

  if (!slotStatus && !connected) {
    return (
      <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
        <Activity className="h-4 w-4 animate-pulse" />
        {tr("加载中...")}
      </div>
    )
  }

  if (!slotStatus) {
    return (
      <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
        <AlertTriangle className="h-4 w-4" />
        {tr("无活跃部署")}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-green-500" />
          </span>
          <span className="font-medium text-foreground">{tr("运行中")}</span>
        </div>

        <Badge variant="outline" className="gap-1 text-[10px]">
          {connected ? <Wifi className="h-3 w-3 text-green-500" /> : <WifiOff className="h-3 w-3 text-red-400" />}
          {connected ? "WS" : tr("WS断开")}
        </Badge>

        <span className="text-muted-foreground">
          {slotStatus.symbol} / {slotStatus.interval}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <CounterCard
          label={tr("K线数")}
          value={slotStatus.bar_count}
          icon={<BarChart3 className="h-3.5 w-3.5 text-blue-400" />}
        />
        <CounterCard
          label={tr("信号数")}
          value={slotStatus.signals_emitted}
          icon={<TrendingUp className="h-3.5 w-3.5 text-yellow-400" />}
        />
        <CounterCard
          label={tr("订单数")}
          value={slotStatus.orders_placed}
          icon={<Activity className="h-3.5 w-3.5 text-green-400" />}
        />
        <CounterCard
          label={tr("持仓")}
          value={slotStatus.position}
          icon={
            slotStatus.position === "long" ? (
              <ArrowUp className="h-3.5 w-3.5 text-green-400" />
            ) : slotStatus.position === "short" ? (
              <ArrowDown className="h-3.5 w-3.5 text-red-400" />
            ) : (
              <Activity className="h-3.5 w-3.5 text-muted-foreground" />
            )
          }
        />
      </div>

      {/* 对账修正透明性:纠正发生了,用户就得看得见——否则他不知道系统改过持仓 */}
      {slotStatus && (slotStatus.position_corrections ?? 0) > 0 && (
        <div className="mt-1 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-600 dark:text-amber-400">
          {tr("持仓已按交易所实测对账修正")} {slotStatus.position_corrections} {tr("次")}
          {slotStatus.last_correction_ts ? (
            <>
              {tr(" · 最近 {t}", {
                t: new Date(slotStatus.last_correction_ts).toLocaleTimeString("zh-CN", {
                  hour: "2-digit",
                  minute: "2-digit",
                }),
              })}
              {typeof slotStatus.last_correction_delta === "number" &&
              Number.isFinite(slotStatus.last_correction_delta) &&
              slotStatus.last_correction_delta !== 0 ? (
                <> {tr("· 偏差 {d}", { d: formatSignedDelta(slotStatus.last_correction_delta) })}</>
              ) : null}
            </>
          ) : null}
        </div>
      )}

      {!compact && (
        <div className="mt-1">
          <div className="mb-1.5 font-medium text-muted-foreground">{tr("信号日志")}</div>
          {allSignals.length === 0 ? (
            <div className="py-4 text-center text-muted-foreground">{tr("暂无信号")}</div>
          ) : (
            <div className="max-h-48 overflow-y-auto rounded border border-border/40 bg-background/50">
              <table className="w-full text-[11px]">
                <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                  <tr className="text-left text-muted-foreground">
                    <th className="px-2 py-1">{tr("时间")}</th>
                    <th className="px-2 py-1">{tr("方向")}</th>
                    <th className="px-2 py-1">{tr("价格")}</th>
                    <th className="px-2 py-1">{tr("原因")}</th>
                  </tr>
                </thead>
                <tbody>
                  {allSignals.map((sig, i) => (
                    <tr key={`${sig.ts}-${i}`} className="border-t border-border/20 hover:bg-muted/30">
                      <td className="whitespace-nowrap px-2 py-1 text-muted-foreground">
                        {new Date(sig.ts).toLocaleTimeString()}
                      </td>
                      <td className="px-2 py-1">
                        <span className={sig.side.toLowerCase().includes("buy") ? "text-green-400" : "text-red-400"}>
                          {sig.side.toLowerCase().includes("buy") ? "BUY" : "SELL"}
                        </span>
                      </td>
                      <td className="px-2 py-1 font-mono">
                        {typeof sig.price === "number" ? sig.price.toLocaleString() : "-"}
                      </td>
                      <td className="max-w-[200px] truncate px-2 py-1 text-muted-foreground">
                        {sig.reason || "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function CounterCard({ label, value, icon }: { label: string; value: string | number; icon: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border/40 bg-muted/30 px-2.5 py-1.5">
      {icon}
      <div className="flex flex-col">
        <span className="text-[10px] text-muted-foreground">{label}</span>
        <span className="font-mono font-medium text-foreground">{String(value)}</span>
      </div>
    </div>
  )
}
