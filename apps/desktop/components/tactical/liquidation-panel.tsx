"use client"

import { resolveBackendHost } from "@/lib/backend-host";
import { useCallback, useEffect, useMemo, useState } from "react"
import { Flame } from "lucide-react"
import { cn } from "@/lib/utils"
import { filterBySymbol } from "@/lib/symbols"
import { useMarketsWs, type MarketMessage } from "@/lib/ws/use-markets-ws"

const API_HOST = resolveBackendHost()
const API_BASE = `http://${API_HOST}`

interface LiquidationStats {
  window: string
  long_liquidated_usd: number
  short_liquidated_usd: number
  total_liquidated_usd: number
  long_count: number
  short_count: number
  largest_single_usd: number
}

interface LiquidationEvent {
  exchange: string
  symbol: string
  side: "long" | "short"
  quantity: number
  price: number
  notional_usd: number
  timestamp: number
}

function formatUSD(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`
  return `$${val.toFixed(0)}`
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

const WINDOWS = ["15m", "30m", "1h", "4h", "24h"] as const

interface LiquidationPanelProps {
  symbolFilter?: string
}

/** 拉取 JSON 并取 data 字段(失败静默)。 */
async function fetchData<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url)
    const body = await res.json()
    return (body?.data ?? null) as T | null
  } catch {
    return null
  }
}

/** 每 5s 轮询清算统计, 写入 setter。 */
function usePolledLiquidationStats(
  setAllStats: (v: Record<string, LiquidationStats>) => void,
) {
  useEffect(() => {
    const tick = () =>
      void fetchData<{ windows?: Record<string, LiquidationStats> }>(
        `${API_BASE}/api/v1/derivatives/liquidation-stats/all`,
      ).then((d) => {
        if (d?.windows) setAllStats(d.windows)
      })
    tick()
    const id = setInterval(tick, 5000)
    return () => clearInterval(id)
  }, [setAllStats])
}

/** 挂载时拉一次最近清算事件(倒序), 写入 setter。 */
function useRecentLiquidations(
  setEvents: (updater: (prev: LiquidationEvent[]) => LiquidationEvent[]) => void,
) {
  useEffect(() => {
    void fetchData<{ events?: LiquidationEvent[] }>(
      `${API_BASE}/api/v1/derivatives/liquidations/recent?limit=200`,
    ).then((d) => {
      if (d?.events) setEvents(() => [...d.events!].reverse())
    })
  }, [setEvents])
}

export function LiquidationPanel({ symbolFilter }: LiquidationPanelProps) {
  const [allStats, setAllStats] = useState<Record<string, LiquidationStats>>({})
  const [events, setEvents] = useState<LiquidationEvent[]>([])
  const [selectedWindow, setSelectedWindow] = useState<string>("1h")

  usePolledLiquidationStats(setAllStats)
  useRecentLiquidations(setEvents)

  // 走单例 Provider（共享 1 条 WS 连接）订阅 liquidation + liquidation_stats。
  // 之前手动 new WebSocket 订阅 binance + okx 两交易所 → 每挂载一条额外连接。
  const onMessageBatch = useCallback((msgs: MarketMessage[]) => {
    for (const msg of msgs) {
      if (msg.type === "liquidation") {
        setEvents((prev) => [msg.data as unknown as LiquidationEvent, ...prev].slice(0, 200))
      } else if (msg.type === "liquidation_stats") {
        const d = msg.data as Record<string, unknown>
        const window = d.window as string
        if (window) setAllStats((prev) => ({ ...prev, [window]: d as unknown as LiquidationStats }))
      }
    }
  }, [])

  useMarketsWs({
    symbols: ["ALL"],
    exchange: "binance",
    marketType: "futures",
    onMessageBatch,
  })
  useMarketsWs({
    symbols: ["ALL"],
    exchange: "okx",
    marketType: "futures",
    onMessageBatch,
  })

  // 根据交易对筛选事件
  const filteredEvents = useMemo(
    () => filterBySymbol(events, symbolFilter),
    [events, symbolFilter],
  )

  const stats = allStats[selectedWindow]

  return (
    <div className="space-y-3">
      <div className="flex gap-1 items-center">
        {WINDOWS.map((w) => (
          <button
            key={w}
            onClick={() => setSelectedWindow(w)}
            className={cn(
              "px-2 py-1 text-xs rounded transition-colors",
              selectedWindow === w
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            )}
          >
            {w}
          </button>
        ))}
        {symbolFilter && (
          <span className="text-[11px] text-muted-foreground px-2 py-0.5 bg-muted/50 rounded ml-1">
            {symbolFilter}
          </span>
        )}
      </div>

      {stats ? (
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="bg-red-500/10 rounded p-2 text-center">
            <div className="text-red-400 font-mono font-bold">{formatUSD(stats.long_liquidated_usd)}</div>
            <div className="text-muted-foreground">多头强平 ({stats.long_count})</div>
          </div>
          <div className="bg-green-500/10 rounded p-2 text-center">
            <div className="text-green-400 font-mono font-bold">{formatUSD(stats.short_liquidated_usd)}</div>
            <div className="text-muted-foreground">空头强平 ({stats.short_count})</div>
          </div>
          <div className="bg-yellow-500/10 rounded p-2 text-center">
            <Flame className="h-3 w-3 text-yellow-400 mx-auto mb-0.5" />
            <div className="text-yellow-400 font-mono font-bold">{formatUSD(stats.total_liquidated_usd)}</div>
            <div className="text-muted-foreground">总计</div>
          </div>
        </div>
      ) : (
        <div className="text-center text-muted-foreground text-xs py-4">暂无数据</div>
      )}

      <div className="max-h-[300px] overflow-y-auto space-y-1 hover-scrollbar">
        {filteredEvents.length === 0 ? (
          <div className="text-center text-muted-foreground text-xs py-4">
            等待强平数据...
          </div>
        ) : (
          filteredEvents.map((e, i) => (
            <div
              key={`liq-${e.timestamp}-${i}`}
              className={cn(
                "flex items-center gap-2 px-2 py-1 rounded text-xs font-mono",
                e.notional_usd >= 1_000_000 ? "bg-yellow-500/10 border border-yellow-500/20" :
                e.notional_usd >= 100_000 ? "bg-orange-500/5" : "bg-muted/30",
              )}
            >
              <Flame className={cn(
                "h-3 w-3 shrink-0",
                e.notional_usd >= 1_000_000 ? "text-yellow-400" : "text-orange-400"
              )} />
              <span className="text-muted-foreground w-14">{formatTime(e.timestamp)}</span>
              <span className="w-20 truncate">{e.symbol}</span>
              {e.side === "long" ? (
                <span className="text-red-400 w-10">多头</span>
              ) : (
                <span className="text-green-400 w-10">空头</span>
              )}
              <span className="w-20 text-right">{e.price.toLocaleString()}</span>
              <span className={cn(
                "w-20 text-right font-bold",
                e.notional_usd >= 1_000_000 ? "text-yellow-400" : "text-foreground"
              )}>
                {formatUSD(e.notional_usd)}
              </span>
              <span className="text-muted-foreground w-14 text-right">{e.exchange}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
