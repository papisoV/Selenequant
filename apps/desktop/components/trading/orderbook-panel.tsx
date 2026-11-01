"use client"

import React, { useMemo, useState, useCallback, useRef } from "react"
import { createPortal } from "react-dom"
import { Skeleton } from "@/components/ui/skeleton"
import type { OrderBook } from "@/lib/types/backend"
import { useUiText } from "@/components/i18n/use-ui-text"

function formatPrice(price: number): string {
  if (price >= 10000) return price.toFixed(1)
  if (price >= 100) return price.toFixed(2)
  if (price >= 1) return price.toFixed(3)
  if (price >= 0.01) return price.toFixed(4)
  return price.toFixed(6)
}

function formatQty(qty: number): string {
  if (qty >= 1000) return qty.toFixed(2)
  if (qty >= 1) return qty.toFixed(4)
  return qty.toFixed(5)
}

function formatUsdt(val: number): string {
  if (val >= 1_000_000) return (val / 1_000_000).toFixed(2) + "M"
  if (val >= 10_000) return (val / 1_000).toFixed(1) + "K"
  if (val >= 1_000) return (val / 1_000).toFixed(2) + "K"
  if (val >= 1) return val.toFixed(2)
  return val.toFixed(4)
}

interface OrderbookPanelProps {
  data: OrderBook | null
  maxRows?: number
  title?: string
  baseCurrency?: string
  quoteCurrency?: string
  onPriceClick?: (price: number) => void
}

export interface DisplayRow {
  price: number
  qty: number
  levelUsdt: number
  cumUsdt: number
  cumQty: number
}

function buildDisplayRows(
  levels: { price: number; quantity: number }[],
  max: number,
): DisplayRow[] {
  const rows: DisplayRow[] = []
  let cumUsdt = 0
  let cumQty = 0
  for (let i = 0; i < Math.min(levels.length, max); i++) {
    const { price, quantity } = levels[i]
    const levelUsdt = price * quantity
    cumUsdt += levelUsdt
    cumQty += quantity
    rows.push({ price, qty: quantity, levelUsdt, cumUsdt, cumQty })
  }
  return rows
}

type HoverState = { side: "ask" | "bid"; displayIndex: number } | null

/**
 * 由悬停位置算出该档的均价/档内额/累计额。纯函数。
 * - ask: 从该档累加到末档(卖盘向下吃单)
 * - bid: 直接用该档已算好的累计
 */
export function computeHoverInfo(
  hover: HoverState,
  askRows: DisplayRow[],
  bidRows: DisplayRow[],
): { avgPrice: number; levelUsdt: number; cumUsdt: number } | null {
  if (!hover) return null
  if (hover.side === "ask") {
    const row = askRows[hover.displayIndex]
    if (!row) return null
    let cumUsdt = 0
    let cumQty = 0
    for (let i = hover.displayIndex; i < askRows.length; i++) {
      cumUsdt += askRows[i].levelUsdt
      cumQty += askRows[i].qty
    }
    return {
      avgPrice: cumQty > 0 ? cumUsdt / cumQty : row.price,
      levelUsdt: row.levelUsdt,
      cumUsdt,
    }
  }
  const row = bidRows[hover.displayIndex]
  if (!row) return null
  return {
    avgPrice: row.cumQty > 0 ? row.cumUsdt / row.cumQty : row.price,
    levelUsdt: row.levelUsdt,
    cumUsdt: row.cumUsdt,
  }
}

