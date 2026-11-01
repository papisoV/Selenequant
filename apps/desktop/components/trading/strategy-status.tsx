"use client"

import { useCallback, useState } from "react"
import { useReconnectingWs } from "@/lib/hooks/use-reconnecting-ws"
import { Wifi, WifiOff, TrendingUp, TrendingDown, Minus } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { useUiText } from "@/components/i18n/use-ui-text"

export interface StrategySignal {
  strategy_id: string
  symbol: string
  side: "buy" | "sell" | "hold"
  strength: number
  reason: string
  timestamp: number
  price?: number
}

interface StrategyStatusProps {
  strategyId?: string
  compact?: boolean
  wsUrl?: string
}

export function StrategyStatus({ strategyId, compact = false, wsUrl }: StrategyStatusProps) {
  const tr = useUiText()

  const [signals, setSignals] = useState<StrategySignal[]>([])
  const [latestSignal, setLatestSignal] = useState<StrategySignal | null>(null)

  const url =
    wsUrl ||
    (typeof window === "undefined"
      ? ""
      : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/api/v1/ws/strategy`)

  const onMessage = useCallback((event: MessageEvent) => {
    try {
      const msg = JSON.parse(event.data)
      if (msg.type === "strategy_signal") {
        const signal: StrategySignal = {
          strategy_id: msg.data.strategy_id,
          symbol: msg.data.symbol,
          side: msg.data.side?.toLowerCase() || "hold",
          strength: msg.data.strength || 0,
          reason: msg.data.reason || "",
          timestamp: Date.now(),
          price: msg.data.price,
        }
        setLatestSignal(signal)
        setSignals((prev) => [signal, ...prev].slice(0, 20))
      }
    } catch {
      // Ignore parse errors
    }
  }, [])

  const { connected, send } = useReconnectingWs(url, onMessage, () => {
    send({ action: "subscribe", channel: "strategy" })
  })

  const getSignalIcon = (side: string) => {
    if (side.includes("buy")) return <TrendingUp className="h-4 w-4 text-green-400" />
    if (side.includes("sell")) return <TrendingDown className="h-4 w-4 text-red-400" />
    return <Minus className="h-4 w-4 text-muted-foreground" />
  }

  const getSignalColor = (side: string) => {
    if (side.includes("buy")) return "text-green-400"
    if (side.includes("sell")) return "text-red-400"
    return "text-muted-foreground"
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{tr("策略状态")}</span>
        <Badge variant={connected ? "default" : "secondary"} className="gap-1 text-xs">
          {connected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
          {connected ? tr("已连接") : tr("未连接")}
        </Badge>
      </div>

      {latestSignal ? (
        <div className="flex items-center gap-3 rounded-lg border border-border/40 bg-muted/30 p-3">
          {getSignalIcon(latestSignal.side)}
          <div className="flex flex-col flex-1 min-w-0">
            <span className={`text-lg font-bold ${getSignalColor(latestSignal.side)}`}>
              {latestSignal.side.toUpperCase()}
            </span>
            <span className="text-xs text-muted-foreground truncate">
              {latestSignal.reason || tr("无原因")}
            </span>
          </div>
          <div className="flex flex-col items-end">
            <span className="text-sm font-mono">{latestSignal.strength.toFixed(2)}</span>
            <span className="text-xs text-muted-foreground">{tr("强度")}</span>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
          {tr("暂无信号")}
        </div>
      )}

      {!compact && signals.length > 0 && (
        <div className="space-y-1">
          <span className="text-xs text-muted-foreground">{tr("信号历史")}</span>
          <div className="max-h-32 overflow-y-auto rounded border border-border/40">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-muted/80">
                <tr className="text-left text-muted-foreground">
                  <th className="px-2 py-1">{tr("时间")}</th>
                  <th className="px-2 py-1">{tr("信号")}</th>
                  <th className="px-2 py-1">{tr("强度")}</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((sig, i) => (
                  <tr key={`${sig.timestamp}-${i}`} className="border-t border-border/20">
                    <td className="px-2 py-1 text-muted-foreground">
                      {new Date(sig.timestamp).toLocaleTimeString()}
                    </td>
                    <td className={`px-2 py-1 font-medium ${getSignalColor(sig.side)}`}>
                      {sig.side.toUpperCase()}
                    </td>
                    <td className="px-2 py-1 font-mono">{sig.strength.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
