"use client"

import { resolveBackendHost } from "@/lib/backend-host";
import { useCallback, useEffect, useRef, useState } from "react"
import { cn } from "@/lib/utils"
import { useMarketsWs, type MarketMessage } from "@/lib/ws/use-markets-ws"

const API_HOST = resolveBackendHost()
const API_BASE = `http://${API_HOST}`

interface FundingRateData {
  exchange: string
  symbol: string
  funding_rate: number
  mark_price: number
  index_price: number
  next_funding_time: number
  timestamp: number
}

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(4)}%`
}

function formatCountdown(nextFunding: number): string {
  const diff = nextFunding - Date.now()
  if (diff <= 0) return "Now"
  const h = Math.floor(diff / 3_600_000)
  const m = Math.floor((diff % 3_600_000) / 60_000)
  return `${h}h ${m}m`
}

export function FundingRatePanel() {
  const [rates, setRates] = useState<Record<string, FundingRateData>>({})
  const ratesRef = useRef<Record<string, FundingRateData>>({})

  useEffect(() => {
    const fetchRates = () => {
      fetch(`${API_BASE}/api/v1/derivatives/funding-rates`)
        .then((r) => r.json())
        .then((d) => { if (d.data?.rates) setRates(d.data.rates) })
        .catch(() => {})
    }
    fetchRates()
    const interval = setInterval(fetchRates, 3000)
    return () => clearInterval(interval)
  }, [])

  // 走单例 Provider（共享 1 条 WS 连接）订阅 funding_rate 推送。
  // 之前这里手动 new WebSocket，每挂载一次组件就多一条同端点连接 → 后端重复推送 ×N
  const onMessageBatch = useCallback((msgs: MarketMessage[]) => {
    let changed = false
    const next = { ...ratesRef.current }
    for (const msg of msgs) {
      if (msg.type !== "funding_rate") continue
      const d = msg.data as unknown as FundingRateData
      const key = `${d.exchange}:${d.symbol}`
      if (!key) continue
      next[key] = d
      changed = true
    }
    if (!changed) return
    ratesRef.current = next
    setRates(next)
  }, [])

  useMarketsWs({
    symbols: ["ALL"],
    exchange: "binance",
    marketType: "futures",
    onMessageBatch,
  })

  const entries = Object.entries(rates)

  return (
    <div className="space-y-2">
      {entries.length === 0 ? (
        <div className="text-center text-muted-foreground text-xs py-4">
          等待资金费率数据...
        </div>
      ) : (
        <div className="space-y-1">
          <div className="grid grid-cols-5 gap-2 text-xs text-muted-foreground px-2 py-1">
            <span>交易对</span>
            <span className="text-right">费率</span>
            <span className="text-right">标记价格</span>
            <span className="text-right">指数价格</span>
            <span className="text-right">下次结算</span>
          </div>
          {entries.map(([key, r]) => {
            const isPositive = r.funding_rate > 0
            const isExtreme = Math.abs(r.funding_rate) > 0.001
            return (
              <div
                key={key}
                className={cn(
                  "grid grid-cols-5 gap-2 text-xs font-mono px-2 py-1.5 rounded",
                  isExtreme ? "bg-yellow-500/10" : "bg-muted/30",
                )}
              >
                <span className="truncate">{r.symbol}</span>
                <span className={cn(
                  "text-right font-bold",
                  isPositive ? "text-green-400" : "text-red-400",
                  isExtreme && "text-yellow-400"
                )}>
                  {formatRate(r.funding_rate)}
                </span>
                <span className="text-right">{r.mark_price.toLocaleString()}</span>
                <span className="text-right">{r.index_price > 0 ? r.index_price.toLocaleString() : "—"}</span>
                <span className="text-right text-muted-foreground">
                  {r.next_funding_time > 0 ? formatCountdown(r.next_funding_time) : "—"}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