export function OrderbookPanel({
  data,
  maxRows = 8,
  title,
  baseCurrency,
  quoteCurrency = "USDT",
  onPriceClick,
}: OrderbookPanelProps) {
  const tr = useUiText()

  const [hover, setHover] = useState<HoverState>(null)
  const [tipPos, setTipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  const containerRef = useRef<HTMLDivElement>(null)

  const { askDisplayRows, bidDisplayRows, maxLevelUsdt } = useMemo(() => {
    if (!data) return { askDisplayRows: [], bidDisplayRows: [], maxLevelUsdt: 1 }

    const bidRows = buildDisplayRows(data.bids, maxRows)
    const askRows = buildDisplayRows(data.asks, maxRows)
    const askDisplay = [...askRows].reverse()

    const maxLvl = Math.max(
      ...bidRows.map((r) => r.levelUsdt),
      ...askRows.map((r) => r.levelUsdt),
      1,
    )
    return { askDisplayRows: askDisplay, bidDisplayRows: bidRows, maxLevelUsdt: maxLvl }
  }, [data, maxRows])

  const clearHover = useCallback(() => setHover(null), [])

  const handleRowHover = useCallback(
    (side: "ask" | "bid", displayIndex: number, e: React.MouseEvent) => {
      setHover({ side, displayIndex })
      const rowRect = (e.currentTarget as HTMLElement).getBoundingClientRect()
      setTipPos({
        x: rowRect.left,
        y: rowRect.top + rowRect.height / 2,
      })
    },
    [],
  )

  const handleRowClick = useCallback(
    (price: number) => {
      if (onPriceClick) {
        onPriceClick(price)
      }
      window.dispatchEvent(new CustomEvent("trade-fill-price", { detail: price }))
    },
    [onPriceClick],
  )

  const hoverInfo = useMemo(
    () => computeHoverInfo(hover, askDisplayRows, bidDisplayRows),
    [hover, askDisplayRows, bidDisplayRows],
  )

  const isAskHighlighted = useCallback(
    (i: number) => hover?.side === "ask" && i >= hover.displayIndex,
    [hover],
  )
  const isBidHighlighted = useCallback(
    (i: number) => hover?.side === "bid" && i <= hover.displayIndex,
    [hover],
  )

  const qtyLabel = baseCurrency ? `数量(${baseCurrency})` : "数量"
  const totalLabel = `总额(${quoteCurrency})`
  const priceLabel = `价格(${quoteCurrency})`

  return (
    <div ref={containerRef}>
      <h3 className="text-base font-semibold mb-3">{title ?? tr("订单簿")}</h3>
      {!data ? (
        <Skeleton className="h-48" />
      ) : (
        <div className="text-xs font-mono" onMouseLeave={clearHover}>
          <div className="grid grid-cols-3 text-muted-foreground px-1 pb-1 border-b border-border mb-0.5">
            <span>{priceLabel}</span>
            <span className="text-right">{qtyLabel}</span>
            <span className="text-right">{totalLabel}</span>
          </div>

          {askDisplayRows.map((row, i) => {
            const pct = (row.levelUsdt / maxLevelUsdt) * 100
            const highlighted = isAskHighlighted(i)
            return (
              <div
                key={`a-${i}`}
                className={`grid grid-cols-3 relative px-1 py-[3px] cursor-pointer transition-colors ${highlighted ? "bg-red-500/20" : "hover:bg-muted/30"}`}
                onMouseEnter={(e) => handleRowHover("ask", i, e)}
                onClick={() => handleRowClick(row.price)}
              >
                <div
                  className="absolute inset-y-0 right-0 bg-red-500/10 pointer-events-none"
                  style={{ width: `${pct}%` }}
                />
                <span className="text-red-400 relative z-10">{formatPrice(row.price)}</span>
                <span className="text-right relative z-10">{formatQty(row.qty)}</span>
                <span className="text-right relative z-10 text-muted-foreground">
                  {formatUsdt(row.cumUsdt)}
                </span>
              </div>
            )
          })}

          {askDisplayRows.length > 0 && bidDisplayRows.length > 0 && (
            <div className="flex items-center justify-center py-1 border-y border-border my-0.5 text-muted-foreground gap-2">
              <span className="text-sm font-semibold text-foreground">
                {formatPrice(askDisplayRows[askDisplayRows.length - 1].price)}
              </span>
              <span className="text-[10px]">
                ≈ ${askDisplayRows[askDisplayRows.length - 1].price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          )}

          {bidDisplayRows.map((row, i) => {
            const pct = (row.levelUsdt / maxLevelUsdt) * 100
            const highlighted = isBidHighlighted(i)
            return (
              <div
                key={`b-${i}`}
                className={`grid grid-cols-3 relative px-1 py-[3px] cursor-pointer transition-colors ${highlighted ? "bg-emerald-500/20" : "hover:bg-muted/30"}`}
                onMouseEnter={(e) => handleRowHover("bid", i, e)}
                onClick={() => handleRowClick(row.price)}
              >
                <div
                  className="absolute inset-y-0 right-0 bg-emerald-500/10 pointer-events-none"
                  style={{ width: `${pct}%` }}
                />
                <span className="text-emerald-400 relative z-10">{formatPrice(row.price)}</span>
                <span className="text-right relative z-10">{formatQty(row.qty)}</span>
                <span className="text-right relative z-10 text-muted-foreground">
                  {formatUsdt(row.cumUsdt)}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {hover && hoverInfo && typeof document !== "undefined" &&
        createPortal(
          <div
            className="fixed z-mock pointer-events-none"
            style={{
              left: `${tipPos.x - 8}px`,
              top: `${tipPos.y}px`,
              transform: "translate(-100%, -50%)",
            }}
          >
            <div className="bg-popover/95 backdrop-blur-sm border border-border rounded-md shadow-xl px-2.5 py-2 text-[11px] font-mono whitespace-nowrap space-y-1">
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{tr("均价")}</span>
                <span className="font-semibold">≈ {formatPrice(hoverInfo.avgPrice)}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{tr("档位")} {quoteCurrency}</span>
                <span className="font-semibold">{formatUsdt(hoverInfo.levelUsdt)}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{tr("累计")} {quoteCurrency}</span>
                <span className="font-semibold">{formatUsdt(hoverInfo.cumUsdt)}</span>
              </div>
            </div>
          </div>,
          document.body,
        )
      }
    </div>
  )
}
