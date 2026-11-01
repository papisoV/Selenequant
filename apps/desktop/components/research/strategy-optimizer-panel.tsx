"use client"

import React, { useState, useCallback, useMemo, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Slider } from "@/components/ui/slider"
import { Progress } from "@/components/ui/progress"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Gauge, Play, Download, RefreshCw, Star, TrendingUp, StopCircle } from "lucide-react"
import { api, apiPost } from "@/lib/api-client"
import { useWorkflowStore } from "@/lib/store/workflow"
import { useRouter } from "next/navigation"
import { useUiText, useLocale } from "@/components/i18n/use-ui-text"

const DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
const DEFAULT_INTERVALS = ["1h", "4h", "1d"]

const OPTIMIZATION_OBJECTIVES = [
  { id: "sharpe", name: "夏普比率" },
  { id: "return", name: "总收益" },
  { id: "calmar", name: "卡尔马比率" },
  { id: "sortino", name: "索提诺比率" },
  { id: "win_rate", name: "胜率" },
]

const OPTIMIZATION_METHODS = [
  { id: "grid", name: "网格搜索" },
  { id: "bayesian", name: "贝叶斯优化" },
  { id: "genetic", name: "遗传算法" },
]

interface ParamRange { min: number; max: number; current: number }
interface OptimizationResult { params: Record<string, number>; return_pct: number; sharpe: number; max_drawdown: number; win_rate?: number; total_trades?: number; score: number; task_id?: string }
interface StrategyTemplate { id: string; name: string; nameZh?: string; params: string[] }
interface FactoryTemplate { id: string; name: string; name_zh?: string; default_params?: Record<string, unknown> }

/**
 * 模板显示名:`name`(英文名)与 `name_zh`(中文名)是**一对字段**,不是同一条文案的两种译法。
 * 跟 `indicator-library.tsx` 同一范式 —— zh 下显示中文名,en 下用英文名。
 * 曾经这里恒取 `name_zh`,en 模式下整列策略名停在中文。
 */
function templateName(t: StrategyTemplate, showZh: boolean): string {
  return showZh ? (t.nameZh || t.name) : t.name
}


function generateGridCombinations(paramRanges: Record<string, ParamRange>, stepsPerParam: number = 5): Record<string, number>[] {
  const paramKeys = Object.keys(paramRanges); const combinations: Record<string, number>[] = []
  const paramSteps: Record<string, number[]> = {}
  for (const key of paramKeys) { const range = paramRanges[key]; const stepSize = (range.max - range.min) / (stepsPerParam - 1); paramSteps[key] = []; for (let i = 0; i < stepsPerParam; i++) { paramSteps[key].push(Math.round(range.min + i * stepSize)) } }
  function generate(idx: number, current: Record<string, number>) { if (idx === paramKeys.length) { combinations.push({ ...current }); return } const key = paramKeys[idx]; for (const val of paramSteps[key]) { current[key] = val; generate(idx + 1, current) } }
  generate(0, {}); return combinations.slice(0, 50)
}

