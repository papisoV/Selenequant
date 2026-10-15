"use client"

import React from "react"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { TrendingUp, TrendingDown } from "lucide-react"
import type { Ticker } from "@/lib/types/backend"

interface TickerPanelProps {
  ticker: Ticker | null
  title?: string
}

export function TickerPanel({ ticker, title }: TickerPanelProps) {
  return (
    <div>
      <h3 className="text-base font-semibold mb-3">{title ?? "行情"}</h3>
      {!ticker ? (
        <div className="space-y-3">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-20" />
        </div>
      ) : (
        <div className="space-y-3">
          <div className="text-2xl font-bold font-mono">
            ${ticker.last_price?.toLocaleString()}
          </div>
          <div className="flex items-center gap-2">
            {(ticker.change_pct_24h ?? 0) >= 0 ? (
              <TrendingUp className="h-4 w-4 text-emerald-400" />
            ) : (
              <TrendingDown className="h-4 w-4 text-red-400" />
            )}
            <Badge
              variant={
                (ticker.change_pct_24h ?? 0) >= 0 ? "default" : "destructive"
              }
            >
              {(ticker.change_pct_24h ?? 0) >= 0 ? "+" : ""}
              {(ticker.change_pct_24h ?? 0).toFixed(2)}%
            </Badge>
          </div>
          <div className="space-y-1 text-xs text-muted-foreground">
            <div className="flex justify-between">
              <span>24h 高</span>
              <span className="text-foreground font-mono">
                {ticker.bid ? `$${ticker.bid.toLocaleString()}` : "--"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>24h 低</span>
              <span className="text-foreground font-mono">
                {ticker.ask ? `$${ticker.ask.toLocaleString()}` : "--"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>24h 成交量</span>
              <span className="text-foreground font-mono">
                {ticker.quote_volume_24h
                  ? `${ticker.quote_volume_24h.toLocaleString(undefined, { maximumFractionDigits: 0 })} USDT`
                  : `${ticker.volume_24h?.toLocaleString()} (Base)`}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
