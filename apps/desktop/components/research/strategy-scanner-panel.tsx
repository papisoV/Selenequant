"use client"

import React, { useState, useCallback, useEffect, useMemo } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Skeleton } from "@/components/ui/skeleton"
import { Progress } from "@/components/ui/progress"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Radar, Play, StopCircle, Download, TrendingUp, Star, Zap, RefreshCw } from "lucide-react"
import { api, apiPost } from "@/lib/api-client"
import { useWorkflowStore } from "@/lib/store/workflow"
import { useRouter } from "next/navigation"

const DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
const DEFAULT_INTERVALS = ["1h", "4h", "1d"]

const TEMPLATE_CATEGORIES = [
  { id: "trend", name: "趋势跟踪" },
  { id: "oscillator", name: "震荡指标" },
  { id: "volatility", name: "波动率" },
  { id: "volume", name: "成交量" },
  { id: "mean_reversion", name: "均值回归" },
  { id: "other", name: "其他" },
]

const SORT_METRICS = [
  { id: "sharpe", name: "夏普比率" },
  { id: "totalReturn", name: "总收益" },
  { id: "winRate", name: "胜率" },
  { id: "profitFactor", name: "盈亏比" },
]

interface FactoryTemplate { id: string; name: string; name_zh?: string; category?: string; default_params?: Record<string, unknown> }
interface TemplateWithCategory extends FactoryTemplate { categoryKey: string }

interface ScannerResult { templateId: string; templateName: string; symbol: string; interval: string; totalReturn: number; sharpe: number; maxDrawdown: number; winRate: number; totalTrades: number; profitFactor: number }

interface ScannerTask { id: string; status: "pending" | "running" | "completed" | "failed"; result?: ScannerResult }

