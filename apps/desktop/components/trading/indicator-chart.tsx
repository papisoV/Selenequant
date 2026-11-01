"use client"

import React, { useEffect, useRef, useState, useMemo, useCallback } from "react"
import {
  createChart,
  ColorType,
  IChartApi,
  ISeriesApi,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  CrosshairMode,
} from "lightweight-charts"
import type { Kline } from "@/lib/types/backend"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Eye,
  EyeOff,
} from "lucide-react"

export function calculateSMA(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null)
    } else {
      const sum = data.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0)
      result.push(sum / period)
    }
  }
  return result
}

export function calculateEMA(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  const multiplier = 2 / (period + 1)
  let ema: number | null = null

  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null)
    } else if (i === period - 1) {
      ema = data.slice(0, period).reduce((a, b) => a + b, 0) / period
      result.push(ema)
    } else {
      ema = (data[i] - ema!) * multiplier + ema!
      result.push(ema)
    }
  }
  return result
}

export function calculateBollingerBands(
  data: number[],
  period: number,
  stdDev: number
): { upper: (number | null)[]; middle: (number | null)[]; lower: (number | null)[] } {
  const middle = calculateSMA(data, period)
  const upper: (number | null)[] = []
  const lower: (number | null)[] = []

  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      upper.push(null)
      lower.push(null)
    } else {
      const slice = data.slice(i - period + 1, i + 1)
      const mean = middle[i]!
      const variance = slice.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / period
      const std = Math.sqrt(variance)
      upper.push(mean + stdDev * std)
      lower.push(mean - stdDev * std)
    }
  }

  return { upper, middle, lower }
}

export function calculateRSI(data: number[], period: number = 14): (number | null)[] {
  const result: (number | null)[] = []
  const gains: number[] = []
  const losses: number[] = []

  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      result.push(null)
      gains.push(0)
      losses.push(0)
    } else {
      const change = data[i] - data[i - 1]
      gains.push(change > 0 ? change : 0)
      losses.push(change < 0 ? Math.abs(change) : 0)

      if (i < period) {
        result.push(null)
      } else if (i === period) {
        const avgGain = gains.slice(0, period).reduce((a, b) => a + b, 0) / period
        const avgLoss = losses.slice(0, period).reduce((a, b) => a + b, 0) / period
        if (avgLoss === 0) {
          result.push(100)
        } else {
          const rs = avgGain / avgLoss
          result.push(100 - 100 / (1 + rs))
        }
      } else {
        const avgGain = gains.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period
        const avgLoss = losses.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period
        if (avgLoss === 0) {
          result.push(100)
        } else {
          const rs = avgGain / avgLoss
          result.push(100 - 100 / (1 + rs))
        }
      }
    }
  }

  return result
}

export function calculateMACD(
  data: number[],
  fastPeriod: number = 12,
  slowPeriod: number = 26,
  signalPeriod: number = 9
): { macd: (number | null)[]; signal: (number | null)[]; histogram: (number | null)[] } {
  const fastEMA = calculateEMA(data, fastPeriod)
  const slowEMA = calculateEMA(data, slowPeriod)

  const macd: (number | null)[] = []
  for (let i = 0; i < data.length; i++) {
    if (fastEMA[i] === null || slowEMA[i] === null) {
      macd.push(null)
    } else {
      macd.push(fastEMA[i]! - slowEMA[i]!)
    }
  }

  const validMacd = macd.filter((v) => v !== null) as number[]
  const signalEMA = calculateEMA(validMacd, signalPeriod)
  const signal: (number | null)[] = []
  let validIdx = 0
  for (let i = 0; i < macd.length; i++) {
    if (macd[i] === null) {
      signal.push(null)
    } else {
      signal.push(signalEMA[validIdx] ?? null)
      validIdx++
    }
  }

  const histogram: (number | null)[] = []
  for (let i = 0; i < data.length; i++) {
    if (macd[i] === null || signal[i] === null) {
      histogram.push(null)
    } else {
      histogram.push(macd[i]! - signal[i]!)
    }
  }

  return { macd, signal, histogram }
}

/** 本地时区偏移(秒) —— 把 UTC 秒折算到本地墙钟。 */
function tzOffsetSec(): number {
  return -(new Date().getTimezoneOffset() * 60)
}

