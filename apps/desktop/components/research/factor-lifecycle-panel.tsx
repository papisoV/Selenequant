"use client"

import React, { useState, useCallback, useEffect, useMemo, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Activity, RefreshCw, ChevronDown, ChevronRight } from "lucide-react"
import { api } from "@/lib/api-client"
import { useContextStore } from "@/lib/store/context"
import { toSymbolPair } from "@/lib/symbol-utils"
import { useUiText } from "@/components/i18n/use-ui-text"
import { identityTranslate, type Translate } from "@/lib/i18n/types"

const DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
const DEFAULT_INTERVALS = ["1h", "4h", "1d"]

interface LifecycleEpoch {
  epoch: number
  rolling_ic: number
  mean_ic: number
  icir: number
  verdict: string
  reason: string
  state_after: string
}

interface LifecycleRow {
  factor: string
  factor_name: string
  category: string
  state: "ACTIVE" | "WATCH" | "DECAYING" | "RETIRED"
  reason: string
  mean_ic: number
  rolling_ic: number
  baseline_ic: number
  icir: number
  ls_spread: number
  turnover_top_quintile: number
  ic_history: number[]
  epochs: LifecycleEpoch[]
}

interface LifecycleData {
  symbol: string
  interval: string
  lookback_bars: number
  epoch_bars: number
  forward_bars: number
  generated_at: string
  rows: LifecycleRow[]
  total_factors: number
  bar_count: number
}

type StateName = LifecycleRow["state"]

// 独立状态 token:与 pos/neg 解耦(zh-CN 翻转陷阱,原型 v2 裁决)
const STATE_COLORS: Record<StateName, string> = {
  ACTIVE: "#61bc66",
  WATCH: "#e0af3a",
  DECAYING: "#f3615c",
  RETIRED: "#767a7e",
}

// 严重性优先排序:DECAYING → WATCH → ACTIVE → RETIRED
const STATE_ORDER: Record<StateName, number> = {
  DECAYING: 0,
  WATCH: 1,
  ACTIVE: 2,
  RETIRED: 3,
}

const STATE_BADGE_CLASS: Record<StateName, string> = {
  ACTIVE: "text-[#61bc66] border-[#61bc66]/35",
  WATCH: "text-[#e0af3a] border-[#e0af3a]/35",
  DECAYING: "text-[#f3615c] border-[#f3615c]/40",
  RETIRED: "text-[#b3b7bc] border-border",
}