export function StrategyOptimizerPanel() {
  const tr = useUiText()
  const showNameZh = useLocale() !== "en"

  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [loadingStrategies, setLoadingStrategies] = useState(true)
  const [selectedStrategy, setSelectedStrategy] = useState("")
  const [selectedSymbol, setSelectedSymbol] = useState("BTC/USDT")
  const [selectedInterval, setSelectedInterval] = useState("1h")
  const [objective, setObjective] = useState("sharpe")
  const [method, setMethod] = useState("grid")
  const [paramRanges, setParamRanges] = useState<Record<string, ParamRange>>({})
  const [optimizing, setOptimizing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [completedCount, setCompletedCount] = useState(0)
  const [totalCombinations, setTotalCombinations] = useState(0)
  const [results, setResults] = useState<OptimizationResult[] | null>(null)
  const [abortController, setAbortController] = useState<AbortController | null>(null)
  const setOptimization = useWorkflowStore((s) => s.setOptimization)
  const router = useRouter()

  useEffect(() => { loadStrategies() }, [])

  const loadStrategies = async () => {
    setLoadingStrategies(true)
    try {
      const data = await api<FactoryTemplate[]>("/api/v1/strategies/factory-templates")
      const list = Array.isArray(data) ? data : []
      const templates: StrategyTemplate[] = list.map((t: FactoryTemplate) => ({
        id: t.id, name: t.name, nameZh: t.name_zh,
        params: Object.keys(t.default_params || {}).filter((k) => typeof t.default_params![k] === "number"),
      }))
      setStrategies(templates)
      if (templates.length > 0) { setSelectedStrategy(templates[0].id); initParamRanges(templates[0]) }
    } catch {
      // fallback empty
    }
    setLoadingStrategies(false)
  }

  const initParamRanges = (strategy: StrategyTemplate) => {
    const newRanges: Record<string, ParamRange> = {}
    for (const param of strategy.params) {
      if (param.includes("short") || param.includes("fast") || param.includes("oversold")) { newRanges[param] = { min: 5, max: 50, current: 15 } }
      else if (param.includes("long") || param.includes("slow") || param.includes("overbought")) { newRanges[param] = { min: 20, max: 200, current: 50 } }
      else if (param.includes("period") || param.includes("len")) { newRanges[param] = { min: 5, max: 50, current: 14 } }
      else if (param.includes("threshold") || param.includes("std") || param.includes("mult")) { newRanges[param] = { min: 1, max: 5, current: 2 } }
      else if (param.includes("signal")) { newRanges[param] = { min: 5, max: 15, current: 9 } }
      else if (param.includes("deviation") || param.includes("hold")) { newRanges[param] = { min: 1, max: 20, current: 5 } }
      else { newRanges[param] = { min: 5, max: 50, current: 20 } }
    }
    setParamRanges(newRanges); setResults(null)
  }

  const handleStrategyChange = useCallback((strategyId: string) => { setSelectedStrategy(strategyId); const strategy = strategies.find((s) => s.id === strategyId); if (strategy) { initParamRanges(strategy) } }, [strategies])
  const updateParamRange = useCallback((param: string, values: number[]) => { setParamRanges((prev) => ({ ...prev, [param]: { ...prev[param], min: values[0], max: values[1] } })) }, [])

  const startOptimization = useCallback(async () => {
    if (!selectedStrategy || Object.keys(paramRanges).length === 0) return
    setOptimizing(true); setProgress(0); setCompletedCount(0); setResults(null)
    const controller = new AbortController(); setAbortController(controller)
    const combinations = generateGridCombinations(paramRanges, 5); setTotalCombinations(combinations.length)
    const allResults: OptimizationResult[] = []
    try {
      for (let i = 0; i < combinations.length; i++) {
        if (controller.signal.aborted) break
        try {
          const runData = await apiPost<{ task_id: string }>("/api/v1/backtests", { strategy_id: selectedStrategy, symbol: selectedSymbol, interval: selectedInterval, parameters: combinations[i], initial_capital: 10000 })
          const btData = await api<{ total_return?: number; sharpe_ratio?: number; max_drawdown?: number; win_rate?: number; total_trades?: number; id?: string }>(`/api/v1/backtests/${runData.task_id}`)
          allResults.push({ params: combinations[i], return_pct: (btData.total_return ?? 0) * 100, sharpe: btData.sharpe_ratio ?? 0, max_drawdown: (btData.max_drawdown ?? 0) * 100, win_rate: btData.win_rate ?? 0, total_trades: btData.total_trades ?? 0, score: 0, task_id: btData.id })
        } catch {
          // skip failed combo
        }
        setCompletedCount(i + 1); setProgress(Math.round((i + 1) / combinations.length * 100))
      }
      const scoredResults = allResults.map((r) => { let score = 0; switch (objective) { case "sharpe": score = r.sharpe; break; case "return": score = r.return_pct; break; case "calmar": score = r.sharpe / (r.max_drawdown || 1); break; case "sortino": score = r.sharpe * 1.5; break; case "win_rate": score = (r.win_rate || 0) * 100; break; default: score = r.sharpe } return { ...r, score } })
      scoredResults.sort((a, b) => b.score - a.score); setResults(scoredResults)
    } finally { setOptimizing(false); setAbortController(null) }
  }, [selectedStrategy, selectedSymbol, selectedInterval, paramRanges, objective])

  const stopOptimization = useCallback(() => { if (abortController) { abortController.abort(); setOptimizing(false) } }, [abortController])
  const getScoreStars = (score: number): number => { if (score >= 4) return 5; if (score >= 3) return 4; if (score >= 2) return 3; if (score >= 1) return 2; return 1 }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><Gauge className="h-4 w-4" />{tr("策略参数优化")}</CardTitle></CardHeader>
        <CardContent>
          {loadingStrategies ? (<div className="space-y-2"><Skeleton className="h-8 w-full" /><p className="text-sm text-muted-foreground">{tr("加载策略模板...")}</p></div>) : (
            <>
              <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                <div className="space-y-2"><label className="text-sm font-medium">{tr("策略")}</label><Select value={selectedStrategy} onValueChange={handleStrategyChange}><SelectTrigger><SelectValue placeholder={tr("选择策略")}>{strategies.find((s) => s.id === selectedStrategy) ? templateName(strategies.find((s) => s.id === selectedStrategy)!, showNameZh) : undefined}</SelectValue></SelectTrigger><SelectContent>{strategies.map((s) => (<SelectItem key={s.id} value={s.id}>{templateName(s, showNameZh)}</SelectItem>))}</SelectContent></Select></div>
                <div className="space-y-2"><label className="text-sm font-medium">{tr("交易对")}</label><Select value={selectedSymbol} onValueChange={setSelectedSymbol}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{DEFAULT_SYMBOLS.map((s) => (<SelectItem key={s} value={s}>{s}</SelectItem>))}</SelectContent></Select></div>
                <div className="space-y-2"><label className="text-sm font-medium">{tr("周期")}</label><Select value={selectedInterval} onValueChange={setSelectedInterval}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{DEFAULT_INTERVALS.map((i) => (<SelectItem key={i} value={i}>{i}</SelectItem>))}</SelectContent></Select></div>
                <div className="space-y-2"><label className="text-sm font-medium">{tr("优化目标")}</label><Select value={objective} onValueChange={setObjective}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{OPTIMIZATION_OBJECTIVES.map((o) => (<SelectItem key={o.id} value={o.id}>{tr(o.name)}</SelectItem>))}</SelectContent></Select></div>
                <div className="space-y-2"><label className="text-sm font-medium">{tr("优化方法")}</label><Select value={method} onValueChange={setMethod}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{OPTIMIZATION_METHODS.map((m) => (<SelectItem key={m.id} value={m.id}>{tr(m.name)}</SelectItem>))}</SelectContent></Select></div>
              </div>
              <div className="mt-4 space-y-4">
                <label className="text-sm font-medium">{tr("参数范围")}</label>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {Object.entries(paramRanges).map(([param, range]) => (
                    <div key={param} className="space-y-2">
                      <div className="flex justify-between text-sm"><span>{param}</span><span className="text-muted-foreground">{range.min} - {range.max}</span></div>
                      <Slider value={[range.min, range.max]} min={1} max={300} step={1} onValueChange={(values) => updateParamRange(param, values)} />
                    </div>
                  ))}
                </div>
              </div>
              <div className="mt-4 flex items-center gap-4">
                {!optimizing ? (<Button onClick={startOptimization} disabled={strategies.length === 0 || Object.keys(paramRanges).length === 0}><Play className="h-4 w-4 mr-2" />{tr("开始优化")}</Button>) : (<Button onClick={stopOptimization} variant="destructive"><StopCircle className="h-4 w-4 mr-2" />{tr("停止优化")}</Button>)}
                {optimizing && (<div className="flex-1 max-w-md"><Progress value={progress} className="h-2" /><p className="text-sm text-muted-foreground mt-1">{tr("正在优化... 已完成")} {completedCount} / {totalCombinations} {tr("次回测 (")}{progress}%)</p></div>)}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base">{tr("优化结果")}</CardTitle></CardHeader>
        <CardContent>
          {results ? (
            <>
              <Table>
                <TableHeader><TableRow><TableHead>{tr("参数")}</TableHead><TableHead className="text-right">{tr("收益")}</TableHead><TableHead className="text-right">{tr("夏普")}</TableHead><TableHead className="text-right">{tr("最大回撤")}</TableHead><TableHead className="text-right">{tr("评分")}</TableHead></TableRow></TableHeader>
                <TableBody>
                  {results.slice(0, 5).map((row, idx) => (
                    <TableRow key={idx} className={idx === 0 ? "bg-emerald-500/10" : ""}>
                      <TableCell className="font-mono text-xs">{Object.entries(row.params).map(([k, v]) => `${k.split("_").pop()}=${v}`).join(", ")}</TableCell>
                      <TableCell className="text-right"><span className={(row.return_pct ?? 0) >= 0 ? "text-emerald-500" : "text-red-500"}>{(row.return_pct ?? 0) >= 0 ? "+" : ""}{row.return_pct?.toFixed(1) ?? "—"}%</span></TableCell>
                      <TableCell className="text-right">{row.sharpe?.toFixed(2) ?? "—"}</TableCell>
                      <TableCell className="text-right"><span className="text-red-400">-{row.max_drawdown?.toFixed(1) ?? "—"}%</span></TableCell>
                      <TableCell className="text-right"><div className="flex items-center justify-end gap-1">{Array.from({ length: getScoreStars(row.score) }).map((_, i) => (<Star key={i} className="h-3 w-3 fill-yellow-400 text-yellow-400" />))}</div></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="mt-4 flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!results || results.length === 0}
                  onClick={() => {
                    if (!results || results.length === 0) return;
                    const best = results[0];
                    setOptimization({
                      source: "optimizer",
                      taskId: best.task_id,
                      sharpe: best.sharpe,
                      params: best.params,
                      interval: selectedInterval,
                      symbol: selectedSymbol,
                      pickedAt: Date.now(),
                    });
                    router.push("/trade/backtests");
                  }}
                >
                  {tr("应用最优参数")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!results || results.length === 0}
                  onClick={() => {
                    if (!results || results.length === 0) return;
                    const json = JSON.stringify(results, null, 2);
                    const blob = new Blob([json], { type: "application/json" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `optimizer-result-${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.json`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  <Download className="h-3 w-3 mr-2" />{tr("导出结果")}
                </Button>
              </div>
            </>
          ) : (<div className="text-sm text-muted-foreground text-center py-8">{tr("配置参数后点击\"开始优化\"")}</div>)}
        </CardContent>
      </Card>
    </div>
  )
}