/** 单根 K 线的时间戳(秒, 含时区偏移); 缺省为 0。 */
function barTime(timestamp: number | undefined): number {
  return Math.floor((timestamp ?? 0) / 1000) + tzOffsetSec()
}

/**
 * 把 (时间数组, 值数组) 配对成 lightweight-charts 的 series 数据, 丢掉空值。纯函数。
 * time 用 any —— 库的 Time 是品牌类型, 调用处一律 `as any` 传入。
 */
export function toSeriesData(
  times: number[],
  values: (number | null | undefined)[],
): { time: any; value: number }[] {
  const out: { time: any; value: number }[] = []
  times.forEach((t, i) => {
    const v = values[i]
    if (v !== null && v !== undefined) out.push({ time: t, value: v })
  })
  return out
}

/** lightweight-charts 的轴/网格/十字线配色(多处共用)。 */
const CHART_GRID_COLOR = "rgba(255, 255, 255, 0.05)"
const CHART_BORDER_COLOR = "rgba(255, 255, 255, 0.1)"
const CHART_TEXT_COLOR = "rgba(255, 255, 255, 0.5)"
const CHART_CROSSHAIR_COLOR = "rgba(255, 255, 255, 0.4)"

/** 时间轴格式化: "Mon D, HH:MM"(UTC)。 */
function formatChartTime(time: number): string {
  const d = new Date(time * 1000)
  const month = d.toLocaleString("en", { month: "short", timeZone: "UTC" })
  const hh = String(d.getUTCHours()).padStart(2, "0")
  const mm = String(d.getUTCMinutes()).padStart(2, "0")
  return `${month} ${d.getUTCDate()}, ${hh}:${mm}`
}

/** 主图/子图共用的 createChart 基础选项。 */
function baseChartOptions(height: number) {
  return {
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: CHART_TEXT_COLOR,
    },
    grid: {
      vertLines: { color: CHART_GRID_COLOR },
      horzLines: { color: CHART_GRID_COLOR },
    },
    rightPriceScale: { borderColor: CHART_BORDER_COLOR },
    localization: { timeFormatter: formatChartTime },
    height,
  }
}

const BB_COLOR_UPPER = "rgba(59, 130, 246, 0.5)"
const BB_COLOR_MIDDLE = "rgba(59, 130, 246, 0.8)"

/** 加一条布林带线(不显价格线/末值)。 */
function addBandLine(
  chart: IChartApi,
  color: string,
): ISeriesApi<"Line"> {
  return chart.addSeries(LineSeries, {
    color,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
  })
}

export interface IndicatorChartProps {
  data: Kline[]
  interval: string
  showBollingerBands?: boolean
  showRSI?: boolean
  showMACD?: boolean
  showVolume?: boolean
  bollingerPeriod?: number
  bollingerStdDev?: number
  rsiPeriod?: number
  macdFastPeriod?: number
  macdSlowPeriod?: number
  macdSignalPeriod?: number
}

interface IndicatorVisibility {
  bollingerBands: boolean
  rsi: boolean
  macd: boolean
  volume: boolean
}