function Sparkline({
  row,
  tr = identityTranslate,
}: {
  row: LifecycleRow
  tr?: Translate
}) {
  const { ic_history, baseline_ic, epochs } = row
  if (!ic_history || ic_history.length < 2) return null

  const W = 840, H = 88
  const PAD = 8
  const finite = ic_history.filter((v) => Number.isFinite(v))
  if (finite.length < 2) return null
  const vmin = Math.min(...finite, 0, baseline_ic || 0)
  const vmax = Math.max(...finite, 0, baseline_ic || 0)
  const range = vmax - vmin || 1
  const y = (v: number) => H - PAD - ((v - vmin) / range) * (H - 2 * PAD)
  const x = (i: number) => (i / (finite.length - 1)) * W

  const pts = finite.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")
  const isRetired = row.state === "RETIRED"
  const segments = useMemo(() => {
    const segs: { color: string; points: string }[] = []
    let cur: { color: string; pts: string[] } | null = null
    // 尾部状态决定降级阈值;历史段按 epoch state_after 换色
    const epochState = (frac: number): StateName => {
      if (epochs.length === 0) return "ACTIVE"
      const idx = Math.min(epochs.length - 1, Math.floor(frac * epochs.length))
      return epochs[idx].state_after as StateName
    }
    for (let i = 0; i < finite.length - 1; i++) {
      const st = isRetired ? epochState(i / (finite.length - 1)) : row.state
      const color = STATE_COLORS[st]
      if (!cur || cur.color !== color) {
        if (cur && cur.pts.length > 1) {
          segs.push({ color: cur.color, points: cur.pts.join(" ") })
        }
        // 接缝:新段从上一段末点起笔,避免断线
        const startPts: string[] = cur && cur.pts.length > 0 ? [cur.pts[cur.pts.length - 1]] : []
        cur = { color, pts: [...startPts, `${x(i).toFixed(1)},${y(finite[i]).toFixed(1)}`] }
      }
      cur.pts.push(`${x(i + 1).toFixed(1)},${y(finite[i + 1]).toFixed(1)}`)
    }
    if (cur && cur.pts.length > 1) segs.push({ color: cur.color, points: cur.pts.join(" ") })
    return segs
  }, [finite, epochs, isRetired, row.state]) // eslint-disable-line react-hooks/exhaustive-deps

  const lastX = x(finite.length - 1)
  const lastY = y(finite[finite.length - 1])
  const label = tr("{name} 滚动 IC 轨迹:baseline {b},现值 {c},状态 {s}", {
    name: row.factor_name,
    b: baseline_ic.toFixed(3),
    c: row.rolling_ic.toFixed(3),
    s: row.state,
  })

  return (
    <div className="rounded-md border border-border/50 bg-background px-3 pt-2.5 pb-1.5">
      <svg
        width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        role="img" aria-label={label}
      >
        {Number.isFinite(baseline_ic) && (
          <>
            <line x1={0} y1={y(baseline_ic)} x2={W} y2={y(baseline_ic)} stroke="#767a7e" strokeWidth={1} strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
            <text x={W - 4} y={y(baseline_ic) - 4} fontSize={10} fill="#767a7e" textAnchor="end">baseline</text>
          </>
        )}
        <line x1={0} y1={y(0)} x2={W} y2={y(0)} stroke="#3a3e42" strokeWidth={1} strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
        <text x={W - 4} y={y(0) + 12} fontSize={10} fill="#767a7e" textAnchor="end">zero</text>
        {segments.map((s, i) => (
          <polyline key={i} fill="none" stroke={s.color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" points={s.points} />
        ))}
        <circle cx={lastX} cy={lastY} r={3} fill={STATE_COLORS[row.state]} />
      </svg>
    </div>
  )
}

export function FactorLifecyclePanel() {
  const tr = useUiText()

  const { pageFilter } = useContextStore()
  const [symbol, setSymbol] = useState("BTC/USDT")
  const [interval, setInterval] = useState("1h")
  const userTouchedSymbol = useRef(false)
  const userTouchedInterval = useRef(false)
  const [data, setData] = useState<LifecycleData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!userTouchedSymbol.current && pageFilter.symbol) {
      setSymbol(toSymbolPair(pageFilter.symbol))
    }
  }, [pageFilter.symbol])

  useEffect(() => {
    if (!userTouchedInterval.current && pageFilter.tf && DEFAULT_INTERVALS.includes(pageFilter.tf)) {
      setInterval(pageFilter.tf)
    }
  }, [pageFilter.tf])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = new URLSearchParams({ symbol, interval, lookback_bars: "2000", forward_bars: "1", epoch_bars: "100" })
      const d = await api<LifecycleData>(`/api/v1/research/lifecycle?${qs.toString()}`)
      setData(d)
      // 默认展开:WATCH/DECAYING/RETIRED 行(严重性行默认看尸检,原型 v2 合同)
      const serious = (d?.rows ?? []).filter((r) => r.state !== "ACTIVE").map((r) => r.factor)
      setExpanded(new Set(serious))
    } catch (e) {
      setError(e instanceof Error ? e.message : "巡检失败")
    }
    setLoading(false)
  }, [symbol, interval])

  useEffect(() => { load() }, [load])

  const toggle = useCallback((factor: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(factor)) { next.delete(factor) } else { next.add(factor) }
      return next
    })
  }, [])

  const rows = useMemo(() => {
    const r = data?.rows ?? []
    return [...r].sort((a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state] || b.rolling_ic - a.rolling_ic)
  }, [data])

  const counts = useMemo(() => {
    const c = { watch: 0, decaying: 0, retired: 0 }
    for (const r of rows) {
      if (r.state === "WATCH") c.watch++
      else if (r.state === "DECAYING") c.decaying++
      else if (r.state === "RETIRED") c.retired++
    }
    return c
  }, [rows])

  const lastRun = useMemo(() => {
    if (!data?.generated_at) return null
    const then = new Date(data.generated_at).getTime()
    if (Number.isNaN(then)) return null
    const mins = Math.max(0, Math.round((Date.now() - then) / 60000))
    return mins < 60 ? tr("{n} 分钟前", { n: mins }) : tr("{n}h 前", { n: Math.round(mins / 60) })
  }, [data?.generated_at])

  const fmt = (v: number, digits = 3) => (Number.isFinite(v) ? v.toFixed(digits) : "—")
  const signed = (v: number, digits = 3) => (Number.isFinite(v) ? `${v >= 0 ? "+" : ""}${v.toFixed(digits)}` : "—")

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Activity className="h-4 w-4" />
          {tr("因子生命周期")}
        </CardTitle>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Select value={symbol} onValueChange={(v) => { userTouchedSymbol.current = true; setSymbol(v) }}>
            <SelectTrigger className="w-[130px] h-7"><SelectValue /></SelectTrigger>
            <SelectContent>{DEFAULT_SYMBOLS.map((s) => (<SelectItem key={s} value={s}>{s}</SelectItem>))}</SelectContent>
          </Select>
          <Select value={interval} onValueChange={(v) => { userTouchedInterval.current = true; setInterval(v) }}>
            <SelectTrigger className="w-[80px] h-7"><SelectValue /></SelectTrigger>
            <SelectContent>{DEFAULT_INTERVALS.map((i) => (<SelectItem key={i} value={i}>{i}</SelectItem>))}</SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-3 ml-auto">
          {data && (
            <span className="text-xs text-muted-foreground font-mono">
              {rows.length} factors{counts.watch > 0 && <> · <span className="text-[#e0af3a]">{counts.watch} watch</span></>}
              {counts.decaying > 0 && <> · <span className="text-[#f3615c]">{counts.decaying} decaying</span></>}
              {counts.retired > 0 && <> · <span className="text-muted-foreground">{counts.retired} retired</span></>}
            </span>
          )}
          {lastRun && <span className="text-xs text-muted-foreground">{tr("巡检于")} {lastRun}</span>}
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            {loading ? <RefreshCw className="h-3 w-3 mr-1.5 animate-spin" /> : <RefreshCw className="h-3 w-3 mr-1.5" />}
            {tr("运行巡检")}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {error && <p className="mb-3 text-sm text-red-500">{tr(error)}</p>}
        {loading && !data ? (
          <div className="space-y-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <div className="text-sm text-muted-foreground text-center py-8">{tr("暂无数据 — 点击\"运行巡检\"")}</div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[26px]"></TableHead>
                <TableHead>{tr("因子")}</TableHead>
                <TableHead>{tr("状态")}</TableHead>
                <TableHead className="hidden md:table-cell">{tr("判决理由")}</TableHead>
                <TableHead className="text-right">rolling IC</TableHead>
                <TableHead className="text-right">ICIR</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const isOpen = expanded.has(row.factor)
                const isRetired = row.state === "RETIRED"
                const serious = row.state !== "ACTIVE"
                const icColor = !Number.isFinite(row.rolling_ic)
                  ? "text-muted-foreground"
                  : row.rolling_ic >= 0 ? "text-[#61bc66]" : "text-[#f3615c]"
                return (
                  <React.Fragment key={row.factor}>
                    <TableRow
                      className={`cursor-pointer transition-colors hover:bg-muted/40 ${row.state === "WATCH" ? "bg-[#e0af3a]/[0.045] hover:bg-[#e0af3a]/[0.075]" : ""} ${row.state === "DECAYING" ? "bg-[#f3615c]/[0.055] hover:bg-[#f3615c]/[0.085]" : ""} focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#309cfb] focus-visible:-outline-offset-2`}
                      tabIndex={0}
                      role="button"
                      aria-expanded={isOpen}
                      aria-controls={`lifecycle-detail-${row.factor}`}
                      onClick={() => toggle(row.factor)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(row.factor) }
                      }}
                    >
                      <TableCell>
                        <span
                          className="inline-block w-2 h-2 rounded-full"
                          style={{ backgroundColor: STATE_COLORS[row.state] }}
                          aria-hidden
                        />
                      </TableCell>
                      <TableCell className={`font-mono text-[13px] ${isRetired ? "text-muted-foreground" : ""}`}>
                        {row.factor.toUpperCase()}
                        {serious && <span className="ml-1.5 text-muted-foreground">{isOpen ? <ChevronDown className="h-3 w-3 inline" /> : <ChevronRight className="h-3 w-3 inline" />}</span>}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`font-mono text-[11px] ${STATE_BADGE_CLASS[row.state]}`}>
                          {row.state}
                        </Badge>
                      </TableCell>
                      <TableCell className={`hidden md:table-cell max-w-[320px] truncate text-xs ${row.state === "DECAYING" || row.state === "WATCH" ? "text-foreground" : isRetired ? "text-muted-foreground" : "text-muted-foreground"}`}>
                        {row.reason}
                      </TableCell>
                      <TableCell className={`text-right font-mono text-xs ${isRetired ? "text-muted-foreground" : icColor}`}>
                        {signed(row.rolling_ic, 3)}
                      </TableCell>
                      <TableCell className={`text-right font-mono text-xs ${isRetired ? "text-muted-foreground" : "text-muted-foreground"}`}>
                        {fmt(row.icir, 2)}
                      </TableCell>
                    </TableRow>
                    {isOpen && (
                      <TableRow id={`lifecycle-detail-${row.factor}`} className="bg-muted/30 hover:bg-muted/30">
                        <TableCell colSpan={6} className="px-4 pb-4 pl-9 pt-3">
                          <div className="space-y-2.5">
                            <div className="font-mono text-xs rounded-md border border-border/50 bg-background px-2.5 py-1.5">
                              {row.reason}
                            </div>
                            <div>
                              <div className="flex items-baseline gap-2.5 mb-1.5">
                                <span className="text-[11px] text-muted-foreground tracking-wide">{tr("滚动 rank-IC(120-bar 窗,")}{interval})</span>
                                <span className="font-mono text-[11px] text-muted-foreground">
                                  baseline {fmt(row.baseline_ic)} {tr("· 现值")} {signed(row.rolling_ic)}
                                </span>
                              </div>
                              <Sparkline row={row} tr={tr} />
                            </div>
                            <div className="flex rounded-md border border-border/50 overflow-hidden text-xs">
                              <div className="flex-1 px-3 py-2 border-r border-border/50">
                                <div className="text-[11px] text-muted-foreground mb-0.5">mean IC</div>
                                <div className="font-mono text-[13px]">{fmt(row.mean_ic)}</div>
                              </div>
                              <div className="flex-1 px-3 py-2 border-r border-border/50">
                                <div className="text-[11px] text-muted-foreground mb-0.5">rolling IC</div>
                                <div className={`font-mono text-[13px] ${icColor}`}>{signed(row.rolling_ic)}</div>
                              </div>
                              <div className="flex-1 px-3 py-2 border-r border-border/50">
                                <div className="text-[11px] text-muted-foreground mb-0.5">baseline</div>
                                <div className="font-mono text-[13px]">{fmt(row.baseline_ic)}</div>
                              </div>
                              <div className="flex-1 px-3 py-2 border-l-2 border-r border-border/50">
                                <div className="text-[11px] text-muted-foreground mb-0.5">ICIR</div>
                                <div className="font-mono text-[13px]">{fmt(row.icir, 2)}</div>
                              </div>
                              <div className="flex-1 px-3 py-2 border-r border-border/50">
                                <div className="text-[11px] text-muted-foreground mb-0.5">L/S spread</div>
                                <div className={`font-mono text-[13px] ${Number.isFinite(row.ls_spread) ? (row.ls_spread >= 0 ? "text-[#61bc66]" : "text-[#f3615c]") : ""}`}>
                                  {signed(row.ls_spread, 4)}
                                </div>
                              </div>
                              <div className="flex-1 px-3 py-2">
                                <div className="text-[11px] text-muted-foreground mb-0.5">{tr("top 换手")}</div>
                                <div className="font-mono text-[13px]">{fmt(row.turnover_top_quintile, 2)}</div>
                              </div>
                            </div>
                            {row.epochs.length > 1 && (
                              <div className="font-mono text-[11px] text-muted-foreground">
                                {row.epochs.map((ep, i) => {
                                  const prev = i > 0 ? row.epochs[i - 1].state_after : null
                                  if (prev && prev !== ep.state_after) {
                                    return (
                                      <span key={ep.epoch}>
                                        {i > 0 && " → "}
                                        <span className={STATE_BADGE_CLASS[ep.state_after as StateName].split(" ")[0]}>
                                          epoch {ep.epoch + 1} {ep.state_after}
                                        </span>
                                      </span>
                                    )
                                  }
                                  return null
                                })}
                                {row.epochs.every((ep, i) => i === 0 || row.epochs[i - 1].state_after === ep.state_after) && (
                                  <span>epoch 1–{row.epochs.length} {row.state}{tr("(无迁移)")}</span>
                                )}
                              </div>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </React.Fragment>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
