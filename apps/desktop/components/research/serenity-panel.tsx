"use client"

import React, { useState, useCallback, useMemo, useEffect, Fragment } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Shield, Search, Eye, RefreshCw, Loader2, TrendingUp, TrendingDown, Minus,
  CheckCircle2, AlertTriangle, XCircle, BarChart3, ArrowUpRight, ArrowDownRight, Layers,
} from "lucide-react"
import {
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
} from "recharts"
import { api } from "@/lib/api-client"

const SYMBOLS = ["BTC", "ETH", "SOL", "ARB", "OP", "AVAX", "LINK", "UNI", "AAVE"]

const TIER_COLORS: Record<string, string> = { strong_bullish: "text-emerald-500", bullish: "text-green-500", neutral: "text-zinc-400", bearish: "text-red-400", strong_bearish: "text-red-600" }
const TIER_BG: Record<string, string> = { strong_bullish: "bg-emerald-500/10", bullish: "bg-green-500/10", neutral: "bg-zinc-500/10", bearish: "bg-red-400/10", strong_bearish: "bg-red-600/10" }

const FACTOR_LABELS: Record<string, string> = { consensus_scarcity: "共识", liquidity_scarcity: "流动性", trust_scarcity: "信任", replication_difficulty: "复制", certification_progress: "认证", prepayment_signal: "预付", decentralization: "去中心化", evidence_strength: "证据" }
const PENALTY_LABELS: Record<string, string> = { fork_risk: "分叉风险", tvl_concentration: "TVL集中", dev_exodus: "开发出走", revenue_decline: "收入下降", oi_divergence: "OI背离", whale_exodus: "巨鲸出走", governance_risk: "治理风险", liquidity_trap: "流动性陷阱" }