export default function IndicatorChart({
  data,
  interval,
  showBollingerBands = true,
  showRSI = true,
  showMACD = true,
  showVolume = true,
  bollingerPeriod = 20,
  bollingerStdDev = 2,
  rsiPeriod = 14,
  macdFastPeriod = 12,
  macdSlowPeriod = 26,
  macdSignalPeriod = 9,
}: IndicatorChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const mainChartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const macdChartRef = useRef<IChartApi | null>(null)

  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null)
  const bbUpperRef = useRef<ISeriesApi<"Line"> | null>(null)
  const bbMiddleRef = useRef<ISeriesApi<"Line"> | null>(null)
  const bbLowerRef = useRef<ISeriesApi<"Line"> | null>(null)
  const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null)
  const macdLineRef = useRef<ISeriesApi<"Line"> | null>(null)
  const macdSignalRef = useRef<ISeriesApi<"Line"> | null>(null)
  const macdHistogramRef = useRef<ISeriesApi<"Histogram"> | null>(null)

  const [visibility, setVisibility] = useState<IndicatorVisibility>({
    bollingerBands: showBollingerBands,
    rsi: showRSI,
    macd: showMACD,
    volume: showVolume,
  })

  const toggleIndicator = useCallback((key: keyof IndicatorVisibility) => {
    setVisibility((prev) => ({ ...prev, [key]: !prev[key] }))
  }, [])

  const indicators = useMemo(() => {
    if (data.length === 0) return null

    const closes = data.map((d) => d.close)
    const bb = calculateBollingerBands(closes, bollingerPeriod, bollingerStdDev)
    const rsi = calculateRSI(closes, rsiPeriod)
    const macd = calculateMACD(closes, macdFastPeriod, macdSlowPeriod, macdSignalPeriod)

    return { bb, rsi, macd }
  }, [data, bollingerPeriod, bollingerStdDev, rsiPeriod, macdFastPeriod, macdSlowPeriod, macdSignalPeriod])

  useEffect(() => {
    if (!chartContainerRef.current) return

    const handleResize = () => {
      if (mainChartRef.current && chartContainerRef.current) {
        mainChartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        })
      }
    }

    const chart = createChart(chartContainerRef.current, {
      ...baseChartOptions(visibility.rsi || visibility.macd ? 300 : 500),
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: CHART_TEXT_COLOR,
        fontFamily: "Inter, system-ui, sans-serif",
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: CHART_CROSSHAIR_COLOR, style: 2 },
        horzLine: { color: CHART_CROSSHAIR_COLOR, style: 2 },
      },
      timeScale: {
        borderColor: CHART_BORDER_COLOR,
        timeVisible: true,
        secondsVisible: false,
      },
    })

    mainChartRef.current = chart

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#EF5350",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#EF5350",
    })
    candleSeriesRef.current = candleSeries as any

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    })
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
    volumeSeriesRef.current = volumeSeries

    bbUpperRef.current = addBandLine(chart, BB_COLOR_UPPER)

    bbMiddleRef.current = addBandLine(chart, BB_COLOR_MIDDLE)

    bbLowerRef.current = addBandLine(chart, BB_COLOR_UPPER)

    const ro = new ResizeObserver(handleResize)
    ro.observe(chartContainerRef.current)
    window.addEventListener("resize", handleResize)

    return () => {
      ro.disconnect()
      window.removeEventListener("resize", handleResize)
      chart.remove()
      mainChartRef.current = null
    }
  }, [visibility.rsi, visibility.macd])

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || data.length === 0) return

    const candleData = data.map((d) => ({
      time: barTime(d.timestamp) as any,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }))

    const volData = data.map((d) => ({
      time: barTime(d.timestamp) as any,
      value: d.volume,
      color: d.close >= d.open ? "rgba(16, 185, 129, 0.4)" : "rgba(239, 68, 68, 0.4)",
    }))

    candleSeriesRef.current.setData(candleData)
    volumeSeriesRef.current.setData(volData)

    if (indicators && visibility.bollingerBands) {
      const timeData = data.map((d) => barTime(d.timestamp) as any)

      bbUpperRef.current?.setData(toSeriesData(timeData, indicators.bb.upper))
      bbMiddleRef.current?.setData(toSeriesData(timeData, indicators.bb.middle))
      bbLowerRef.current?.setData(toSeriesData(timeData, indicators.bb.lower))
    } else {
      bbUpperRef.current?.setData([])
      bbMiddleRef.current?.setData([])
      bbLowerRef.current?.setData([])
    }

    if (mainChartRef.current) {
      mainChartRef.current.timeScale().fitContent()
    }
  }, [data, indicators, visibility.bollingerBands])

  return (
    <div className="w-full h-full flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 px-2">
        <Button
          type="button"
          size="sm"
          variant={visibility.bollingerBands ? "default" : "outline"}
          className="h-7 text-xs gap-1"
          onClick={() => toggleIndicator("bollingerBands")}
        >
          {visibility.bollingerBands ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
          Bollinger
        </Button>
        <Button
          type="button"
          size="sm"
          variant={visibility.rsi ? "default" : "outline"}
          className="h-7 text-xs gap-1"
          onClick={() => toggleIndicator("rsi")}
        >
          {visibility.rsi ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
          RSI
        </Button>
        <Button
          type="button"
          size="sm"
          variant={visibility.macd ? "default" : "outline"}
          className="h-7 text-xs gap-1"
          onClick={() => toggleIndicator("macd")}
        >
          {visibility.macd ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
          MACD
        </Button>
        <Button
          type="button"
          size="sm"
          variant={visibility.volume ? "default" : "outline"}
          className="h-7 text-xs gap-1"
          onClick={() => toggleIndicator("volume")}
        >
          {visibility.volume ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
          Volume
        </Button>

        <Badge variant="outline" className="text-xs ml-auto">
          {data.length} candles
        </Badge>
      </div>

      <div ref={chartContainerRef} className="flex-1 min-h-[300px]" />

      {visibility.rsi && (
        <div className="h-[100px] border-t border-border">
          <div className="text-xs text-muted-foreground px-2 py-1 font-medium">RSI ({rsiPeriod})</div>
          <RSISubChart data={data} rsiData={indicators?.rsi ?? null} />
        </div>
      )}

      {visibility.macd && (
        <div className="h-[120px] border-t border-border">
          <div className="text-xs text-muted-foreground px-2 py-1 font-medium">MACD ({macdFastPeriod}/{macdSlowPeriod}/{macdSignalPeriod})</div>
          <MACDSubChart
            data={data}
            macdData={indicators?.macd ?? null}
          />
        </div>
      )}
    </div>
  )
}

function RSISubChart({
  data,
  rsiData,
}: {
  data: Kline[]
  rsiData: (number | null)[] | null
}) {
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInstanceRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null)

  useEffect(() => {
    if (!chartRef.current) return

    const chart = createChart(chartRef.current, {
      ...baseChartOptions(70),
      timeScale: { visible: false },
    })

    chartInstanceRef.current = chart

    const lineSeries = chart.addSeries(LineSeries, {
      color: "#8b5cf6",
      lineWidth: 1,
    })
    seriesRef.current = lineSeries

    return () => {
      chart.remove()
      chartInstanceRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!seriesRef.current || !rsiData || data.length === 0) return

    const rsiChartData = toSeriesData(
      data.map((d) => barTime(d.timestamp) as any),
      rsiData,
    )

    seriesRef.current.setData(rsiChartData)
    chartInstanceRef.current?.timeScale().fitContent()
  }, [data, rsiData])

  return <div ref={chartRef} className="w-full h-[70px]" />
}

function MACDSubChart({
  data,
  macdData,
}: {
  data: Kline[]
  macdData: { macd: (number | null)[]; signal: (number | null)[]; histogram: (number | null)[] } | null
}) {
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInstanceRef = useRef<IChartApi | null>(null)
  const macdLineRef = useRef<ISeriesApi<"Line"> | null>(null)
  const signalLineRef = useRef<ISeriesApi<"Line"> | null>(null)
  const histogramRef = useRef<ISeriesApi<"Histogram"> | null>(null)

  useEffect(() => {
    if (!chartRef.current) return

    const chart = createChart(chartRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "rgba(255, 255, 255, 0.5)",
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 255, 0.05)" },
        horzLines: { color: "rgba(255, 255, 255, 0.05)" },
      },
      rightPriceScale: { borderColor: "rgba(255, 255, 255, 0.1)" },
      timeScale: { visible: false },
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
      height: 90,
    })

    chartInstanceRef.current = chart

    const histogramSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "price" },
      priceScaleId: "",
    })
    histogramRef.current = histogramSeries

    const macdLine = chart.addSeries(LineSeries, {
      color: "#3b82f6",
      lineWidth: 1,
    })
    macdLineRef.current = macdLine

    const signalLine = chart.addSeries(LineSeries, {
      color: "#f59e0b",
      lineWidth: 1,
    })
    signalLineRef.current = signalLine

    return () => {
      chart.remove()
      chartInstanceRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!macdData || data.length === 0) return

    const timeData = data.map((d) => barTime(d.timestamp) as any)

    if (histogramRef.current) {
      const histogramData = timeData
        .map((t, i) => ({
          time: t,
          value: macdData.histogram[i],
          color: (macdData.histogram[i] ?? 0) >= 0 ? "rgba(16, 185, 129, 0.5)" : "rgba(239, 68, 68, 0.5)",
        }))
        .filter((d) => d.value !== null)
      histogramRef.current.setData(histogramData)
    }

    if (macdLineRef.current) {
      macdLineRef.current.setData(toSeriesData(timeData, macdData.macd))
    }

    if (signalLineRef.current) {
      signalLineRef.current.setData(toSeriesData(timeData, macdData.signal))
    }

    chartInstanceRef.current?.timeScale().fitContent()
  }, [data, macdData])

  return <div ref={chartRef} className="w-full h-[90px]" />
}
