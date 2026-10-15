"use client"

import React, { useEffect, useState, useCallback, useRef, useId, useMemo } from "react"
import { useApi } from "@/lib/hooks/use-api"
import { TrendingUp, TrendingDown, Activity } from "lucide-react"

const CARD_CLS = "rp-card rounded border border-border-soft bg-surface p-2"

/**
 * 由权益序列算出展示用的派生指标(当前值/基线/涨跌/百分比)。纯函数。
 */
export function computeEquityMetrics(
  curve: number[],
  initialBalance?: number,
): {
  currentEquity: number
  baseline: number
  isProfit: boolean
  pnlValue: number
  pnlPct: number
} {
  const currentEquity = curve.length > 0 ? curve[curve.length - 1] : 0
  const baseline =
    initialBalance && initialBalance > 0
      ? initialBalance
      : curve.length > 0
        ? curve[0]
        : 0
  const pnlValue = currentEquity - baseline
  const pnlPct = baseline > 0 ? (pnlValue / baseline) * 100 : 0
  return {
    currentEquity,
    baseline,
    isProfit: currentEquity >= baseline,
    pnlValue,
    pnlPct,
  }
}

/** SVG 画布与内边距常量。 */
const SVG_W = 300
const SVG_H = 100
const SVG_PAD_X = 5
const SVG_PAD_Y = 10
/** 实时采样最多保留的点数(轮询窗口)。 */
const LIVE_SAMPLE_KEEP = 60
/** 实时采样间隔(ms)。 */
const LIVE_SAMPLE_INTERVAL_MS = 10_000

export interface PaperEquityPoint {
  timestamp: string
  equity: number
}

/** 序列末点的时间戳(空序列给空串)。 */
function tailTs(pts: PaperEquityPoint[]): string {
  return pts.length > 0 ? (pts[pts.length - 1]?.timestamp ?? "") : ""
}

/** 追加时间戳晚于 cut 的点到 base(返回新数组); cut 为空则全追加。 */
function appendNewerThan(
  base: PaperEquityPoint[],
  extra: PaperEquityPoint[],
  cut: string,
): PaperEquityPoint[] {
  const fresh = cut ? extra.filter((p) => p.timestamp > cut) : extra.slice()
  return fresh.length > 0 ? [...base, ...fresh] : base
}

/**
 * 合并三条权益来源为一个序列(REST 历史 → WS 推送 → 轮询采样 → 实时值),
 * 只保留末 200 点。纯函数, 便于上锁与重写。
 */
export function mergeEquityHistory(
  restHistory: PaperEquityPoint[],
  wsEquityPoints: PaperEquityPoint[] | undefined,
  liveSamples: PaperEquityPoint[],
  liveEquity: number | undefined,
  nowIso: string,
): PaperEquityPoint[] {
  // REST 历史是基底; 无 REST 时退回 WS 全量。
  const ws = wsEquityPoints ?? []
  const merged =
    ws.length === 0
      ? [...restHistory]
      : restHistory.length === 0
        ? [...ws]
        : appendNewerThan(restHistory, ws, tailTs(restHistory))

  // 轮询采样: 只追加晚于当前末点的。
  const withSamples =
    liveSamples.length === 0
      ? merged
      : appendNewerThan(merged, liveSamples, tailTs(merged))

  // 实时值: 总是追加为最新点。
  const withLive =
    liveEquity === undefined
      ? withSamples
      : [...withSamples, { timestamp: nowIso, equity: liveEquity }]

  return withLive.slice(-200)
}

/** 把值域映射到画布 y 坐标(顶=high, 底=low)。 */
function scaleY(value: number, min: number, range: number, top: number, height: number): number {
  return top + (1 - (value - min) / range) * height
}

/**
 * 由权益序列算出 SVG 的折线/面积路径与涨跌标志。纯函数。
 */
export function buildCurvePaths(
  curve: number[],
  w = 300,
  h = 100,
  padX = 5,
  padY = 10,
): { path: string; areaPath: string; isProfit: boolean } {
  const min = Math.min(...curve)
  const range = Math.max(...curve) - min || 1
  const innerW = w - padX * 2
  const innerH = h - padY * 2
  const stepX = curve.length - 1

  const points = curve.map((value, i) => {
    const x = padX + (i / stepX) * innerW
    const y = scaleY(value, min, range, padY, innerH)
    return `${x.toFixed(1)} ${y.toFixed(1)}`
  })

  const path = points.map((pt, i) => `${i === 0 ? "M" : "L"} ${pt}`).join(" ")
  const bottomY = (h - padY).toFixed(1)
  const areaPath = `${path} L ${(w - padX).toFixed(1)} ${bottomY} L ${padX.toFixed(1)} ${bottomY} Z`

  return { path, areaPath, isProfit: curve[curve.length - 1] >= curve[0] }
}

interface PNLCurvePanelProps {
  accountId: string
  className?: string
  noWrapper?: boolean
  compact?: boolean
  wsEquityPoints?: PaperEquityPoint[]
  onHistoryLoaded?: (points: PaperEquityPoint[]) => void
  liveEquity?: number
  initialBalance?: number
  refreshTrigger?: number
}

/**
 * 轮询采样实时权益: 有 liveEquity 时按固定间隔追加到环形窗口;
 * 换账户或收到刷新信号时清空。返回采样序列。
 */
