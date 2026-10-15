"use client"

import { resolveBackendHost } from "@/lib/backend-host";
import { useCallback, useEffect, useMemo, useState } from "react"
import { ArrowDown, ArrowUp } from "lucide-react"
import { cn } from "@/lib/utils"
import { filterBySymbol } from "@/lib/symbols"
import { useMarketsWs, type MarketMessage } from "@/lib/ws/use-markets-ws"

const API_HOST = resolveBackendHost()
const API_BASE = `http://${API_HOST}`

interface LargeTradeEvent {
  exchange: string
  symbol: string
  market_type: "spot" | "futures"
  side: "buy" | "sell"
  price: number
  quantity: number
  notional_usd: number
  trade_id: string
  timestamp: number
}

const EXCHANGES = ["all", "binance", "okx"] as const

function formatUSD(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`
  return `$${val.toFixed(0)}`
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

function sizeCategory(usd: number): string {
  if (usd >= 1_000_000) return "whale"
  if (usd >= 500_000) return "large"
  if (usd >= 100_000) return "medium"
  return "normal"
}

/** 行背景按大小档位着色。 */
function rowBgClass(cat: string): string {
  if (cat === "whale") return "bg-yellow-500/10 border border-yellow-500/30"
  if (cat === "large") return "bg-orange-500/5"
  if (cat === "medium") return "bg-muted/50"
  return ""
}

/** 名义金额文本按档位着色。 */
function notionalTextClass(cat: string): string {
  if (cat === "whale") return "text-yellow-400"
  if (cat === "large") return "text-orange-400"
  return "text-foreground"
}

function TradeRow({ tr, index }: { tr: LargeTradeEvent; index: number }) {
  const cat = sizeCategory(tr.notional_usd)
  const isBuy = tr.side === "buy"
  return (
    <div
      key={`${tr.trade_id}-${index}`}
      className={cn(
        "flex items-center gap-2 px-2 py-1.5 rounded text-xs font-mono transition-colors",
        rowBgClass(cat),
      )}
    >
      {isBuy ? (
        <ArrowUp className="h-3 w-3 text-green-500 shrink-0" />
      ) : (
        <ArrowDown className="h-3 w-3 text-red-500 shrink-0" />
      )}
      <span className="text-muted-foreground w-14">{formatTime(tr.timestamp)}</span>
      <span className="w-20 truncate">{tr.symbol}</span>
      <span className={cn("w-20 text-right", isBuy ? "text-green-400" : "text-red-400")}>
        {tr.price.toLocaleString()}
      </span>
      <span className="w-16 text-right">{tr.quantity?.toFixed(4) ?? "—"}</span>
      <span className={cn("w-20 text-right font-bold", notionalTextClass(cat))}>
        {formatUSD(tr.notional_usd)}
      </span>
      <span className="text-muted-foreground w-24 text-right flex items-center justify-end gap-1">
        <span className={cn(
          "text-[10px] px-1 rounded",
          tr.market_type === "futures" ? "bg-purple-500/20 text-purple-400" : "bg-blue-500/20 text-blue-400"
        )}>
          {tr.market_type === "futures" ? "合约" : "现货"}
        </span>
        {tr.exchange}
      </span>
    </div>
  )
}

interface LargeTradeFeedProps {
  symbolFilter?: string
}

export function LargeTradeFeed({ symbolFilter }: LargeTradeFeedProps) {
  const [trades, setTrades] = useState<LargeTradeEvent[]>([])
  const [stats, setStats] = useState<{ buy_count: number; sell_count: number; buy_volume_usd: number; sell_volume_usd: number } | null>(null)
  const [exchangeFilter, setExchangeFilter] = useState<string>("all")

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/derivatives/large-trades/recent?limit=50`)
      .then((r) => r.json())
      .then((d) => { if (d.data?.events) setTrades([...d.data.events].reverse()) })
      .catch(() => {})

    fetch(`${API_BASE}/api/v1/derivatives/large-trades/stats`)
      .then((r) => r.json())
      .then((d) => { if (d.data) setStats(d.data) })
      .catch(() => {})
  }, [])

  // 走单例 Provider（共享 1 条 WS 连接）订阅 large_trade。
  // 之前手动 new WebSocket 订阅 binance + okx 两交易所 → 每挂载一条额外连接。
  const onMessageBatch = useCallback((msgs: MarketMessage[]) => {
    for (const msg of msgs) {
      if (msg.type !== "large_trade") continue
      setTrades((prev) => [msg.data as unknown as LargeTradeEvent, ...prev].slice(0, 200))
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

  const filtered = useMemo(() => {
    let result = trades
    if (exchangeFilter !== "all") {
      result = result.filter((tr) => tr.exchange === exchangeFilter)
    }
    return filterBySymbol(result, symbolFilter)
  }, [trades, exchangeFilter, symbolFilter])

  const buyCount = stats?.buy_count ?? 0
  const sellCount = stats?.sell_count ?? 0
  const buyVol = stats?.buy_volume_usd ?? 0
  const sellVol = stats?.sell_volume_usd ?? 0

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-4 gap-2 text-xs">
        <div className="bg-green-500/10 rounded p-2 text-center">
          <div className="text-green-400 font-mono font-bold">{buyCount}</div>
          <div className="text-muted-foreground">买入笔数</div>
        </div>
        <div className="bg-red-500/10 rounded p-2 text-center">
          <div className="text-red-400 font-mono font-bold">{sellCount}</div>
          <div className="text-muted-foreground">卖出笔数</div>
        </div>
        <div className="bg-green-500/10 rounded p-2 text-center">
          <div className="text-green-400 font-mono font-bold">{formatUSD(buyVol)}</div>
          <div className="text-muted-foreground">买入量</div>
        </div>
        <div className="bg-red-500/10 rounded p-2 text-center">
          <div className="text-red-400 font-mono font-bold">{formatUSD(sellVol)}</div>
          <div className="text-muted-foreground">卖出量</div>
        </div>
      </div>

      {/* 交易所筛选 + 交易对指示 */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {EXCHANGES.map((ex) => (
          <button
            key={ex}
            onClick={() => setExchangeFilter(ex)}
            className={cn(
              "px-2.5 py-0.5 text-[11px] rounded transition-colors",
              exchangeFilter === ex
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            )}
          >
            {ex === "all" ? "全部交易所" : ex}
          </button>
        ))}
        {symbolFilter && (
          <span className="text-[11px] text-muted-foreground px-2 py-0.5 bg-muted/50 rounded">
            {symbolFilter}
          </span>
        )}
      </div>

      <div className="max-h-[400px] overflow-y-auto space-y-1 hover-scrollbar">
        {filtered.length === 0 ? (
          <div className="text-center text-muted-foreground text-xs py-8">
            等待大单数据...
          </div>
        ) : (
          filtered.map((tr, i) => (
            <TradeRow key={`${tr.trade_id}-${i}`} tr={tr} index={i} />
          ))
        )}
      </div>
    </div>
  )
}
