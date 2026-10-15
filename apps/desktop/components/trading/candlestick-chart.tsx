"use client"

import React, { useEffect, useRef, useState, useMemo, useCallback } from "react"
import { createChart, ColorType, IChartApi, ISeriesApi, CandlestickSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle, createSeriesMarkers } from "lightweight-charts"
import type { Kline } from "@/lib/types/backend"
import { cn } from "@/lib/utils"

export interface TradeMarker {
  time: number
  position: "aboveBar" | "belowBar" | "inBar" | "atPriceTop" | "atPriceBottom" | "atPriceMiddle"
  color: string
  shape: "arrowUp" | "arrowDown" | "circle" | "square"
  text?: string
  tradeId?: string
  price?: number
  id?: string
  size?: number
}

export interface TradeTooltipData {
  tradeId: string
  side: "long" | "short"
  entryTime: number
  exitTime: number
  entryPrice: number
  exitPrice: number
  stopLoss: number
  takeProfit: number
  pnl: number
  pnlPct: number
  quantity: number
  exitReason?: string
  x: number
  y: number
}

export interface TradePriceLine {
  tradeId: string
  entryTime: number
  exitTime: number
  stopLoss: number
  takeProfit: number
  side: "long" | "short"
  entryPrice: number
  exitPrice: number
}

interface HoverData {
  time: string | number
  open: number
  high: number
  low: number
  close: number
}

/** Kline → OHLC 展示形态(time 缺省 0)。纯函数。 */
export function candleToOHLC(c: {
  timestamp?: number
  open: number
  high: number
  low: number
  close: number
}) {
  return { time: c.timestamp ?? 0, open: c.open, high: c.high, low: c.low, close: c.close }
}

/** 把 ref.current 同步为最新值。 */
export function useLatestRef<T>(ref: { current: T }, value: T): void {
  useEffect(() => {
    ref.current = value
  }, [ref, value])
}

