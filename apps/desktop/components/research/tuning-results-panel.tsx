"use client"

import React, { useState, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Brain, TrendingUp, TrendingDown, Clock, Target, RefreshCw, ChevronDown, ChevronUp, ExternalLink, BarChart3, ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react"
import { api } from "@/lib/api-client"
import { useWorkflowStore } from "@/lib/store/workflow"
import { useRouter } from "next/navigation"
import { applyTuningResult as applyTuningResultBridge, type TuningResultSummary } from "@/lib/tuning-bridge"

interface TuningResultDetail {
  file_name: string; best_params: Record<string, number>
  best_sharpe: number; n_trials: number; optimization_time: number
  exchange: string; market_type: string; symbol: string
  backtest_result: {
    total_trades?: number; profit_factor?: number; calmar_ratio?: number
    sortino_ratio?: number; annual_return?: number; expectancy?: number
    max_drawdown?: number; win_rate?: number; total_return?: number
    [key: string]: unknown
  }
}

export function TuningResultsPanel() {
  const [results, setResults] = useState<TuningResultSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [detailCache, setDetailCache] = useState<Record<string, TuningResultDetail>>({})
  const [creatingStrategy, setCreatingStrategy] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<keyof TuningResultSummary | null>(null)
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")
  const setOptimization = useWorkflowStore((s) => s.setOptimization)
  const router = useRouter()

  type SortableKey = keyof TuningResultSummary

  const handleSort = (key: SortableKey) => { if (sortKey === key) { setSortDir(prev => prev === "asc" ? "desc" : "asc") } else { setSortKey(key); setSortDir("desc") } }

  const sortedResults = React.useMemo(() => {
    if (!sortKey) return results
    return [...results].sort((a, b) => {
      const va = a[sortKey]; const vb = b[sortKey]
      if (va === vb) return 0; if (va == null) return 1; if (vb == null) return -1
      const cmp = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb))
      return sortDir === "asc" ? cmp : -cmp
    })
  }, [results, sortKey, sortDir])

  const SortIcon = ({ column }: { column: SortableKey }) => { if (sortKey !== column) return <ArrowUpDown className="h-3 w-3 ml-1 opacity-40" />; return sortDir === "asc" ? <ArrowUp className="h-3 w-3 ml-1" /> : <ArrowDown className="h-3 w-3 ml-1" /> }

  const fetchResults = async () => {
    setLoading(true); setError(null)
    try {
      const data = await api<TuningResultSummary[]>("/api/v1/research/tuning/results")
      setResults(data ?? [])
    } catch { setError("获取调参结果失败") }
    finally { setLoading(false) }
  }

  const fetchDetail = async (fileName: string) => {
    if (detailCache[fileName]) return
    try {
      const data = await api<TuningResultDetail>(`/api/v1/research/tuning/results/${fileName}`)
      if (data) setDetailCache(prev => ({ ...prev, [fileName]: data }))
    } catch { /* ignore */ }
  }

  useEffect(() => { fetchResults() }, [])

  const toggleRow = async (fileName: string) => { if (expandedRow === fileName) { setExpandedRow(null) } else { setExpandedRow(fileName); await fetchDetail(fileName) } }

  const goToStudio = async (result: TuningResultSummary) => {
    setCreatingStrategy(result.strategy_id)
    try {
      await applyTuningResultBridge(result, router, setOptimization)
    } catch (err) {
      console.error("[tuning-panel] goToStudio failed:", err)
    } finally { setCreatingStrategy(null) }
  }

  const formatPercent = (value: number) => `${(value * 100).toFixed(2)}%`
  const formatSharpe = (value: number) => { const color = value > 1 ? "text-green-500" : value > 0 ? "text-yellow-500" : "text-red-500"; return <span className={color}>{value.toFixed(3)}</span> }
  const formatProfitFactor = (value: number | undefined) => { if (value === undefined || value === null) return "-"; if (!isFinite(value)) return <span className="text-green-500">inf</span>; const color = value > 2 ? "text-green-500" : value > 1 ? "text-yellow-500" : "text-red-500"; return <span className={color}>{value.toFixed(2)}</span> }

  if (loading) { return (<Card><CardHeader><CardTitle className="flex items-center gap-2"><Brain className="h-5 w-5" />调参结果</CardTitle></CardHeader><CardContent><div className="space-y-3">{[1, 2, 3].map(i => (<Skeleton key={i} className="h-12 w-full" />))}</div></CardContent></Card>) }
  if (error) { return (<Card><CardHeader><CardTitle className="flex items-center gap-2"><Brain className="h-5 w-5" />调参结果</CardTitle></CardHeader><CardContent><p className="text-red-500">{error}</p><Button variant="outline" size="sm" className="mt-2" onClick={fetchResults}><RefreshCw className="h-4 w-4 mr-2" />重试</Button></CardContent></Card>) }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 justify-between">
          <div className="flex items-center gap-2"><Brain className="h-5 w-5" />调参结果</div>
          <Button variant="outline" size="sm" onClick={fetchResults}><RefreshCw className="h-4 w-4 mr-2" />刷新</Button>
        </CardTitle>
        <p className="text-sm text-muted-foreground">共 {results.length} 条调参记录</p>
      </CardHeader>
      <CardContent>
        {results.some(r => r.pre_noop_fix) && (
          <div className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-500">
            ⚠ 2026-10-01 优化器采样修复前的历史榜单:best_params 未真正参与回测,仅供参考
          </div>
        )}
        {results.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground"><Brain className="h-12 w-12 mx-auto mb-4 opacity-50" /><p>暂无调参结果。请先运行参数优化。</p></div>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[30px]"></TableHead>
                  <TableHead><button className="inline-flex items-center hover:text-foreground transition-colors" onClick={() => handleSort("strategy_name")}>策略<SortIcon column="strategy_name" /></button></TableHead>
                  <TableHead><button className="inline-flex items-center hover:text-foreground transition-colors" onClick={() => handleSort("exchange")}>市场<SortIcon column="exchange" /></button></TableHead>
                  <TableHead><button className="inline-flex items-center hover:text-foreground transition-colors" onClick={() => handleSort("interval")}>周期<SortIcon column="interval" /></button></TableHead>
                  <TableHead><button className="inline-flex items-center hover:text-foreground transition-colors" onClick={() => handleSort("best_sharpe")}>夏普<SortIcon column="best_sharpe" /></button></TableHead>
                  <TableHead><button className="inline-flex items-center hover:text-foreground transition-colors" onClick={() => handleSort("total_return")}>收益<SortIcon column="total_return" /></button></TableHead>
                  <TableHead><button className="inline-flex items-center hover:text-foreground transition-colors" onClick={() => handleSort("max_drawdown")}>回撤<SortIcon column="max_drawdown" /></button></TableHead>
                  <TableHead><button className="inline-flex items-center hover:text-foreground transition-colors" onClick={() => handleSort("win_rate")}>胜率<SortIcon column="win_rate" /></button></TableHead>
                  <TableHead><button className="inline-flex items-center hover:text-foreground transition-colors" onClick={() => handleSort("n_trials")}>迭代<SortIcon column="n_trials" /></button></TableHead>
                  <TableHead className="w-[80px]">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedResults.map((result, idx) => {
                  const gated = result.oos_rejected === true || result.all_rejected === true
                  return (
                  <React.Fragment key={`${idx}-${result.file_name}`}>
                    <TableRow className={`cursor-pointer hover:bg-muted/50 ${gated ? "opacity-50" : ""}`} onClick={() => toggleRow(result.file_name)}>
                      <TableCell>{expandedRow === result.file_name ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</TableCell>
                      <TableCell><div><span className="font-medium">{result.strategy_name}</span><Badge variant="outline" className="ml-2 text-xs">{result.strategy_id}</Badge>{gated && <Badge className="ml-2 text-xs bg-red-500/20 text-red-400 border-0">{result.oos_rejected ? "OOS衰减" : "trades不足"}</Badge>}{result.pre_noop_fix && <Badge className="ml-2 text-xs bg-amber-500/20 text-amber-500 border-0">修复前</Badge>}</div></TableCell>
                      <TableCell><Badge variant="secondary" className="text-xs">{result.exchange}/{result.market_type}</Badge></TableCell>
                      <TableCell><Badge variant="secondary">{result.interval}</Badge></TableCell>
                      <TableCell>{formatSharpe(result.best_sharpe)}</TableCell>
                      <TableCell>{result.total_return > 0 ? (<span className="text-green-500 flex items-center gap-1"><TrendingUp className="h-3 w-3" />{formatPercent(result.total_return)}</span>) : (<span className="text-red-500 flex items-center gap-1"><TrendingDown className="h-3 w-3" />{formatPercent(result.total_return)}</span>)}</TableCell>
                      <TableCell className="text-red-500">{formatPercent(result.max_drawdown)}</TableCell>
                      <TableCell>{formatPercent(result.win_rate)}</TableCell>
                      <TableCell>{result.n_trials}</TableCell>
                      <TableCell><Button variant="ghost" size="sm" disabled={creatingStrategy === result.strategy_id} onClick={(e) => { e.stopPropagation(); goToStudio(result) }} title="从模板创建策略并跳转到工作台">{creatingStrategy === result.strategy_id ? <RefreshCw className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}</Button></TableCell>
                    </TableRow>
                    {expandedRow === result.file_name && detailCache[result.file_name] && (
                      <TableRow>
                        <TableCell colSpan={10} className="bg-muted/30 p-4">
                          <div className="space-y-4">
                            <div>
                              <h4 className="font-medium mb-2 flex items-center gap-2"><Target className="h-4 w-4" />最优参数</h4>
                              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                                {Object.entries(detailCache[result.file_name].best_params).map(([key, value]) => (<div key={key} className="bg-background rounded p-2 border"><span className="text-xs text-muted-foreground">{key}</span><span className="ml-2 font-medium">{typeof value === "number" ? value.toFixed(4) : value}</span></div>))}
                              </div>
                            </div>
                            <div>
                              <h4 className="font-medium mb-2">回测详情</h4>
                              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                                {[{ label: "交易所/市场", value: `${detailCache[result.file_name].exchange || result.exchange}/${detailCache[result.file_name].market_type || result.market_type}` }, { label: "交易对", value: detailCache[result.file_name].symbol || result.symbol }, { label: "总交易次数", value: detailCache[result.file_name].backtest_result.total_trades }, { label: "盈亏比", value: detailCache[result.file_name].backtest_result.profit_factor, format: (v: number) => !isFinite(v) ? "inf" : v.toFixed(2) }, { label: "Calmar比率", value: detailCache[result.file_name].backtest_result.calmar_ratio, format: (v: number) => v?.toFixed(3) }, { label: "Sortino比率", value: detailCache[result.file_name].backtest_result.sortino_ratio, format: (v: number) => v?.toFixed(3) }, { label: "年化收益", value: detailCache[result.file_name].backtest_result.annual_return, format: (v: number) => v ? `${(v * 100).toFixed(2)}%` : "-" }, { label: "最大回撤", value: detailCache[result.file_name].backtest_result.max_drawdown, format: (v: number) => v ? `${(v * 100).toFixed(2)}%` : "-" }, { label: "胜率", value: detailCache[result.file_name].backtest_result.win_rate, format: (v: number) => v ? `${(v * 100).toFixed(2)}%` : "-" }, { label: "优化耗时", value: detailCache[result.file_name].optimization_time, format: (v: number) => `${v?.toFixed(1)}s` }, { label: "年化口径", value: result.annualization_factor, format: (v: number) => `年化 ${v}`, title: "本条记录回测与DSR共用的 periods_per_year(crypto=365系、us_stock=252;equity分钟级为近似表)" }].map(({ label, value, format, title }) => (<div key={label} className="bg-background rounded p-2 border" title={title}><span className="text-xs text-muted-foreground">{label}</span><span className="ml-2 font-medium">{value !== undefined && value !== null ? (format ? format(value as number) : value) : "N/A"}</span></div>))}
                              </div>
                            </div>
                            <div className="flex justify-end"><Button variant="default" size="sm" disabled={creatingStrategy === result.strategy_id} onClick={() => goToStudio(result)} className="gap-2">{creatingStrategy === result.strategy_id ? (<><RefreshCw className="h-4 w-4 animate-spin" />创建中...</>) : (<><ExternalLink className="h-4 w-4" />在策略工作台中验证</>)}</Button></div>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </React.Fragment>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