export function StrategyScannerPanel() {
  const [templates, setTemplates] = useState<TemplateWithCategory[]>([])
  const [loadingTemplates, setLoadingTemplates] = useState(true)
  const [selectedTemplates, setSelectedTemplates] = useState<Set<string>>(new Set())
  const [selectedSymbols, setSelectedSymbols] = useState<Set<string>>(new Set(["BTC/USDT"]))
  const [selectedIntervals, setSelectedIntervals] = useState<Set<string>>(new Set(["1h"]))
  const [sortMetric, setSortMetric] = useState<"sharpe" | "totalReturn" | "winRate" | "profitFactor">("sharpe")
  const [showHeatmap, setShowHeatmap] = useState(false)
  const [tasks, setTasks] = useState<ScannerTask[]>([])
  const [running, setRunning] = useState(false)
  const setOptimization = useWorkflowStore((s) => s.setOptimization)
  const router = useRouter()

  useEffect(() => { loadTemplates() }, [])

  const loadTemplates = async () => {
    setLoadingTemplates(true)
    try {
      const data = await api<FactoryTemplate[]>("/api/v1/strategies/factory-templates")
      const list = Array.isArray(data) ? data : []
      setTemplates(list.map((t) => ({ ...t, categoryKey: t.category || "other" })))
    } catch {
      // fallback empty
    }
    setLoadingTemplates(false)
  }

  const templatesByCategory = useMemo(() => { const grouped: Record<string, TemplateWithCategory[]> = {}; for (const template of templates) { const cat = template.categoryKey; if (!grouped[cat]) grouped[cat] = []; grouped[cat].push(template) } return grouped }, [templates])
  const completedResults = useMemo(() => tasks.filter((t) => t.status === "completed" && t.result).map((t) => t.result!), [tasks])
  const topResults = useMemo(() => { return [...completedResults].sort((a, b) => { const va = a[sortMetric]; const vb = b[sortMetric]; return (vb as number) - (va as number) }).slice(0, 20) }, [completedResults, sortMetric])
  const resultsMap = useMemo(() => { const m = new Map<string, Map<string, ScannerResult>>(); for (const r of completedResults) { if (!m.has(r.templateId)) m.set(r.templateId, new Map()); m.get(r.templateId)!.set(`${r.symbol}_${r.interval}`, r) } return m }, [completedResults])

  const toggleTemplate = useCallback((templateId: string) => { setSelectedTemplates((prev) => { const next = new Set(prev); if (next.has(templateId)) { next.delete(templateId) } else { next.add(templateId) }; return next }) }, [])
  const toggleSymbol = useCallback((symbol: string) => { setSelectedSymbols((prev) => { const next = new Set(prev); if (next.has(symbol)) { next.delete(symbol) } else { next.add(symbol) }; return next }) }, [])
  const toggleInterval = useCallback((interval: string) => { setSelectedIntervals((prev) => { const next = new Set(prev); if (next.has(interval)) { next.delete(interval) } else { next.add(interval) }; return next }) }, [])
  const selectAllInCategory = useCallback((category: string) => { const catTemplates = templatesByCategory[category] || []; setSelectedTemplates((prev) => { const next = new Set(prev); for (const t of catTemplates) { next.add(t.id) }; return next }) }, [templatesByCategory])

  const startScan = useCallback(async () => {
    const selectedTemplateList = templates.filter((t) => selectedTemplates.has(t.id))
    const symbolList = Array.from(selectedSymbols)
    const intervalList = Array.from(selectedIntervals)
    if (selectedTemplateList.length === 0 || symbolList.length === 0 || intervalList.length === 0) return

    setRunning(true); setTasks([])
    const allTasks: ScannerTask[] = []
    let completedCount = 0

    for (const tmpl of selectedTemplateList) {
      for (const sym of symbolList) {
        for (const iv of intervalList) {
          const taskId = `${tmpl.id}_${sym}_${iv}`
          allTasks.push({ id: taskId, status: "pending" })
        }
      }
    }
    setTasks(allTasks)

    for (let i = 0; i < allTasks.length; i++) {
      const tmpl = selectedTemplateList.find((t) => allTasks[i].id.startsWith(t.id))
      if (!tmpl) continue
      const parts = allTasks[i].id.replace(`${tmpl.id}_`, "").split("_")
      const sym = parts[0]; const iv = parts[1]
      setTasks((prev) => prev.map((t, idx) => idx === i ? { ...t, status: "running" } : t))
      try {
        const runData = await apiPost<{ task_id: string }>("/api/v1/backtests", { strategy_id: tmpl.id, symbol: sym, interval: iv, initial_capital: 10000 })
        const btData = await api<{ total_return?: number; sharpe_ratio?: number; max_drawdown?: number; win_rate?: number; total_trades?: number; profit_factor?: number }>(`/api/v1/backtests/${runData.task_id}`)
        const result: ScannerResult = { templateId: tmpl.id, templateName: tmpl.name_zh || tmpl.name, symbol: sym, interval: iv, totalReturn: (btData.total_return ?? 0) * 100, sharpe: btData.sharpe_ratio ?? 0, maxDrawdown: (btData.max_drawdown ?? 0) * 100, winRate: btData.win_rate ?? 0, totalTrades: btData.total_trades ?? 0, profitFactor: btData.profit_factor ?? 0 }
        setTasks((prev) => prev.map((t, idx) => idx === i ? { ...t, status: "completed", result } : t))
      } catch {
        setTasks((prev) => prev.map((t, idx) => idx === i ? { ...t, status: "failed" } : t))
      }
      completedCount++
    }
    setRunning(false)
  }, [templates, selectedTemplates, selectedSymbols, selectedIntervals])

  const stopScan = useCallback(() => { setRunning(false) }, [])
  const exportResults = useCallback(() => {
    if (completedResults.length === 0) return
    const csv = ["策略,交易对,周期,收益%,夏普,最大回撤%,胜率%,交易数,盈亏比", ...completedResults.map((r) => `${r.templateName},${r.symbol},${r.interval},${(r.totalReturn ?? 0).toFixed(2)},${(r.sharpe ?? 0).toFixed(2)},${(r.maxDrawdown ?? 0).toFixed(2)},${((r.winRate ?? 0) * 100).toFixed(1)},${r.totalTrades},${(r.profitFactor ?? 0).toFixed(2)}`)].join("\n")
    const blob = new Blob([csv], { type: "text/csv" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `scanner-results-${new Date().toISOString().slice(0, 10)}.csv`; a.click(); URL.revokeObjectURL(url)
  }, [completedResults])

  const estimatedTasks = selectedTemplates.size * selectedSymbols.size * selectedIntervals.size
  const progressPct = tasks.length > 0 ? Math.round((completedResults.length / tasks.length) * 100) : 0

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><Radar className="h-4 w-4" />策略扫描配置</CardTitle></CardHeader>
        <CardContent>
          {loadingTemplates ? (<div className="space-y-2"><Skeleton className="h-8 w-full" /><p className="text-sm text-muted-foreground">加载策略模板...</p></div>) : (
            <>
              <div className="space-y-4">
                <div className="flex items-center justify-between"><label className="text-sm font-medium">策略模板</label><Badge variant="outline">{selectedTemplates.size} / {templates.length}</Badge></div>
                {TEMPLATE_CATEGORIES.map((cat) => { const catTemplates = templatesByCategory[cat.id] || []; if (catTemplates.length === 0) return null; return (
                  <div key={cat.id} className="space-y-2">
                    <div className="flex items-center gap-2"><span className="text-xs font-medium text-muted-foreground">{cat.name}</span><Button variant="ghost" size="sm" className="h-5 px-2 text-xs" onClick={() => selectAllInCategory(cat.id)}>全选</Button></div>
                    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2">
                      {catTemplates.map((template) => (
                        <div key={template.id} className={`flex items-center space-x-2 p-2 rounded border ${selectedTemplates.has(template.id) ? "border-primary bg-primary/5" : "border-border"}`}>
                          <Checkbox id={`template-${template.id}`} checked={selectedTemplates.has(template.id)} onCheckedChange={() => toggleTemplate(template.id)} />
                          <label htmlFor={`template-${template.id}`} className="text-xs cursor-pointer truncate">{template.name_zh || template.name}</label>
                        </div>
                      ))}
                    </div>
                  </div>
                ) })}
              </div>
              <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2"><label className="text-sm font-medium">交易对</label><div className="flex flex-wrap gap-2">{DEFAULT_SYMBOLS.map((symbol) => (<Badge key={symbol} variant={selectedSymbols.has(symbol) ? "default" : "outline"} className="cursor-pointer" onClick={() => toggleSymbol(symbol)}>{symbol}</Badge>))}</div></div>
                <div className="space-y-2"><label className="text-sm font-medium">周期</label><div className="flex flex-wrap gap-2">{DEFAULT_INTERVALS.map((interval) => (<Badge key={interval} variant={selectedIntervals.has(interval) ? "default" : "outline"} className="cursor-pointer" onClick={() => toggleInterval(interval)}>{interval}</Badge>))}</div></div>
              </div>
              <div className="mt-4 flex items-center justify-between">
                <div className="text-sm text-muted-foreground">预计 {estimatedTasks} 次回测任务</div>
                <div className="flex items-center gap-2">
                  {!running ? (<Button onClick={startScan} disabled={estimatedTasks === 0}><Play className="h-4 w-4 mr-2" />开始扫描</Button>) : (<Button onClick={stopScan} variant="destructive"><StopCircle className="h-4 w-4 mr-2" />停止</Button>)}
                </div>
              </div>
              {running && (<div className="mt-4 space-y-2"><Progress value={progressPct} className="h-2" /><div className="flex justify-between text-sm text-muted-foreground"><span>已完成 {completedResults.length} / {tasks.length}</span><span>失败 {tasks.filter(t => t.status === "failed").length}</span></div></div>)}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base"><TrendingUp className="h-4 w-4" />扫描结果</CardTitle>
            <div className="flex items-center gap-2">
              <Select value={sortMetric} onValueChange={(v) => setSortMetric(v as typeof sortMetric)}><SelectTrigger className="w-32 h-8"><SelectValue /></SelectTrigger><SelectContent>{SORT_METRICS.map((m) => (<SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>))}</SelectContent></Select>
              <Button variant="outline" size="sm" onClick={exportResults} disabled={completedResults.length === 0}><Download className="h-3 w-3 mr-1" />CSV</Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {completedResults.length > 0 ? (
            <>
              <Table>
                <TableHeader><TableRow><TableHead>策略</TableHead><TableHead>交易对</TableHead><TableHead>周期</TableHead><TableHead className="text-right">收益</TableHead><TableHead className="text-right">夏普</TableHead><TableHead className="text-right">最大回撤</TableHead><TableHead className="text-right">胜率</TableHead><TableHead className="text-right">交易数</TableHead><TableHead className="w-[80px]">操作</TableHead></TableRow></TableHeader>
                <TableBody>
                  {topResults.map((result, idx) => (
                    <TableRow key={`${result.templateId}-${result.symbol}-${result.interval}`} className={idx === 0 ? "bg-emerald-500/10" : idx < 3 ? "bg-emerald-500/5" : ""}>
                      <TableCell className="font-medium text-xs"><div className="flex items-center gap-1">{idx < 3 && (<Star className={`h-3 w-3 ${idx === 0 ? "fill-yellow-400 text-yellow-400" : "fill-yellow-200 text-yellow-200"}`} />)}{result.templateName}</div></TableCell>
                      <TableCell className="text-xs">{result.symbol}</TableCell>
                      <TableCell className="text-xs">{result.interval}</TableCell>
                      <TableCell className="text-right"><span className={(result.totalReturn ?? 0) >= 0 ? "text-emerald-500" : "text-red-500"}>{(result.totalReturn ?? 0) >= 0 ? "+" : ""}{result.totalReturn?.toFixed(1) ?? "—"}%</span></TableCell>
                      <TableCell className="text-right font-mono text-xs">{result.sharpe?.toFixed(2) ?? "—"}</TableCell>
                      <TableCell className="text-right"><span className="text-red-400">-{result.maxDrawdown?.toFixed(1) ?? "—"}%</span></TableCell>
                      <TableCell className="text-right">{((result.winRate ?? 0) * 100).toFixed(0)}%</TableCell>
                      <TableCell className="text-right text-xs">{result.totalTrades}</TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={() => {
                            setOptimization({
                              source: "scanner",
                              sharpe: result.sharpe,
                              params: {},
                              symbol: result.symbol,
                              interval: result.interval,
                              pickedAt: Date.now(),
                            });
                            router.push("/trade/backtests");
                          }}
                        >
                          调入回测
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="mt-4 flex items-center gap-4">
                <Button variant="outline" size="sm" onClick={() => setShowHeatmap(!showHeatmap)}><Zap className="h-3 w-3 mr-1" />热力图</Button>
                <span className="text-sm text-muted-foreground">共 {completedResults.length} 个有效结果</span>
              </div>
              {showHeatmap && (
                <div className="mt-4 p-4 border rounded bg-muted/30">
                  <div className="text-sm font-medium mb-2">策略x交易对x周期 矩阵</div>
                  <div className="grid gap-1">
                    {Array.from(resultsMap.entries()).map(([templateId, symbolIntervalMap]) => { const template = templates.find((t) => t.id === templateId); return (
                      <div key={templateId} className="flex items-center gap-2">
                        <span className="w-32 text-xs truncate">{template?.name_zh || template?.name || templateId}</span>
                        <div className="flex gap-1">{Array.from(symbolIntervalMap.entries()).map(([key, result]) => (<div key={key} className={`w-8 h-8 rounded flex items-center justify-center text-xs font-mono ${result.sharpe >= 2 ? "bg-emerald-500 text-white" : result.sharpe >= 1 ? "bg-emerald-300 text-black" : result.sharpe >= 0 ? "bg-yellow-200 text-black" : "bg-red-300 text-black"}`} title={`${result.symbol} ${result.interval}: 夏普 ${result.sharpe?.toFixed(2) ?? "—"}`}>{result.sharpe.toFixed(1)}</div>))}</div>
                      </div>
                    ) })}
                  </div>
                </div>
              )}
            </>
          ) : (<div className="text-sm text-muted-foreground text-center py-8">{running ? "正在扫描..." : "选择策略模板、交易对和周期，然后点击\"开始扫描\""}</div>)}
        </CardContent>
      </Card>
    </div>
  )
}