function useLiveSamples(
  liveEquity: number | undefined,
  accountId: string,
  refreshTrigger: number | undefined,
): PaperEquityPoint[] {
  const [samples, setSamples] = useState<PaperEquityPoint[]>([])
  const liveRef = useRef(liveEquity)
  liveRef.current = liveEquity
  const hasLive = liveEquity !== undefined

  // 换账户 / 刷新信号 → 清空采样。
  useEffect(() => {
    setSamples([])
  }, [accountId, refreshTrigger])

  // 有实时值时按间隔采样。
  useEffect(() => {
    if (!hasLive) return
    const id = setInterval(() => {
      const val = liveRef.current
      if (val === undefined) return
      setSamples((prev) => [
        ...prev.slice(-LIVE_SAMPLE_KEEP),
        { timestamp: new Date().toISOString(), equity: val },
      ])
    }, LIVE_SAMPLE_INTERVAL_MS)
    return () => clearInterval(id)
  }, [accountId, hasLive])

  return samples
}

export function PNLCurvePanel({
  accountId,
  className,
  noWrapper = false,
  compact = false,
  wsEquityPoints,
  onHistoryLoaded,
  liveEquity,
  initialBalance,
  refreshTrigger,
}: PNLCurvePanelProps) {
  const [restHistory, setRestHistory] = useState<PaperEquityPoint[]>([])
  const [loading, setLoading] = useState(true)
  const prevAccountId = useRef(accountId)

  const { data: fetchedHistory } = useApi<PaperEquityPoint[]>(
    accountId ? `/api/v1/paper/accounts/${encodeURIComponent(accountId)}/equity-history?limit=200` : null
  )

  const fetchHistory = useCallback(async () => {
    if (!accountId) return
    if (fetchedHistory && fetchedHistory.length > 0) {
      setRestHistory(fetchedHistory)
      onHistoryLoaded?.(fetchedHistory)
    } else {
      setRestHistory([])
    }
    setLoading(false)
  }, [accountId, fetchedHistory, onHistoryLoaded])

  useEffect(() => {
    if (prevAccountId.current !== accountId) {
      setRestHistory([])
      setLoading(true)
      prevAccountId.current = accountId
    }
    fetchHistory()
  }, [fetchHistory, accountId])

  const liveSamples = useLiveSamples(liveEquity, accountId, refreshTrigger)

  // 外部刷新信号 → 清空历史并重取(与采样清空分离, 采样在 hook 内)。
  useEffect(() => {
    if (refreshTrigger === undefined || refreshTrigger <= 0) return
    setRestHistory([])
    setLoading(true)
    fetchHistory()
  }, [refreshTrigger]) // eslint-disable-line react-hooks/exhaustive-deps

  const history = useMemo(
    () => mergeEquityHistory(restHistory, wsEquityPoints, liveSamples, liveEquity, new Date().toISOString()),
    [restHistory, wsEquityPoints, liveSamples, liveEquity]
  )

  if (loading && history.length === 0) {
    const spinner = (
      <div className="h-[120px] flex items-center justify-center">
        <Activity className="h-5 w-5 animate-pulse text-ink-3 opacity-20" />
      </div>
    )
    return noWrapper ? (
      <div className={className}>{spinner}</div>
    ) : (
      <div className={CARD_CLS}>{spinner}</div>
    )
  }

  const curve = history.map((p) => p.equity)
  const { currentEquity, baseline, isProfit, pnlValue, pnlPct } =
    computeEquityMetrics(curve, initialBalance)

  const content = (
    <>
      <div className="flex flex-row items-center justify-between border-b border-border-soft pb-1 mb-2">
        <div className="flex items-center gap-1.5 text-[10px] font-medium text-ink">
          {isProfit ? (
            <TrendingUp className="h-3 w-3 text-emerald-400" />
          ) : (
            <TrendingDown className="h-3 w-3 text-red-400" />
          )}
          收益曲线
        </div>
        {curve.length > 0 && (
          <div className="flex items-center gap-2 text-[10px] font-mono">
            <span className="text-ink-3">{baseline.toFixed(0)} →</span>
            <span className={isProfit ? "text-emerald-400" : "text-red-400"}>
              {currentEquity.toFixed(2)}
            </span>
            <span className={`px-1 py-0.5 rounded text-[9px] ${isProfit ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>
              {pnlValue >= 0 ? "+" : ""}{pnlValue.toFixed(2)} ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)
            </span>
          </div>
        )}
      </div>
      <div className={compact ? "h-[120px] w-full" : "h-[180px] w-full"}>
        {curve.length > 1 ? (
          <EquityCurveSVG curve={curve} />
        ) : (
          <div className="h-full flex items-center justify-center text-[10px] text-ink-4 opacity-60">
            暂无历史数据
          </div>
        )}
      </div>
    </>
  )

  if (noWrapper) {
    return <div className={className}>{content}</div>
  }

  return (
    <div className={`${CARD_CLS}${className ? ` ${className}` : ""}`}>
      {content}
    </div>
  )
}

function EquityCurveSVG({ curve }: { curve: number[] }) {
  const gradientId = useId()
  const { path, areaPath, isProfit } = buildCurvePaths(
    curve,
    SVG_W,
    SVG_H,
    SVG_PAD_X,
    SVG_PAD_Y,
  )

  return (
    <svg viewBox={`0 0 ${SVG_W} ${SVG_H}`} className="w-full h-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={isProfit ? "#10b981" : "#EF5350"} stopOpacity={0.2} />
          <stop offset="100%" stopColor={isProfit ? "#10b981" : "#EF5350"} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradientId})`} />
      <path d={path} fill="none" stroke={isProfit ? "#10b981" : "#EF5350"} strokeWidth={1} />
    </svg>
  )
}
