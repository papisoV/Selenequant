"use client"

import React, { useEffect, useRef, useState, useMemo, useCallback } from "react"
import { createChart, ColorType, IChartApi, ISeriesApi, CandlestickSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle, createSeriesMarkers } from "lightweight-charts"
import type { Kline } from "@/lib/types/backend"
import { cn } from "@/lib/utils"
import { nearestSorted, withinRange } from "@/lib/chart/time-cursor"
import { useUiText } from "@/components/i18n/use-ui-text"

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

/** 新数据到达时的视口处置方式。 */
export type ViewportAction =
  | { kind: "incremental" } // 同 bar 覆盖 / 末根追加:只 update 最后一根,视口不动
  | { kind: "preserve"; shift: number } // 向前追加历史:整段平移,保持当前看的位置
  | { kind: "fit" } // 全新数据集(首帧 / 换窗口 / 换周期):收全量进视口

/**
 * 判断一份新数据该怎么处置视口。纯函数 —— 曾经这里把"全新数据集"也钉死在
 * 末 120 根,导致全景取回整个回测区间却只显示最后一小段("取了数却看不见")。
 *
 * @param prevLen  上一份数据的根数(0 = 首帧)
 * @param prevFirstTs / prevLastTs 上一份数据的首末时间戳
 * @param nextLen / nextFirstTs / nextLastTs 新数据的对应值
 */
