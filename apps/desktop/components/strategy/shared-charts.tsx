"use client"

import React, { useId } from "react"
import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts"

export function formatChartValue(value: number, negative?: boolean): string {
  if (!Number.isFinite(value)) return "—"
  if (negative) return value.toFixed(3)
  if (Math.abs(value) >= 1000) return value.toFixed(0)
  if (Math.abs(value) >= 100) return value.toFixed(1)
  return value.toFixed(2)
}

/* ---------- CurveChart ---------- */

export function CurveChart({
  curve,
  negative = false,
  baselineCurve,
  baselineLabel,
  emptyLabel,
  ariaLabel,
  height = 220,
  timestamps,
}: {
  curve: number[]
  negative?: boolean
  baselineCurve?: number[]
  baselineLabel?: string
  emptyLabel?: string
  ariaLabel?: string
  height?: number
  timestamps?: number[]
}) {
  const uid = useId()
  const validCurve = curve.filter(v => Number.isFinite(v))
  const validBaseline = baselineCurve?.filter(v => Number.isFinite(v))

  if (validCurve.length < 2) {
    return (
      <p
        role="img"
        aria-label={ariaLabel ?? "收益曲线"}
        className="flex items-center justify-center rounded-xl border border-dashed border-border/70 bg-muted/20 px-4 text-sm text-muted-foreground"
        style={{ height }}
      >
        {emptyLabel ?? "—"}
      </p>
    )
  }

  const w = 640
  const h = height
  const padX = 64
  const padY = 30
  const padBottom = 20

  const allValues = validBaseline ? [...validCurve, ...validBaseline] : validCurve
  const min = Math.min(...allValues)
  const max = Math.max(...allValues)
  const range = max - min || 1

  const gradientId = `${uid}-${negative ? "dd" : "eq"}`
  const stroke = negative
    ? "var(--color-warn)"
    : validCurve[validCurve.length - 1] >= validCurve[0]
      ? "var(--color-pos)"
      : "var(--color-neg)"

  const toPath = (data: number[]) =>
    data
      .map((v, i) => {
        const x = padX + (i / (data.length - 1)) * (w - padX * 2)
        const y = padY + (1 - (v - min) / range) * (h - padY * 2)
        return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`
      })
      .join(" ")

  const path = toPath(validCurve)
  const areaPath = `${path} L ${(w - padX).toFixed(1)} ${(h - padY).toFixed(1)} L ${padX.toFixed(1)} ${(h - padY).toFixed(1)} Z`
  const lastPointX = padX + (w - padX * 2)
  const lastPointY = padY + (1 - (validCurve[validCurve.length - 1] - min) / range) * (h - padY * 2)

  const xAxisLabels = generateXAxisLabels(timestamps, w, padX)

  return (
    <div className="space-y-3">
      {validBaseline && validBaseline.length >= 2 && baselineLabel ? (
        <div className="flex flex-wrap items-center justify-between gap-3 text-[11px] text-muted-foreground">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: stroke }} />
              <span>Strategy</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="h-px w-5 border-t border-dashed border-ink/40" />
              <span>{baselineLabel}</span>
            </div>
          </div>
          <div className="rounded-full border border-border/70 bg-background/70 px-2.5 py-1 font-mono text-[11px] text-foreground/80">
            Last {formatChartValue(validCurve[validCurve.length - 1], negative)}
          </div>
        </div>
      ) : null}
      <div
        role="img"
        aria-label={ariaLabel ?? "收益曲线"}
        className="overflow-hidden rounded-xl border border-border/70 bg-[#111317] px-2 py-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]"
      >
        <svg viewBox={`0 0 ${w} ${h}`} className="w-full overflow-visible">
          {[0, 0.25, 0.5, 0.75, 1].map((pct) => {
            const y = padY + pct * (h - padY * 2)
            const val = max - pct * range
            return (
              <g key={pct}>
                <line x1={padX} y1={y} x2={w - padX} y2={y} stroke="rgba(255,255,255,0.035)" />
                <text x={padX - 8} y={y + 4} textAnchor="end" fontSize={10} className="fill-muted-foreground">
                  {formatChartValue(val, negative)}
                </text>
              </g>
            )
          })}
          {xAxisLabels.map(({ x, label }, idx) => (
            <text key={idx} x={x} y={h - 6} textAnchor="middle" fontSize={9} className="fill-muted-foreground">
              {label}
            </text>
          ))}
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" style={{ stopColor: stroke }} stopOpacity={0.2} />
              <stop offset="100%" style={{ stopColor: stroke }} stopOpacity={0.01} />
            </linearGradient>
          </defs>
          <path d={areaPath} fill={`url(#${gradientId})`} />
          <path d={path} fill="none" style={{ stroke }} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
          {validBaseline && validBaseline.length >= 2 && (
            <path
              d={toPath(validBaseline)}
              fill="none"
              stroke="var(--color-ink)"
              strokeOpacity={0.5}
              strokeWidth={1.2}
              strokeDasharray="5 4"
              strokeLinecap="round"
            />
          )}
          <circle cx={lastPointX} cy={lastPointY} r={4.2} style={{ fill: stroke }} stroke="rgba(17,19,23,0.96)" strokeWidth={2.4} />
        </svg>
      </div>
    </div>
  )
}

/* ---------- MetricCard ---------- */

export function MetricCard({
  label,
  value,
  icon: Icon,
  color,
  hint,
}: {
  label: string
  value: string
  icon: React.ComponentType<{ className?: string }>
  color?: string
  /** 可选警示:数值被加工/封顶时提示,避免呈现被静默改过的数字 */
  hint?: string
}) {
  return (
    <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
      <Icon className={`h-4 w-4 ${color || "text-primary"}`} />
      <div>
        <p className="text-xs text-muted-foreground">
          {label}
          {hint ? (
            <span className="ml-1 text-amber-500" title={hint}>
              ⚠
            </span>
          ) : null}
        </p>
        <p className={`text-sm font-medium font-mono ${color || ""}`}>{value}</p>
        {hint ? <p className="text-[10px] text-amber-500">{hint}</p> : null}
      </div>
    </div>
  )
}

/**
 * 生成 X 轴刻度(最多 5 个)。无时间戳时退回百分比刻度。纯函数。
 */
export function generateXAxisLabels(
  timestamps: number[] | undefined,
  w: number,
  padX: number,
): { x: number; label: string }[] {
  const innerW = w - padX * 2
  if (!timestamps || timestamps.length < 2) {
    return [0, 0.25, 0.5, 0.75, 1].map((pct) => ({
      x: padX + pct * innerW,
      label: `${Math.round(pct * 100)}%`,
    }))
  }
  const count = Math.min(5, timestamps.length)
  const span = timestamps.length - 1
  return Array.from({ length: count }, (_, i) => {
    const idx = Math.round((i / (count - 1)) * span)
    return {
      x: padX + (idx / span) * innerW,
      label: new Date(timestamps[idx]).toLocaleDateString("default", {
        month: "short",
        day: "numeric",
      }),
    }
  })
}

/* ---------- TradeDistribution ---------- */

export type TradeDataRow = Record<string, unknown> & {
  pnl?: number | string | null
}

/** 把盈亏序列切成 4–8 个等宽桶, 每桶计数(末桶含右端点)。纯函数。 */
export function bucketPnls(
  pnls: number[],
): { start: number; end: number; count: number }[] {
  const min = Math.min(...pnls)
  const max = Math.max(...pnls)
  const bucketCount = Math.min(8, Math.max(4, pnls.length))
  const width = max - min || 1
  return Array.from({ length: bucketCount }, (_, index) => {
    const start = min + (width / bucketCount) * index
    const end =
      index === bucketCount - 1 ? max : min + (width / bucketCount) * (index + 1)
    const count = pnls.filter(
      (v) => v >= start && (index === bucketCount - 1 ? v <= end : v < end),
    ).length
    return { start, end, count }
  })
}

export function TradeDistribution({
  trades,
  emptyLabel,
  rangeLabel,
}: {
  trades: TradeDataRow[]
  emptyLabel?: string
  rangeLabel?: string
}) {
  const pnls = trades.map((trade) => Number(trade.pnl ?? 0)).filter((v) => Number.isFinite(v))
  if (pnls.length === 0) {
    return <EmptyNote label={emptyLabel} />
  }

  const buckets = bucketPnls(pnls)
  const maxCount = Math.max(...buckets.map((b) => b.count), 1)

  return (
    <div className="space-y-3">
      {rangeLabel ? (
        <div className="flex items-center justify-between text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          <span>{rangeLabel}</span>
          <span>{pnls.length}</span>
        </div>
      ) : null}
      {buckets.map((bucket, index) => {
        const tone = bucket.end <= 0 ? "bg-neg" : bucket.start >= 0 ? "bg-pos" : "bg-warn"
        return (
          <div key={`${bucket.start}-${index}`} className="rounded-xl border border-border/60 bg-background/30 px-3 py-2">
            <div className="mb-2 flex items-center justify-between gap-4 text-xs text-muted-foreground">
              <span className="font-mono">{bucket.start.toFixed(2)} to {bucket.end.toFixed(2)}</span>
              <span className="font-semibold text-foreground/80">{bucket.count}</span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-muted/70">
              <div className={`h-full rounded-full ${tone}`} style={{ width: `${(bucket.count / maxCount) * 100}%` }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

/* ---------- MonthlyReturnsHeatmap ---------- */

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/** 全期收益(首→末); 首值为 0 时记 0。 */
function totalReturnOf(curve: number[]): number {
  return curve[0] ? (curve[curve.length - 1] - curve[0]) / curve[0] : 0
}

/** 月份标签: 桶多(>6)时带两位年份, 否则只月份名。 */
function monthLabel(monthIdx: number, year: string, manyBuckets: boolean): string {
  return manyBuckets ? `${MONTH_LABELS[monthIdx]} ${year.slice(2)}` : MONTH_LABELS[monthIdx]
}

export function computeMonthlyReturns(
  curve: number[],
  startDate?: string | null,
  endDate?: string | null,
): { label: string; value: number }[] {
  if (curve.length < 2) return []

  const start = startDate ? new Date(startDate) : null
  const end = endDate ? new Date(endDate) : null
  const validRange =
    start && end && !isNaN(start.getTime()) && !isNaN(end.getTime())
  const totalMs = validRange ? end!.getTime() - start!.getTime() : 0
  if (!validRange || totalMs <= 0) {
    return [{ label: "Total", value: totalReturnOf(curve) }]
  }

  // 每点按其时间戳归到 "年-月" 桶, 记录该桶覆盖的起止下标。
  const buckets = new Map<string, { startIdx: number; endIdx: number }>()
  const span = curve.length - 1
  curve.forEach((_, idx) => {
    const at = new Date(start!.getTime() + (idx / span) * totalMs)
    const key = `${at.getFullYear()}-${at.getMonth()}`
    const bucket = buckets.get(key)
    if (bucket) bucket.endIdx = idx
    else buckets.set(key, { startIdx: idx, endIdx: idx })
  })

  const many = buckets.size > 6
  return [...buckets.entries()].map(([key, { startIdx, endIdx }]) => {
    const [year, monthStr] = key.split("-")
    const from = curve[startIdx]
    const to = curve[endIdx]
    return {
      label: monthLabel(parseInt(monthStr, 10), year, many),
      value: from ? (to - from) / from : 0,
    }
  })
}

/** 空态占位段落(多处共用)。 */
function EmptyNote({ label }: { label?: string }) {
  return (
    <p className="text-sm text-muted-foreground text-center py-8">{label ?? "—"}</p>
  )
}

/** 月度收益格子的背景色(按强度在 pos/neg 之间取 alpha)。纯函数。 */
export function heatBackground(value: number): string {
  const intensity = Math.min(1, Math.abs(value) * 8)
  const alpha = Math.round((0.18 + intensity * 0.4) * 100)
  const token = value >= 0 ? "var(--color-pos)" : "var(--color-neg)"
  return `color-mix(in srgb, ${token} ${alpha}%, transparent)`
}

export function MonthlyReturnsHeatmap({
  curve,
  startDate,
  endDate,
  emptyLabel,
  positiveLabel,
  negativeLabel,
}: {
  curve: number[]
  startDate?: string | null
  endDate?: string | null
  emptyLabel?: string
  positiveLabel?: string
  negativeLabel?: string
}) {
  if (curve.length < 2) {
    return <EmptyNote label={emptyLabel} />
  }

  const values = computeMonthlyReturns(curve, startDate, endDate)

  if (values.length === 0) {
    return <EmptyNote label={emptyLabel} />
  }

  return (
    <div role="img" aria-label="月度收益热力图" className="space-y-4">
      <div className="flex flex-wrap items-center gap-4 text-[11px] text-muted-foreground">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-pos/80" />
          <span>{positiveLabel}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-neg/80" />
          <span>{negativeLabel}</span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
        {values.map((item) => {
          return (
            <div key={item.label} className="rounded-xl border border-border/70 p-3 shadow-sm" style={{ backgroundColor: heatBackground(item.value) }}>
              <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{item.label}</div>
              <div className="mt-2 text-lg font-semibold font-mono text-foreground">{(item.value * 100).toFixed(2)}%</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ---------- ReturnDistributionHistogram ---------- */

export function ReturnDistributionHistogram({
  bins,
  emptyLabel,
  height = 240,
}: {
  bins: { bin_center: number; count: number }[]
  emptyLabel?: string
  height?: number
}) {
  if (!bins || bins.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-8">{emptyLabel ?? "—"}</p>
  }
  const data = bins.map((b) => ({ ...b, bin_center: b.bin_center * 100 }))
  return (
    <div className="w-full" role="img" aria-label="收益分布直方图" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 5, right: 8, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="bin_center"
            tick={{ fontSize: 10 }}
            stroke="hsl(var(--muted-foreground))"
            tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          />
          <YAxis tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" allowDecimals />
          <Tooltip
            contentStyle={{
              backgroundColor: "hsl(var(--popover))",
              border: "1px solid hsl(var(--border))",
              borderRadius: "6px",
              fontSize: "12px",
            }}
            formatter={(value, name) => [value, name === "count" ? "频数" : name]}
            labelFormatter={(v) => `收益 ${v}%`}
          />
          <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {data.map((b, i) => (
              <Cell key={i} fill={b.bin_center >= 0 ? "var(--color-pos)" : "var(--color-neg)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

/* ---------- Buy & Hold baseline generator ---------- */

export function generateBuyHoldCurve(equityCurve: number[]): number[] {
  if (equityCurve.length < 2) return []
  const start = equityCurve[0]
  const end = equityCurve[equityCurve.length - 1]
  const totalReturn = end / start
  return equityCurve.map((_, i) => {
    const progress = i / (equityCurve.length - 1)
    return start * (1 + (totalReturn - 1) * progress)
  })
}
