"use client"

import React, { useEffect, useMemo, useRef, useState } from "react"
import { createChart, ColorType, IChartApi, ISeriesApi, AreaSeries, CrosshairMode, AreaSeriesPartialOptions, Time } from "lightweight-charts"
import { useChartTheme } from "@/components/chart/use-chart-theme"
import { toRgba } from "@/lib/chart/theme"

// ---------------------------------------------------------------------------
// Semantic colors — canvas charts can't consume CSS var()/color-mix(), so we
// resolve tokens to concrete strings via useChartTheme() (locale-reactive hook).
// drawdown 模式属**判定轴**(恶化水下图) → theme.destructive(恒定);
// 权益曲线的涨/跌属**方向轴** → theme.pos/neg(随 locale 翻转)。
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// LightweightAreaChart — single canvas chart for both the strategy equity
// curve (with optional benchmark baseline) and the drawdown underwater curve.
// Deduplicates the two near-identical components that used to live in
// equity-curve-chart.tsx.
// ---------------------------------------------------------------------------

interface LightweightAreaChartProps {
  data: number[]
  timestamps?: number[]
  baselineData?: number[]
  baselineLabel?: string
  height?: number
  className?: string
  mode?: "equity" | "drawdown"
  ariaLabel?: string
}

export function LightweightAreaChart({
  data,
  timestamps,
  baselineData,
  baselineLabel = "Buy & Hold",
  height,
  className,
  mode = "equity",
  ariaLabel,
}: LightweightAreaChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const [isVisible, setIsVisible] = useState(false)

  // 图表 token 契约:locale 变化返回新引用 → 下方建图 effect 依赖它即重绘。
  const theme = useChartTheme()
  // 权益曲线涨/跌 = 方向轴(随 locale 翻转);drawdown = 判定轴(恒定 destructive)
  const isUp = data.length >= 2 && data[data.length - 1] >= data[0]
  const mainColor = mode === "drawdown" ? theme.destructive : isUp ? theme.pos : theme.neg

  // Lazy render gate — only build the canvas when the container is on screen.
  useEffect(() => {
    if (!chartContainerRef.current) return
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          setIsVisible(entry.isIntersecting && entry.boundingClientRect.width > 0)
        }
      },
      { threshold: 0.1 },
    )
    observer.observe(chartContainerRef.current)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const container = chartContainerRef.current
    if (!container || data.length < 2 || !isVisible) return
    const chartWidth = container.clientWidth
    if (chartWidth < 50) return

    const chart = createChart(container, {
      width: chartWidth,
      height: height ?? container.clientHeight ?? 300,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "rgba(255, 255, 255, 0.6)",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 0.05)" },
        horzLines: { color: "rgba(255, 255, 255, 0.05)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: toRgba(mode === "drawdown" ? theme.destructive : "#22d3ee", 0.5),
          width: 1,
          style: 2,
          labelBackgroundColor: toRgba(mode === "drawdown" ? theme.destructive : "#22d3ee", 0.8),
        },
        horzLine: {
          color: toRgba(mode === "drawdown" ? theme.destructive : "#22d3ee", 0.5),
          width: 1,
          style: 2,
          labelBackgroundColor: toRgba(mode === "drawdown" ? theme.destructive : "#22d3ee", 0.8),
        },
      },
      rightPriceScale: {
        borderColor: "rgba(255, 255, 255, 0.1)",
        scaleMargins: { top: 0.1, bottom: 0.1 },
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
    chartRef.current = chart

    const _tzOffsetSec = -(new Date().getTimezoneOffset() * 60)
    const toPoint = (value: number, index: number) => {
      let time: Time
      if (timestamps && timestamps[index]) {
        time = (Math.floor(timestamps[index] / 1000) + _tzOffsetSec) as Time
      } else {
        const baseDate = new Date("2024-01-01")
        baseDate.setHours(index)
        time = (Math.floor(baseDate.getTime() / 1000) + _tzOffsetSec) as Time
      }
      return { time, value: mode === "drawdown" ? Math.abs(value) * 100 : value }
    }

    const areaSeriesOptions: AreaSeriesPartialOptions = {
      topColor: toRgba(mainColor, 0.3),
      bottomColor: toRgba(mainColor, 0.01),
      lineColor: mainColor,
      lineWidth: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    }
    const areaSeries = chart.addSeries(AreaSeries, areaSeriesOptions)
    areaSeries.setData(data.map(toPoint))

    if (mode === "equity" && baselineData && baselineData.length >= 2) {
      const baselineOptions: AreaSeriesPartialOptions = {
        topColor: "rgba(255, 255, 255, 0.1)",
        bottomColor: "rgba(255, 255, 255, 0.01)",
        lineColor: "rgba(255, 255, 255, 0.5)",
        lineWidth: 1,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      }
      const baselineSeries = chart.addSeries(AreaSeries, baselineOptions)
      baselineSeries.setData(baselineData.map(toPoint))
    }

    chart.timeScale().fitContent()

    // Sync canvas size to the container without rebuilding the chart.
    const syncSize = () => {
      if (!container || !chartRef.current) return
      const w = container.clientWidth
      const h = height ?? container.clientHeight
      chartRef.current.applyOptions({ width: Math.max(w, 50), height: Math.max(h, 50) })
    }
    const resizeObserver = new ResizeObserver(syncSize)
    resizeObserver.observe(container)
    window.addEventListener("resize", syncSize)

    return () => {
      resizeObserver.disconnect()
      window.removeEventListener("resize", syncSize)
      chart.remove()
      chartRef.current = null
    }
  }, [data, timestamps, baselineData, height, isVisible, theme, mode])

  const resolvedAria = ariaLabel ?? (mode === "drawdown" ? "回撤水下曲线" : "策略权益曲线")

  if (data.length < 2) {
    return (
      <div
        role="img"
        aria-label={resolvedAria}
        className={`flex items-center justify-center text-sm text-muted-foreground ${className}`}
        style={{ height: height ?? 300 }}
      >
        暂无数据
      </div>
    )
  }

  return (
    <div
      ref={chartContainerRef}
      role="img"
      aria-label={resolvedAria}
      className={className}
      style={{ height: height ?? "100%", width: "100%" }}
    >
      {mode === "equity" && baselineData && baselineData.length >= 2 && (
        <div className="pointer-events-none absolute top-2 left-2 z-10 flex items-center gap-4 text-[11px] text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: mainColor }} />
            <span>Strategy</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-white/50" />
            <span>{baselineLabel}</span>
          </div>
        </div>
      )}
    </div>
  )
}