export default function CandlestickChart({
  data,
  interval,
  onLoadMore,
  isLoadingMore,
  markers,
  onMarkerClick,
  tradeTooltips,
  tradePriceLines,
  symbol,
}: {
  data: Kline[]
  interval: string
  onLoadMore?: (oldestTimestamp: number) => void
  isLoadingMore?: boolean
  markers?: TradeMarker[]
  onMarkerClick?: (marker: TradeMarker, x: number, y: number) => void
  tradeTooltips?: Record<string, TradeTooltipData>
  tradePriceLines?: TradePriceLine[]
  symbol?: string
}) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null)
  const markersApiRef = useRef<any>(null)
  const markersDataRef = useRef<TradeMarker[]>([])
  const loadingMoreRef = useRef(false)
  const dataLenRef = useRef(0)
  const prevDataLenRef = useRef(0)
  const prevDataHashRef = useRef("")
  const priceLineRefs = useRef<import("lightweight-charts").IPriceLine[]>([])
  const tradeLineRef = useRef<ISeriesApi<"Line"> | null>(null)
  const activeTradeIdRef = useRef<string | null>(null)

  const [hoverData, setHoverData] = useState<HoverData | null>(null)
  const [tooltipData, setTooltipData] = useState<TradeTooltipData | null>(null)
  const tradeTooltipsRef = useRef<Record<string, TradeTooltipData> | undefined>(undefined)
  const tradePriceLinesRef = useRef<TradePriceLine[] | undefined>(undefined)

  const _tzOffsetSec = -(new Date().getTimezoneOffset() * 60)

  useEffect(() => { tradeTooltipsRef.current = tradeTooltips }, [tradeTooltips])
  useEffect(() => { tradePriceLinesRef.current = tradePriceLines }, [tradePriceLines])

  const lastCandle = useMemo(() => {
    if (data.length === 0) return null
    return data[data.length - 1]
  }, [data])

  const clearTradeAnnotations = useCallback((resetActiveId = true) => {
    if (resetActiveId) activeTradeIdRef.current = null
    const series = seriesRef.current
    if (series) {
      for (const pl of priceLineRefs.current) {
        try { series.removePriceLine(pl) } catch { /* ignore */ }
      }
      priceLineRefs.current = []
    }
    if (tradeLineRef.current && chartRef.current) {
      try { chartRef.current.removeSeries(tradeLineRef.current) } catch { /* ignore */ }
      tradeLineRef.current = null
    }
  }, [])

  const showTradeAnnotations = useCallback((tradeId: string) => {
    if (activeTradeIdRef.current === tradeId) return
    activeTradeIdRef.current = tradeId

    const tooltip = tradeTooltipsRef.current?.[tradeId]
    const priceLineData = tradePriceLinesRef.current?.find((p) => p.tradeId === tradeId)
    if (!tooltip || !seriesRef.current || !chartRef.current) return

    clearTradeAnnotations(false)

    const series = seriesRef.current

    const slLine = series.createPriceLine({
      price: tooltip.stopLoss,
      color: "#EF5350",
      lineStyle: LineStyle.Dashed,
      lineWidth: 1,
      axisLabelVisible: true,
      title: `SL ${tooltip.stopLoss.toLocaleString()}`,
    })
    priceLineRefs.current.push(slLine)

    const tpLine = series.createPriceLine({
      price: tooltip.takeProfit,
      color: "#10b981",
      lineStyle: LineStyle.Dashed,
      lineWidth: 1,
      axisLabelVisible: true,
      title: `TP ${tooltip.takeProfit.toLocaleString()}`,
    })
    priceLineRefs.current.push(tpLine)

    if (priceLineData) {
      const isLong = tooltip.side === "long"
      const lineColor = isLong ? "#10b981" : "#EF5350"
      const tradeLine = chartRef.current.addSeries(LineSeries, {
        color: lineColor,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      tradeLine.setData([
        { time: (Math.floor(priceLineData.entryTime / 1000) + _tzOffsetSec) as any, value: priceLineData.entryPrice },
        { time: (Math.floor(priceLineData.exitTime / 1000) + _tzOffsetSec) as any, value: priceLineData.exitPrice },
      ])
      tradeLineRef.current = tradeLine
    }
  }, [clearTradeAnnotations])

  const clearTradeAnnotationsRef = useRef(clearTradeAnnotations)
  const showTradeAnnotationsRef = useRef(showTradeAnnotations)
  useEffect(() => { clearTradeAnnotationsRef.current = clearTradeAnnotations }, [clearTradeAnnotations])
  useEffect(() => { showTradeAnnotationsRef.current = showTradeAnnotations }, [showTradeAnnotations])

  const displayData = hoverData || (lastCandle ? candleToOHLC(lastCandle) : null)

  useLatestRef(loadingMoreRef, !!isLoadingMore)
  useLatestRef(dataLenRef, data.length)

  const onLoadMoreRef = useRef(onLoadMore)
  useLatestRef(onLoadMoreRef, onLoadMore)

  useEffect(() => {
    if (!chartContainerRef.current) return

    const handleResize = () => {
      if (chartRef.current && chartContainerRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        })
      }
    }

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "rgba(255, 255, 255, 0.5)",
        fontFamily: "Inter, system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 0.05)" },
        horzLines: { color: "rgba(255, 255, 255, 0.05)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: "rgba(255, 255, 255, 0.4)",
          style: 2,
          labelBackgroundColor: "#1e222d",
        },
        horzLine: {
          color: "rgba(255, 255, 255, 0.4)",
          style: 2,
          labelBackgroundColor: "#1e222d",
        },
      },
      rightPriceScale: {
        borderColor: "rgba(255, 255, 255, 0.1)",
        autoScale: true,
      },
      timeScale: {
        borderColor: "rgba(255, 255, 255, 0.1)",
        timeVisible: true,
        secondsVisible: false,
      },
      localization: {
        timeFormatter: (time: number) => {
          const d = new Date(time * 1000)
          const month = d.toLocaleString("en", { month: "short", timeZone: "UTC" })
          const day = d.getUTCDate()
          const hh = String(d.getUTCHours()).padStart(2, "0")
          const mm = String(d.getUTCMinutes()).padStart(2, "0")
          return `${month} ${day}, ${hh}:${mm}`
        },
      },
    })

    try {
      chart.applyOptions({
        ...({ enableConflation: true, precomputeConflationOnInit: true } as any),
      })
    } catch {
      // Graceful fallback if conflation not supported
    }

    chartRef.current = chart

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#EF5350",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#EF5350",
    })
    seriesRef.current = candlestickSeries as any

    markersApiRef.current = createSeriesMarkers(candlestickSeries, [])

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    })
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })
    volumeRef.current = volumeSeries

    chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || param.point === undefined || !seriesRef.current) {
        setHoverData(null)
        clearTradeAnnotationsRef.current()
        return
      }
      const candle = param.seriesData.get(seriesRef.current) as any
      if (candle) {
        setHoverData({
          time: param.time as any,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        })
      } else {
        setHoverData(null)
      }

      const currentMarkers = markersDataRef.current
      if (!currentMarkers || currentMarkers.length === 0) {
        clearTradeAnnotationsRef.current()
        return
      }

      const clickTime = param.time as number
      const matchedMarker = currentMarkers.find((m) => {
        const markerTime = typeof m.time === "number" ? m.time : 0
        return Math.abs(markerTime - clickTime) < 2 && m.tradeId
      })

      if (matchedMarker && matchedMarker.tradeId) {
        const tooltip = tradeTooltipsRef.current?.[matchedMarker.tradeId]
        if (tooltip) {
          setTooltipData({ ...tooltip, x: param.point.x, y: param.point.y })
          showTradeAnnotationsRef.current(matchedMarker.tradeId)
        }
      } else {
        setTooltipData(null)
        clearTradeAnnotationsRef.current()
      }
    })

    chart.timeScale().subscribeVisibleLogicalRangeChange((logicalRange) => {
      if (!logicalRange) return
      if (logicalRange.from < 5 && !loadingMoreRef.current && onLoadMoreRef.current && dataLenRef.current > 0) {
        loadingMoreRef.current = true
        onLoadMoreRef.current(-1)
      }
    })

    if (onMarkerClick) {
      chart.subscribeClick((param) => {
        if (!param || param.point === undefined) return
        const markersData = markersDataRef.current
        if (!markersData || markersData.length === 0) return

        const clickTime = param.time
        if (!clickTime) return

        for (const marker of markersData) {
          const markerTime = marker.time
          const timeDiff = Math.abs(typeof markerTime === 'number' ? markerTime - (clickTime as number) : 0)
          if (timeDiff < 1) {
            const markerWithId = markers?.find(m => m.time === markerTime && m.position === marker.position)
            if (markerWithId?.tradeId) {
              const tooltip = tradeTooltipsRef.current?.[markerWithId.tradeId]
              if (tooltip) {
                setTooltipData({ ...tooltip, x: param.point.x, y: param.point.y })
                showTradeAnnotationsRef.current(markerWithId.tradeId)
              } else {
                onMarkerClick(markerWithId, param.point.x, param.point.y)
              }
            }
            break
          }
        }
      })
    }

    const ro = new ResizeObserver(handleResize)
    ro.observe(chartContainerRef.current)
    window.addEventListener("resize", handleResize)
    return () => {
      ro.disconnect()
      window.removeEventListener("resize", handleResize)
      for (const pl of priceLineRefs.current) {
        try { seriesRef.current?.removePriceLine(pl) } catch { /* ignore */ }
      }
      priceLineRefs.current = []
      if (tradeLineRef.current) {
        try { chart.removeSeries(tradeLineRef.current) } catch { /* ignore */ }
        tradeLineRef.current = null
      }
      markersApiRef.current = null
      markersDataRef.current = []
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      volumeRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!seriesRef.current || !volumeRef.current || !chartRef.current) return
    if (data.length === 0) return

    const lastTs = data.length > 0 ? (data[data.length - 1].timestamp ?? 0) : 0
    const firstTs = data.length > 0 ? (data[0].timestamp ?? 0) : 0
    const dataHash = `${data.length}:${firstTs}:${lastTs}`
    const prevHash = prevDataHashRef.current
    const oldLen = prevDataLenRef.current
    const newLen = data.length

    prevDataHashRef.current = dataHash
    prevDataLenRef.current = newLen

    const isIncrementalUpdate =
      oldLen > 0 &&
      (newLen === oldLen || newLen === oldLen + 1) &&
      prevHash.split(":")[1] === dataHash.split(":")[1]

    if (isIncrementalUpdate && newLen > 0) {
      const last = data[newLen - 1]
      const t = (Math.floor((last.timestamp ?? 0) / 1000) + _tzOffsetSec) as any
      seriesRef.current.update({
        time: t,
        open: last.open,
        high: last.high,
        low: last.low,
        close: last.close,
      })
      volumeRef.current.update({
        time: t,
        value: last.volume,
        color: last.close >= last.open ? "rgba(16, 185, 129, 0.4)" : "rgba(239, 83, 80, 0.4)",
      })
      return
    }

    const isHistoryAppend =
      oldLen > 0 &&
      newLen > oldLen &&
      firstTs !== Number(prevHash.split(":")[1]) &&
      lastTs === Number(prevHash.split(":")[2])

    let savedRange: { from: number; to: number } | null = null
    if (isHistoryAppend) {
      try {
        savedRange = chartRef.current.timeScale().getVisibleLogicalRange()
      } catch { /* ignore */ }
    }

    const candleData = data.map((d) => ({
      time: (Math.floor((d.timestamp ?? 0) / 1000) + _tzOffsetSec) as any,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }))

    const volData = data.map((d) => ({
      time: (Math.floor((d.timestamp ?? 0) / 1000) + _tzOffsetSec) as any,
      value: d.volume,
      color: d.close >= d.open ? "rgba(16, 185, 129, 0.4)" : "rgba(239, 83, 80, 0.4)",
    }))

    seriesRef.current.setData(candleData)
    volumeRef.current.setData(volData)

    if (isHistoryAppend && savedRange) {
      const addedCount = newLen - oldLen
      try {
        chartRef.current.timeScale().setVisibleLogicalRange({
          from: savedRange.from + addedCount,
          to: savedRange.to + addedCount,
        })
      } catch { /* ignore */ }
    }

    if (oldLen < 30 && newLen >= 30) {
      const barsToShow = Math.min(newLen, 120)
      chartRef.current.timeScale().setVisibleLogicalRange({
        from: newLen - barsToShow,
        to: newLen + 10,
      })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  useEffect(() => {
    if (!markersApiRef.current) return
    if (!markers || markers.length === 0) {
      markersApiRef.current.setMarkers([])
      markersDataRef.current = []
      return
    }
    const sorted = [...markers]
      .sort((a, b) => a.time - b.time)
      .map((m) => ({
        time: m.time as any,
        position: m.position,
        color: m.color,
        shape: m.shape,
        text: m.text,
        size: m.size ?? 2,
        price: m.price,
        id: m.id,
      }))
    markersApiRef.current.setMarkers(sorted as any)
    markersDataRef.current = markers
  }, [markers])

  const change = displayData ? displayData.close - displayData.open : 0
  const changePct = displayData ? (change / displayData.open) * 100 : 0

  return (
    <div className="relative w-full h-full flex flex-col overflow-hidden">
      <div className="absolute top-2 left-2 z-10 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-mono pointer-events-none bg-background/40 backdrop-blur-sm px-2 py-1 rounded">
        {displayData ? (
          <>
            <div className="flex items-center gap-1">
              <span className="text-muted-foreground">O</span>
              <span className={cn(change >= 0 ? "text-[#10b981]" : "text-[#EF5350]")}>{displayData.open.toLocaleString()}</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-muted-foreground">H</span>
              <span className={cn(change >= 0 ? "text-[#10b981]" : "text-[#EF5350]")}>{displayData.high.toLocaleString()}</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-muted-foreground">L</span>
              <span className={cn(change >= 0 ? "text-[#10b981]" : "text-[#EF5350]")}>{displayData.low.toLocaleString()}</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-muted-foreground">C</span>
              <span className={cn(change >= 0 ? "text-[#10b981]" : "text-[#EF5350]")}>{displayData.close.toLocaleString()}</span>
            </div>
            <div className="flex items-center gap-1 ml-2">
              <span className={cn("font-bold px-1 rounded", change >= 0 ? "bg-[#10b981]/10 text-[#10b981]" : "bg-[#EF5350]/10 text-[#EF5350]")}>
                {change >= 0 ? "+" : ""}{change.toFixed(2)} ({changePct.toFixed(2)}%)
              </span>
            </div>
          </>
        ) : (
          <span className="text-muted-foreground">载入行情中...</span>
        )}
      </div>

      <div ref={chartContainerRef} className="flex-1 w-full z-[2]" />

      {isLoadingMore && (
        <div className="absolute top-10 left-2 bg-background/80 text-[10px] text-muted-foreground px-1.5 py-0.5 rounded border border-white/10 z-10">
          加载历史数据...
        </div>
      )}

      {tooltipData && (
        <div
          className="absolute z-50 bg-card/95 border border-border/80 rounded-lg shadow-xl p-3 min-w-[200px] max-w-[280px]"
          style={{
            left: tooltipData.x > window.innerWidth / 2 ? tooltipData.x - 220 : tooltipData.x + 20,
            top: tooltipData.y > window.innerHeight / 2 ? tooltipData.y - 280 : tooltipData.y + 20,
          }}
        >
          <div className={cn("text-sm font-semibold mb-2", tooltipData.side === "long" ? "text-emerald-400" : "text-red-400")}>
            {tooltipData.side === "long" ? "做多" : "做空"} {symbol || ""}
          </div>
          <div className="space-y-1 text-xs">
            <div className="flex justify-between">
              <span className="text-muted-foreground">入场</span>
              <span className="tabular-nums">{tooltipData.entryPrice.toLocaleString()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">出场</span>
              <span className="tabular-nums">{tooltipData.exitPrice.toLocaleString()}</span>
            </div>
            <div className="border-t border-border/50 my-1" />
            <div className="flex justify-between text-red-400">
              <span>止损</span>
              <span className="tabular-nums">{tooltipData.stopLoss.toLocaleString()} ({((tooltipData.stopLoss - tooltipData.entryPrice) / tooltipData.entryPrice * 100).toFixed(1)}%)</span>
            </div>
            <div className="flex justify-between text-emerald-400">
              <span>止盈</span>
              <span className="tabular-nums">{tooltipData.takeProfit.toLocaleString()} ({((tooltipData.takeProfit - tooltipData.entryPrice) / tooltipData.entryPrice * 100).toFixed(1)}%)</span>
            </div>
            <div className="border-t border-border/50 my-1" />
            <div className="flex justify-between">
              <span className="text-muted-foreground">持仓</span>
              <span className="tabular-nums">{formatDuration(tooltipData.exitTime - tooltipData.entryTime)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">盈亏</span>
              <span className={cn("tabular-nums font-semibold", tooltipData.pnl >= 0 ? "text-emerald-400" : "text-red-400")}>
                {tooltipData.pnl >= 0 ? "+" : ""}{tooltipData.pnl.toFixed(2)} ({tooltipData.pnlPct >= 0 ? "+" : ""}{tooltipData.pnlPct.toFixed(2)}%)
              </span>
            </div>
          </div>
          <button
            className="absolute top-1 right-1 w-5 h-5 flex items-center justify-center text-muted-foreground hover:text-foreground"
            onClick={() => { setTooltipData(null); clearTradeAnnotationsRef.current() }}
          >
            ×
          </button>
        </div>
      )}
    </div>
  )
}

function formatDuration(ms: number): string {
  const hours = Math.floor(ms / 3_600_000)
  const mins = Math.floor((ms % 3_600_000) / 60_000)
  if (hours > 24) {
    const days = Math.floor(hours / 24)
    return `${days}d ${hours % 24}h`
  }
  if (hours > 0) return `${hours}h ${mins}m`
  return `${mins}m`
}