function formatPct(v: number | null | undefined): string { if (v == null) return "-"; return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%` }
function pctColor(v: number | null | undefined): string { if (v == null) return "text-muted-foreground"; return v > 0 ? "text-emerald-500" : v < 0 ? "text-red-400" : "text-muted-foreground" }

interface SerenityScore {
  tier: string
  raw_score: number
  normalized_score: number
  factors: Record<string, number>
  penalties: Record<string, number>
  price_change_7d?: number | null
  volume_change_7d?: number | null
  funding_rate?: number | null
  oi_change?: number | null
}

interface SerenityReport {
  tier1: SerenityScore
  tier2?: SerenityScore | null
  signal_direction: number
  narrative: string
  system_change: string
  scarce_constraints: string[]
  blind_spots: string[]
  disproof_conditions: string[]
}

interface SerenityScoreRow { timestamp: string; normalized_score: number }

interface PoolLayerSummary { layer: string; count: number; top_scarce_symbols: string[] }
interface PoolCandidate {
  symbol: string
  name: string
  category: string
  chains: string[]
  normalized_score: number
  tier: string
  scarce_layer_score: number
  evidence_strength: number
  evidence_label: string
  rationale: string
  top_penalty: string
  disproof: string[]
}
interface SerenityPoolScan {
  theme: string
  theme_name: string
  scanned_at: number
  universe_size: number
  weak_evidence_count: number
  layer_summary: PoolLayerSummary[]
  top_picks: string[]
  ranked: PoolCandidate[]
}

const THEME_OPTIONS = [
  { key: "lending", name: "Lending" },
  { key: "dex", name: "DEX" },
  { key: "yield", name: "Yield" },
  { key: "restaking", name: "Restaking / Staking" },
  { key: "derivatives", name: "Derivatives" },
  { key: "l2", name: "Layer 2" },
  { key: "ai", name: "AI / Agents" },
]

export function SerenityPanel() {
  const [selectedSymbol, setSelectedSymbol] = useState("BTC")
  const [report, setReport] = useState<SerenityReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<"quick" | "deep">("quick")
  const [history, setHistory] = useState<SerenityScoreRow[]>([])
  const [viewMode, setViewMode] = useState<"single" | "pool">("single")
  const [selectedTheme, setSelectedTheme] = useState("lending")
  const [poolScan, setPoolScan] = useState<SerenityPoolScan | null>(null)
  const [poolLoading, setPoolLoading] = useState(false)
  const [poolError, setPoolError] = useState<string | null>(null)
  const [expandedPoolRow, setExpandedPoolRow] = useState<string | null>(null)

  const handleScan = useCallback(async () => {
    setLoading(true); setMode("quick")
    try { const data = await api<SerenityReport>(`/api/v1/serenity/scan/${selectedSymbol}`); if (data) setReport(data) } finally { setLoading(false) }
  }, [selectedSymbol])

  const handleDeep = useCallback(async () => {
    setLoading(true); setMode("deep")
    try { const data = await api<SerenityReport>(`/api/v1/serenity/deep/${selectedSymbol}`); if (data) setReport(data) } finally { setLoading(false) }
  }, [selectedSymbol])

  const handlePoolScan = useCallback(async () => {
    setPoolLoading(true); setPoolError(null)
    try {
      const data = await api<SerenityPoolScan>(`/api/v1/serenity/pool/scan?theme=${selectedTheme}`)
      if (data && "ranked" in data) setPoolScan(data)
      else setPoolError("扫描未返回数据")
    } catch (e) {
      setPoolError(e instanceof Error ? e.message : "主题池扫描失败")
    } finally { setPoolLoading(false) }
  }, [selectedTheme])

  useEffect(() => {
    if (!report) return
    api<{ history: SerenityScoreRow[] }>(`/api/v1/serenity/history/${selectedSymbol}?limit=50`).then((data) => { if (data?.history) setHistory(data.history) }).catch(() => {})
  }, [report, selectedSymbol])

  const score = report?.tier2 ?? report?.tier1
  const radarData = useMemo(() => {
    if (!score) return []
    return Object.entries(FACTOR_LABELS).map(([key, label]) => ({
      factor: label, value: score.factors[key as keyof typeof score.factors],
      penalty: score.penalties[Object.keys(PENALTY_LABELS)[Object.keys(FACTOR_LABELS).indexOf(key)] as keyof typeof score.penalties] ?? 0,
    }))
  }, [score])

  const historyChartData = useMemo(() => {
    if (!history.length) return []
    return history.slice().reverse().map((row) => ({ time: new Date(row.timestamp).toLocaleDateString(), score: row.normalized_score }))
  }, [history])

  const factorRows = useMemo(() => {
    if (!score) return []
    const factors = Object.entries(FACTOR_LABELS).map(([key, label]) => ({ label, value: score.factors[key as keyof typeof score.factors], type: "factor" as const }))
    const penalties = Object.entries(PENALTY_LABELS).map(([key, label]) => ({ label, value: score.penalties[key as keyof typeof score.penalties], type: "penalty" as const }))
    return [...factors, ...penalties]
  }, [score])

  const signalIcon = report?.signal_direction === 1 ? <ArrowUpRight className="h-4 w-4 text-emerald-500" /> : report?.signal_direction === -1 ? <ArrowDownRight className="h-4 w-4 text-red-400" /> : <Minus className="h-4 w-4 text-zinc-400" />
  const signalLabel = report?.signal_direction === 1 ? "做多" : report?.signal_direction === -1 ? "做空" : "中性"

  return (
    <div className="space-y-4 h-full overflow-auto">
      <Card>
        <CardContent className="flex items-center gap-3 py-3">
          <Shield className="h-5 w-5 text-primary shrink-0" />
          <div className="flex items-center rounded-lg border border-border p-0.5">
            <Button variant={viewMode === "single" ? "default" : "ghost"} size="sm" className="h-7" onClick={() => setViewMode("single")}>单标的</Button>
            <Button variant={viewMode === "pool" ? "default" : "ghost"} size="sm" className="h-7" onClick={() => setViewMode("pool")}>主题池</Button>
          </div>
          {viewMode === "single" && (
            <>
              <Select value={selectedSymbol} onValueChange={setSelectedSymbol}><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent>{SYMBOLS.map((s) => (<SelectItem key={s} value={s}>{s}</SelectItem>))}</SelectContent></Select>
              <Button onClick={handleScan} disabled={loading} size="sm">{loading && mode === "quick" ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Search className="h-4 w-4 mr-1" />}快速扫描</Button>
              <Button onClick={handleDeep} disabled={loading} variant="outline" size="sm">{loading && mode === "deep" ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Eye className="h-4 w-4 mr-1" />}深度验证</Button>
              {score && (
                <div className="flex items-center gap-2 ml-auto">
                  <Badge variant="outline" className={TIER_BG[score.tier] ?? ""}><span className={TIER_COLORS[score.tier] ?? ""}>{score.tier.replace("_", " ")}</span></Badge>
                  <span className="text-2xl font-bold tabular-nums">{score.normalized_score?.toFixed(3) ?? "—"}</span>
                  {signalIcon}<span className="text-sm text-muted-foreground">{signalLabel}</span>
                </div>
              )}
            </>
          )}
          {viewMode === "pool" && (
            <>
              <Select value={selectedTheme} onValueChange={setSelectedTheme}><SelectTrigger className="w-36"><SelectValue /></SelectTrigger><SelectContent>{THEME_OPTIONS.map((t) => (<SelectItem key={t.key} value={t.key}>{t.name}</SelectItem>))}</SelectContent></Select>
              <Button onClick={handlePoolScan} disabled={poolLoading} size="sm">{poolLoading ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Layers className="h-4 w-4 mr-1" />}扫描主题池</Button>
              {poolScan && (
                <div className="flex items-center gap-2 ml-auto">
                  <Badge variant="outline">候选 {poolScan.universe_size}</Badge>
                  <Badge variant="outline" className="text-emerald-500">Top {poolScan.top_picks.length}</Badge>
                  {poolScan.weak_evidence_count > 0 && <Badge variant="outline" className="text-amber-500">弱证据 {poolScan.weak_evidence_count}</Badge>}
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {viewMode === "single" && (
        <>
      {!report && !loading && (<Card><CardContent className="flex flex-col items-center justify-center py-16 text-muted-foreground"><Shield className="h-12 w-12 mb-3 opacity-30" /><p>选择标的并点击"快速扫描"或"深度验证"</p></CardContent></Card>)}
      {loading && !report && (<div className="grid grid-cols-2 gap-4"><Card><CardContent className="py-8"><Skeleton className="h-64 w-full" /></CardContent></Card><Card><CardContent className="py-8"><Skeleton className="h-64 w-full" /></CardContent></Card></div>)}

      {score && report && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><BarChart3 className="h-4 w-4" />因子雷达</CardTitle></CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={300}>
                  <RadarChart data={radarData}>
                    <PolarGrid stroke="var(--color-border)" />
                    <PolarAngleAxis dataKey="factor" tick={{ fontSize: 11, fill: "var(--color-ink-3)" }} />
                    <PolarRadiusAxis angle={90} domain={[0, 1]} tick={false} axisLine={false} />
                    <Radar name="因子" dataKey="value" stroke="var(--color-accent)" fill="var(--color-accent)" fillOpacity={0.2} />
                    <Radar name="惩罚" dataKey="penalty" stroke="var(--color-destructive)" fill="var(--color-destructive)" fillOpacity={0.1} strokeDasharray="4 2" />
                  </RadarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">叙述与洞察</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <p className="text-sm font-medium leading-relaxed">{report.narrative}</p>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">系统变化</p>
                  <p className="text-sm flex items-start gap-1">
                    {report.system_change.includes("surge") || report.system_change.includes("growth") ? <TrendingUp className="h-4 w-4 text-emerald-500 shrink-0 mt-0.5" /> : report.system_change.includes("decline") || report.system_change.includes("shift") ? <TrendingDown className="h-4 w-4 text-red-400 shrink-0 mt-0.5" /> : <Minus className="h-4 w-4 text-zinc-400 shrink-0 mt-0.5" />}
                    {report.system_change}
                  </p>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1"><CheckCircle2 className="h-3 w-3 text-emerald-500" />稀缺约束</p>
                  <ul className="text-sm space-y-0.5">{report.scarce_constraints.map((c, i) => (<li key={i} className="text-muted-foreground">{c}</li>))}</ul>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1"><AlertTriangle className="h-3 w-3 text-amber-500" />盲点</p>
                  <ul className="text-sm space-y-0.5">{report.blind_spots.map((b, i) => (<li key={i} className="text-muted-foreground">{b}</li>))}</ul>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1"><XCircle className="h-3 w-3 text-red-400" />证伪条件</p>
                  <ul className="text-sm space-y-0.5">{report.disproof_conditions.slice(0, 3).map((d, i) => (<li key={i} className="text-muted-foreground">{d}</li>))}</ul>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card className="lg:col-span-2">
              <CardHeader className="pb-2"><CardTitle className="text-sm">因子 & 惩罚</CardTitle></CardHeader>
              <CardContent>
                <Table>
                  <TableHeader><TableRow><TableHead className="w-32">名称</TableHead><TableHead className="w-20">值</TableHead><TableHead>柱</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {factorRows.map((row) => (
                      <TableRow key={row.label}>
                        <TableCell className="text-xs font-medium">{row.label}</TableCell>
                        <TableCell className="text-xs tabular-nums">{row.value?.toFixed(3) ?? "—"}</TableCell>
                        <TableCell><div className="w-full h-3 rounded-full bg-muted overflow-hidden"><div className={`h-full rounded-full ${row.type === "factor" ? "bg-primary/60" : "bg-destructive/60"}`} style={{ width: `${Math.min(row.value * 100, 100)}%` }} /></div></TableCell>
                      </TableRow>
                    ))}
                    <TableRow><TableCell className="text-xs font-bold">原始分数</TableCell><TableCell className="text-xs font-bold tabular-nums" colSpan={2}>{score.raw_score?.toFixed(4) ?? "—"}</TableCell></TableRow>
                  </TableBody>
                </Table>
                {report.tier2 && <p className="text-xs text-muted-foreground mt-2">深度验证偏差: {Math.abs((report.tier2.normalized_score ?? 0) - (report.tier1.normalized_score ?? 0)).toFixed(3)}</p>}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">市场环境</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div><p className="text-xs text-muted-foreground">7日价格变化</p><p className={`font-medium tabular-nums ${pctColor(score?.price_change_7d ?? null)}`}>{formatPct(score?.price_change_7d ?? null)}</p></div>
                  <div><p className="text-xs text-muted-foreground">7日量变</p><p className={`font-medium tabular-nums ${pctColor(score?.volume_change_7d ?? null)}`}>{formatPct(score?.volume_change_7d ?? null)}</p></div>
                  <div><p className="text-xs text-muted-foreground">资金费率</p><p className="font-medium tabular-nums text-muted-foreground">{score?.funding_rate != null ? `${(score.funding_rate * 100).toFixed(4)}%` : "-"}</p></div>
                  <div><p className="text-xs text-muted-foreground">OI 变化</p><p className={`font-medium tabular-nums ${pctColor(score?.oi_change ?? null)}`}>{formatPct(score?.oi_change ?? null)}</p></div>
                </div>
                {historyChartData.length > 1 && (
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">历史评分</p>
                    <ResponsiveContainer width="100%" height={120}>
                      <LineChart data={historyChartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                        <XAxis dataKey="time" tick={{ fontSize: 9 }} />
                        <YAxis domain={[0, 1]} tick={{ fontSize: 9 }} />
                        <Tooltip />
                        <ReferenceLine y={0.55} stroke="var(--color-ink-3)" strokeDasharray="2 2" />
                        <ReferenceLine y={0.35} stroke="var(--color-ink-3)" strokeDasharray="2 2" />
                        <Line type="monotone" dataKey="score" stroke="var(--color-accent)" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
        </>
      )}

      {viewMode === "pool" && (
        <>
          {poolError && (
            <Card>
              <CardContent className="flex items-center gap-2 py-4 text-red-400">
                <AlertTriangle className="h-4 w-4 shrink-0" />{poolError}
              </CardContent>
            </Card>
          )}
          {poolLoading && !poolScan && (
            <Card><CardContent className="py-8"><Skeleton className="h-64 w-full" /></CardContent></Card>
          )}
          {!poolScan && !poolLoading && !poolError && (
            <Card><CardContent className="flex flex-col items-center justify-center py-16 text-muted-foreground"><Layers className="h-12 w-12 mb-3 opacity-30" /><p>选择主题并点击"扫描主题池"</p></CardContent></Card>
          )}
          {poolScan && (
            <>
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><Layers className="h-4 w-4" />主题层汇总</CardTitle></CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                  {poolScan.layer_summary.map((ls) => (
                    <Badge key={ls.layer} variant="outline" className="gap-1 px-3 py-1">
                      <span className="text-xs font-medium">{ls.layer}</span>
                      <span className="text-xs text-muted-foreground">{ls.count}</span>
                      {ls.top_scarce_symbols.length > 0 && <span className="text-xs text-emerald-500">★ {ls.top_scarce_symbols.join(", ")}</span>}
                    </Badge>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm flex items-center justify-between">
                    <span className="flex items-center gap-2"><BarChart3 className="h-4 w-4" />候选池排序</span>
                    <span className="text-xs font-normal text-muted-foreground">主:稀缺层 · 次:综合分</span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-10">#</TableHead>
                        <TableHead>标的</TableHead>
                        <TableHead className="w-16">综合分</TableHead>
                        <TableHead className="w-16">稀缺层</TableHead>
                        <TableHead className="w-14">证据</TableHead>
                        <TableHead className="w-24">Tier</TableHead>
                        <TableHead className="w-12">Pick</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {poolScan.ranked.map((c, i) => (
                        <Fragment key={c.symbol}>
                          <TableRow className="cursor-pointer" onClick={() => setExpandedPoolRow(expandedPoolRow === c.symbol ? null : c.symbol)}>
                            <TableCell className="text-xs text-muted-foreground">{i + 1}</TableCell>
                            <TableCell className="text-sm font-medium">{c.symbol}<span className="ml-2 text-xs text-muted-foreground">{c.name}</span></TableCell>
                            <TableCell className="text-sm tabular-nums">{c.normalized_score?.toFixed(3)}</TableCell>
                            <TableCell className="text-sm tabular-nums text-muted-foreground">{c.scarce_layer_score?.toFixed(3)}</TableCell>
                            <TableCell className={`text-xs ${c.evidence_label === "强" ? "text-emerald-500" : c.evidence_label === "中" ? "text-amber-500" : "text-zinc-400"}`}>{c.evidence_label}</TableCell>
                            <TableCell className={`text-xs ${TIER_COLORS[c.tier] ?? ""}`}>{c.tier.replace("_", " ")}</TableCell>
                            <TableCell>{poolScan.top_picks.includes(c.symbol) ? <span className="text-emerald-500">★</span> : <span className="text-zinc-600">·</span>}</TableCell>
                          </TableRow>
                          {expandedPoolRow === c.symbol && (
                            <TableRow>
                              <TableCell colSpan={7} className="bg-muted/40">
                                <div className="text-xs space-y-1.5 py-1">
                                  <p><span className="text-muted-foreground">类别:</span> {c.category} · <span className="text-muted-foreground">链:</span> {c.chains.join(", ")}</p>
                                  <p><span className="text-muted-foreground">卡点理由:</span> {c.rationale}</p>
                                  {c.top_penalty && <p><span className="text-muted-foreground">主要风险:</span> {c.top_penalty}</p>}
                                  {c.disproof.length > 0 && <p><span className="text-muted-foreground">证伪条件:</span> {c.disproof.join("；")}</p>}
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                        </Fragment>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </>
          )}
        </>
      )}
    </div>
  )
}