export function classifyDataChange(
  prevLen: number,
  prevFirstTs: number,
  prevLastTs: number,
  nextLen: number,
  nextFirstTs: number,
  nextLastTs: number,
): ViewportAction {
  if (prevLen <= 0 || nextLen <= 0) return { kind: "fit" }

  // 末根追加或同 bar 覆盖,且首根没变 → 只更新最后一根
  if (
    (nextLen === prevLen || nextLen === prevLen + 1) &&
    nextFirstTs === prevFirstTs
  ) {
    return { kind: "incremental" }
  }

  // 向前追加历史:根数变多、首根前移、末根不动 → 平移视口
  if (nextLen > prevLen && nextFirstTs !== prevFirstTs && nextLastTs === prevLastTs) {
    return { kind: "preserve", shift: nextLen - prevLen }
  }

  // 其余(换窗口 / 换周期 / 首根与末根都变了)→ 全新数据集,收全量
  return { kind: "fit" }
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
  cursorMs,
  onCursorChange,
  onVisibleRangeChange,
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
  /**
   * 外部(权益图)传进来的共享时间游标(ms)。null = 不显示。
   * 本图数据不含该时刻时**不吸附到边缘**——落区外直接不画,不谎报位置。
   */
  cursorMs?: number | null
  /** 本图悬停 → 上报时间(ms);鼠标移出 → null。供对侧图表同步。 */
  onCursorChange?: (ms: number | null) => void
  /** 视口落定 → 上报可见时间跨度(ms)。语义量,供验收/调试读取。 */
  onVisibleRangeChange?: (range: { from: number; to: number }) => void
}) {
  const tr = useUiText()

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

  // 共享时间游标:外部塞进来的值 + 本图正在上报的回声抑制位。
  // 抑制位按**值**比较(不是布尔),所以即使某次 setCrosshairPosition 没触发回调、
  // 标记"卡住",也不会吞掉后续真实上报 —— 只多一点容差内不比较的成本。
  const appliedCursorMsRef = useRef<number | null>(null)
  // 程序化 setCrosshairPosition 落到的那根 bar 的 ms。回调里出现同值 = 回声,
  // 不回灌给对侧(否则会把自己的吸附位置顶回来源图,画出一条偏离鼠标的游标)。
  const appliedSnappedMsRef = useRef<number | null>(null)
  const onCursorChangeRef = useRef(onCursorChange)
  useLatestRef(onCursorChangeRef, onCursorChange)
  const onVisibleRangeChangeRef = useRef(onVisibleRangeChange)
  useLatestRef(onVisibleRangeChangeRef, onVisibleRangeChange)

  const _tzOffsetSec = -(new Date().getTimezoneOffset() * 60)

  /** 真实 ms → 本图时间轴(秒 + tz 偏移)。 */
  const msToChartTime = useCallback(
    (ms: number) => (Math.floor(ms / 1000) + _tzOffsetSec) as any,
    [_tzOffsetSec],
  )
  /** 本图时间轴 → 真实 ms。 */
  const chartTimeToMs = useCallback(
    (t: number) => (t - _tzOffsetSec) * 1000,
    [_tzOffsetSec],
  )
  // 图表初始化 effect 是 [] 依赖(一次性建图),拿不到每次渲染的新闭包 —— 用 ref 传。
  const chartTimeToMsRef = useRef(chartTimeToMs)
  useLatestRef(chartTimeToMsRef, chartTimeToMs)

  // 本图已上屏数据的时间轴(秒,升序)与其收盘价 —— 供外部游标吸附与取值。
  // setCrosshairPosition 需要 (price, time) 两个参数,而外部只给了时间,
  // 价格由这里按时间查得;查不到(不在本图范围)就不画。
  const chartTimesRef = useRef<number[]>([])
  const chartCloseRef = useRef<Map<number, number>>(new Map())

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
        // 鼠标移出 → 通知对侧清游标(同值重报由消费方自行去重)
        onCursorChangeRef.current?.(null)
        appliedCursorMsRef.current = null
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
        // 上报真实 ms 作共享游标。
        // 若 param.time 正是外部灌进来时落的那根 bar,这是程序化回声,不回灌 ——
        // 否则来源图会跟着这条回流改变自己的游标位置。
        const ms = chartTimeToMsRef.current(param.time as number)
        const isEcho = appliedSnappedMsRef.current != null && ms === appliedSnappedMsRef.current
        appliedSnappedMsRef.current = null
        if (!isEcho) {
          appliedCursorMsRef.current = ms
          onCursorChangeRef.current?.(ms)
        }
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
      // 视口落定 → 上报真实跨度。必须走这个订阅,不能在 fitContent() 之后同步
      // getVisibleRange():那次调用读到的是**变动前**的范围(视口切换尚未应用),
      // 会报出一个与数据长度无关的假跨度。订阅在范围真正生效时回调,才是真值。
      if (onVisibleRangeChangeRef.current) {
        try {
          const vr = chartRef.current?.timeScale().getVisibleRange()
          if (vr) onVisibleRangeChangeRef.current({ from: vr.from as number, to: vr.to as number })
        } catch { /* ignore */ }
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
    const [prevFirstTs, prevLastTs] = prevHash
      ? prevHash.split(":").slice(1).map(Number)
      : [0, 0]

    prevDataHashRef.current = dataHash
    prevDataLenRef.current = newLen

    // 视口处置由纯函数裁决(见 classifyDataChange),可单测。
    const action = classifyDataChange(oldLen, prevFirstTs, prevLastTs, newLen, firstTs, lastTs)

    if (action.kind === "incremental" && newLen > 0) {
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
      // 同步游标吸附索引:同 bar 覆盖 / 新 bar 追加 两种情况
      const times = chartTimesRef.current
      if (times.length > 0 && times[times.length - 1] === t) {
        chartCloseRef.current.set(t, last.close)
      } else {
        chartTimesRef.current = [...times, t]
        chartCloseRef.current.set(t, last.close)
      }
      return
    }

    let savedRange: { from: number; to: number } | null = null
    if (action.kind === "preserve") {
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

    // 供外部游标吸附:上屏时间轴(升序)与收盘价索引。
    const times = candleData.map((d) => d.time as number)
    const closeByTime = new Map<number, number>()
    for (let i = 0; i < times.length; i++) closeByTime.set(times[i], data[i].close)
    chartTimesRef.current = times
    chartCloseRef.current = closeByTime

    if (action.kind === "preserve" && savedRange) {
      try {
        chartRef.current.timeScale().setVisibleLogicalRange({
          from: savedRange.from + action.shift,
          to: savedRange.to + action.shift,
        })
      } catch { /* ignore */ }
      return
    }

    // 全新数据集(首帧 / 换窗口 / 换周期)→ 收全量进视口。
    // 旧实现把视口钉死在末 min(newLen,120) 根:全景模式取回了整个回测区间的 K 线,
    // 却只显示最后一小段 —— "取了数却看不见"就是这一步造成的。
    // 视口跨度由 subscribeVisibleLogicalRangeChange 在范围生效后上报,此处不读。
    try {
      chartRef.current.timeScale().fitContent()
    } catch { /* ignore */ }
    // 视口落定后上报真实跨度(验收探针读的语义量;不读像素)
    if (onVisibleRangeChangeRef.current) {
      try {
        const vr = chartRef.current.timeScale().getVisibleRange()
        if (vr) onVisibleRangeChangeRef.current({ from: vr.from as number, to: vr.to as number })
      } catch { /* ignore */ }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  // 外部共享游标 → 本图画竖线。
  // 只对**外部来源**的值动手:本图自身悬停上报时已把值记进 appliedCursorMsRef,
  // 对侧回灌过来的同一值按值跳过 —— 否则两边会互相 setCrosshairPosition,
  // 触发对方回调,形成回灌环。
  useEffect(() => {
    if (!chartRef.current || !seriesRef.current) return
    const ms = cursorMs ?? null
    if (ms === appliedCursorMsRef.current) return

    if (ms == null) {
      try { chartRef.current.clearCrosshairPosition() } catch { /* ignore */ }
      appliedCursorMsRef.current = null
      return
    }

    const times = chartTimesRef.current
    // 落区外直接不画:聚焦模式只装一笔交易的窗口,权益图悬停到窗口外时
    // 吸附到边缘会谎报时间位置(把"这里没有行情"画成"最边上那根")。
    if (!withinRange(times, msToChartTime(ms))) {
      try { chartRef.current.clearCrosshairPosition() } catch { /* ignore */ }
      appliedCursorMsRef.current = ms
      return
    }

    const snapped = nearestSorted(times, msToChartTime(ms))
    const price = snapped == null ? undefined : chartCloseRef.current.get(snapped)
    if (snapped == null || price === undefined) return
    try {
      // 先记回声指纹再落游标:setCrosshairPosition 会**同步**触发上面那个
      // subscribeCrosshairMove 回调,指纹必须先就位。
      appliedSnappedMsRef.current = chartTimeToMs(snapped)
      chartRef.current.setCrosshairPosition(price, snapped as any, seriesRef.current)
      appliedCursorMsRef.current = ms
    } catch { /* ignore */ }
  }, [cursorMs, data, msToChartTime, chartTimeToMs])

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
          <span className="text-muted-foreground">{tr("载入行情中...")}</span>
        )}
      </div>

      <div ref={chartContainerRef} className="flex-1 w-full z-[2]" />

      {isLoadingMore && (
        <div className="absolute top-10 left-2 bg-background/80 text-[10px] text-muted-foreground px-1.5 py-0.5 rounded border border-white/10 z-10">
          {tr("加载历史数据...")}
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
            {tooltipData.side === "long" ? tr("做多") : tr("做空")} {symbol || ""}
          </div>
          <div className="space-y-1 text-xs">
            <div className="flex justify-between">
              <span className="text-muted-foreground">{tr("入场")}</span>
              <span className="tabular-nums">{tooltipData.entryPrice.toLocaleString()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">{tr("出场")}</span>
              <span className="tabular-nums">{tooltipData.exitPrice.toLocaleString()}</span>
            </div>
            <div className="border-t border-border/50 my-1" />
            <div className="flex justify-between text-red-400">
              <span>{tr("止损")}</span>
              <span className="tabular-nums">{tooltipData.stopLoss.toLocaleString()} ({((tooltipData.stopLoss - tooltipData.entryPrice) / tooltipData.entryPrice * 100).toFixed(1)}%)</span>
            </div>
            <div className="flex justify-between text-emerald-400">
              <span>{tr("止盈")}</span>
              <span className="tabular-nums">{tooltipData.takeProfit.toLocaleString()} ({((tooltipData.takeProfit - tooltipData.entryPrice) / tooltipData.entryPrice * 100).toFixed(1)}%)</span>
            </div>
            <div className="border-t border-border/50 my-1" />
            <div className="flex justify-between">
              <span className="text-muted-foreground">{tr("持仓")}</span>
              <span className="tabular-nums">{formatDuration(tooltipData.exitTime - tooltipData.entryTime)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">{tr("盈亏")}</span>
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
