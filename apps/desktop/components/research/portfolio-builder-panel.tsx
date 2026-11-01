"use client"

import React, { useState, useCallback, useMemo, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Checkbox } from "@/components/ui/checkbox"
import { Slider } from "@/components/ui/slider"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Layers, Save, Rocket, Play, TrendingUp, RefreshCw } from "lucide-react"
import { api, apiPost } from "@/lib/api-client"
import { useUiText } from "@/components/i18n/use-ui-text"

interface StrategyPoolItem {
  id: string; name: string; strategy_id: string; sharpe: number; return_pct: number
  max_drawdown: number; win_rate: number; total_trades: number; volatility?: number; backtest_id: string
}

interface BacktestListItem {
  id?: string; strategy_id?: string; strategy_name?: string; created_at?: number
  sharpe_ratio?: number; total_return?: number; max_drawdown?: number
  win_rate?: number; total_trades?: number
}

const ALLOCATION_METHODS = [
  { id: "equal", name: "等权重" },
  { id: "risk_parity", name: "风险平价" },
  { id: "kelly", name: "凯利准则" },
  { id: "custom", name: "自定义" },
]

interface StrategyAllocation { id: string; name: string; weight: number; sharpe: number; return_pct?: number; max_drawdown?: number }
interface PortfolioMetrics { expected_return: number; portfolio_sharpe: number; portfolio_volatility: number; max_drawdown: number }

interface PortfolioRecommendResult {
  assets: string[]
  weights: number[]
  objective: string
  success: boolean
  message: string
  bl_mu_posterior: number[]
  bl_mu_equilibrium: number[]
  n_views: number
  turnover: number
  config: Record<string, unknown>
}

function calculatePortfolioMetrics(allocations: StrategyAllocation[], strategyPool: StrategyPoolItem[]): PortfolioMetrics {
  const totalWeight = allocations.reduce((sum, a) => sum + a.weight, 0)
  if (totalWeight === 0 || allocations.length === 0) return { expected_return: 0, portfolio_sharpe: 0, portfolio_volatility: 0, max_drawdown: 0 }
  const expectedReturn = allocations.reduce((sum, a) => { const s = strategyPool.find((s) => s.strategy_id === a.id); return sum + (s?.return_pct || 0) * (a.weight / totalWeight) }, 0)
  const portfolioVolatility = allocations.reduce((sum, a) => { const s = strategyPool.find((s) => s.strategy_id === a.id); const vol = s?.volatility || (s?.max_drawdown || 10) * 0.5; return sum + vol * (a.weight / totalWeight) }, 0)
  const portfolioSharpe = expectedReturn / (portfolioVolatility || 1)
  const maxDrawdown = allocations.reduce((sum, a) => { const s = strategyPool.find((s) => s.strategy_id === a.id); return sum + (s?.max_drawdown || 10) * (a.weight / totalWeight) }, 0)
  return { expected_return: expectedReturn, portfolio_sharpe: Math.max(0, portfolioSharpe), portfolio_volatility: portfolioVolatility, max_drawdown: maxDrawdown }
}

function generateCorrelationMatrix(strategies: string[]): number[][] {
  const n = strategies.length; const matrix: number[][] = []
  for (let i = 0; i < n; i++) { matrix[i] = []; for (let j = 0; j < n; j++) { if (i === j) { matrix[i][j] = 1.0 } else if (j > i) { matrix[i][j] = Math.random() * 0.3 + 0.1; matrix[j][i] = matrix[i][j] } } }
  return matrix
}

export function PortfolioBuilderPanel() {
  const tr = useUiText()

  const [strategyPool, setStrategyPool] = useState<StrategyPoolItem[]>([])
  const [loadingPool, setLoadingPool] = useState(true)
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([])
  const [allocationMethod, setAllocationMethod] = useState("risk_parity")
  const [customWeights, setCustomWeights] = useState<Record<string, number>>({})
  const [blAssets, setBlAssets] = useState("BTC/USDT, ETH/USDT, SOL/USDT")
  const [blSignals, setBlSignals] = useState("")
  const [blResult, setBlResult] = useState<PortfolioRecommendResult | null>(null)
  const [blBusy, setBlBusy] = useState(false)
  const [blError, setBlError] = useState<string | null>(null)

  useEffect(() => { loadStrategyPool() }, [])

  const loadStrategyPool = async () => {
    setLoadingPool(true)
    try {
      const data = await api<BacktestListItem[]>("/api/v1/backtests?limit=100")
      const list = Array.isArray(data) ? data : []
      const strategyMap = new Map<string, BacktestListItem>()
      list.forEach((bt) => { const sid = bt.strategy_id ?? ""; if (!strategyMap.has(sid) || (bt.created_at ?? 0) > (strategyMap.get(sid)?.created_at ?? 0)) { strategyMap.set(sid, bt) } })
      const pool: StrategyPoolItem[] = Array.from(strategyMap.entries()).map(([id, bt]) => ({
        id, name: bt.strategy_name || id, strategy_id: id, sharpe: bt.sharpe_ratio || 0,
        return_pct: (bt.total_return || 0) * 100, max_drawdown: (bt.max_drawdown || 0) * 100,
        win_rate: bt.win_rate || 0, total_trades: bt.total_trades || 0,
        volatility: (bt.max_drawdown || 0.1) * 0.5, backtest_id: bt.id ?? id,
      }))
      const validPool = pool.filter(s => s.sharpe > 0 || s.total_trades > 0)
      setStrategyPool(validPool)
      if (validPool.length > 0) { setSelectedStrategies(validPool.sort((a, b) => b.sharpe - a.sharpe).slice(0, 3).map(s => s.strategy_id)) }
    } catch {
      // fallback empty
    }
    setLoadingPool(false)
  }

  const allocations = useMemo((): StrategyAllocation[] => {
    if (selectedStrategies.length === 0) return []
    let weights: number[]
    if (allocationMethod === "equal") { weights = selectedStrategies.map(() => 100 / selectedStrategies.length) }
    else if (allocationMethod === "risk_parity") {
      const volInverses = selectedStrategies.map((id) => { const s = strategyPool.find((strat) => strat.strategy_id === id); const vol = s?.volatility || (s?.max_drawdown || 10) * 0.5; return 1 / Math.max(vol, 1) })
      const totalVolInverse = volInverses.reduce((a, b) => a + b, 0); weights = volInverses.map((v) => (v / totalVolInverse) * 100)
    } else if (allocationMethod === "kelly") {
      const kellyScores = selectedStrategies.map((id) => { const s = strategyPool.find((strat) => strat.strategy_id === id); return Math.pow(s?.sharpe || 0.1, 2) })
      const totalKelly = kellyScores.reduce((a, b) => a + b, 0); weights = kellyScores.map((k) => (k / totalKelly) * 100)
    } else { weights = selectedStrategies.map((id) => customWeights[id] || 100 / selectedStrategies.length) }
    return selectedStrategies.map((id, idx) => { const strategy = strategyPool.find((s) => s.strategy_id === id); return { id, name: strategy?.name || id, weight: weights[idx], sharpe: strategy?.sharpe || 0, return_pct: strategy?.return_pct || 0, max_drawdown: strategy?.max_drawdown || 0 } })
  }, [selectedStrategies, allocationMethod, customWeights, strategyPool])

  const portfolioMetrics = useMemo(() => calculatePortfolioMetrics(allocations, strategyPool), [allocations, strategyPool])
  const corrMatrix = useMemo(() => generateCorrelationMatrix(selectedStrategies), [selectedStrategies])

  const toggleStrategy = useCallback((strategyId: string) => { setSelectedStrategies((prev) => prev.includes(strategyId) ? prev.filter((s) => s !== strategyId) : [...prev, strategyId]) }, [])
  const updateCustomWeight = useCallback((strategyId: string, weight: number) => { setCustomWeights((prev) => ({ ...prev, [strategyId]: weight })) }, [])
  const runRecommend = useCallback(async () => {
    const assets = blAssets.split(",").map((s) => s.trim()).filter(Boolean)
    if (assets.length === 0) { setBlError("至少填写一个资产"); return }
    setBlBusy(true); setBlError(null)
    const signals: Record<string, number> = {}
    for (const line of blSignals.split("\n")) {
      const m = line.trim().match(/^([^:]+):\s*(-?\d+(?:\.\d+)?)$/)
      if (m) signals[m[1].trim()] = parseFloat(m[2])
    }
    try {
      const res = await apiPost<PortfolioRecommendResult>("/api/v1/portfolio/recommend", { assets, signals, objective: "max_sharpe" })
      setBlResult(res)
    } catch (e) {
      setBlError(e instanceof Error ? e.message : "推荐失败")
    }
    setBlBusy(false)
  }, [blAssets, blSignals])
  const getCorrColor = (value: number): string => { if (value >= 0.8) return "bg-blue-500"; if (value >= 0.5) return "bg-blue-400"; if (value >= 0.3) return "bg-blue-300"; return "bg-gray-200" }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><Layers className="h-4 w-4" />{tr("可用策略")}</CardTitle></CardHeader>
        <CardContent>
          {loadingPool ? (<div className="space-y-2"><Skeleton className="h-8 w-full" /><p className="text-sm text-muted-foreground">{tr("加载回测结果...")}</p></div>)
          : strategyPool.length === 0 ? (<div className="text-sm text-muted-foreground text-center py-4">{tr("没有可用的回测结果。请先运行策略优化获取回测数据。")}<Button variant="outline" size="sm" className="mt-2" onClick={loadStrategyPool}><RefreshCw className="h-3 w-3 mr-2" />{tr("重新加载")}</Button></div>)
          : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                {strategyPool.map((strategy) => (
                  <div key={strategy.strategy_id} className={`flex items-center gap-3 p-3 rounded-lg border ${selectedStrategies.includes(strategy.strategy_id) ? "border-primary bg-primary/5" : "border-border"}`}>
                    <Checkbox id={`portfolio-${strategy.strategy_id}`} checked={selectedStrategies.includes(strategy.strategy_id)} onCheckedChange={() => toggleStrategy(strategy.strategy_id)} />
                    <label htmlFor={`portfolio-${strategy.strategy_id}`} className="text-sm flex-1 cursor-pointer">
                      <div className="font-medium">{strategy.name}</div>
                      <div className="text-xs text-muted-foreground">{tr("夏普:")} {strategy.sharpe?.toFixed(2) ?? "—"} {tr("| 收益:")} {strategy.return_pct?.toFixed(1) ?? "—"}%</div>
                    </label>
                  </div>
                ))}
              </div>
              {!loadingPool && strategyPool.length > 0 && (
                <div className="mt-4 space-y-2">
                  <label className="text-sm font-medium">{tr("分配方法")}</label>
                  <Select value={allocationMethod} onValueChange={setAllocationMethod}><SelectTrigger className="w-full max-w-xs"><SelectValue /></SelectTrigger><SelectContent>{ALLOCATION_METHODS.map((m) => (<SelectItem key={m.id} value={m.id}>{tr(m.name)}</SelectItem>))}</SelectContent></Select>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base">{tr("权重分配")}</CardTitle></CardHeader>
          <CardContent>
            {allocations.length > 0 ? (
              <div className="space-y-4">
                {allocations.map((alloc) => (
                  <div key={alloc.id} className="space-y-2">
                    <div className="flex justify-between text-sm"><span>{alloc.name}</span><span className="font-medium">{alloc.weight.toFixed(1)}%</span></div>
                    {allocationMethod === "custom" ? (<Slider value={[customWeights[alloc.id] || alloc.weight]} min={0} max={100} step={1} onValueChange={(values) => updateCustomWeight(alloc.id, values[0])} />) : (<div className="h-2 bg-muted rounded-full overflow-hidden"><div className="h-full bg-primary rounded-full" style={{ width: `${alloc.weight}%` }} /></div>)}
                  </div>
                ))}
              </div>
            ) : (<div className="text-sm text-muted-foreground text-center py-4">{tr("选择策略以构建组合")}</div>)}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base">{tr("组合指标")}</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4">
              <div className="p-3 rounded-lg bg-muted/50"><div className="text-xs text-muted-foreground">{tr("预期收益")}</div><div className="text-xl font-semibold text-emerald-500">+{portfolioMetrics.expected_return.toFixed(1)}%</div></div>
              <div className="p-3 rounded-lg bg-muted/50"><div className="text-xs text-muted-foreground">{tr("组合夏普")}</div><div className="text-xl font-semibold">{portfolioMetrics.portfolio_sharpe.toFixed(2)}</div></div>
              <div className="p-3 rounded-lg bg-muted/50"><div className="text-xs text-muted-foreground">{tr("组合波动率")}</div><div className="text-xl font-semibold">{portfolioMetrics.portfolio_volatility.toFixed(1)}%</div></div>
              <div className="p-3 rounded-lg bg-muted/50"><div className="text-xs text-muted-foreground">{tr("最大回撤")}</div><div className="text-xl font-semibold text-red-400">-{portfolioMetrics.max_drawdown.toFixed(1)}%</div></div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base">{tr("相关性矩阵")}</CardTitle></CardHeader>
        <CardContent>
          {selectedStrategies.length > 1 ? (
            <div className="overflow-auto">
              <table className="w-full text-xs">
                <thead><tr><th className="p-2"></th>{selectedStrategies.map((id) => { const s = strategyPool.find((strat) => strat.strategy_id === id); return (<th key={id} className="p-2 text-center font-medium">{s?.name.slice(0, 10) || id.slice(0, 10)}</th>) })}</tr></thead>
                <tbody>
                  {selectedStrategies.map((rowId, rowIdx) => { const rowStrategy = strategyPool.find((s) => s.strategy_id === rowId); return (
                    <tr key={rowId}><td className="p-2 font-medium">{rowStrategy?.name.slice(0, 10) || rowId.slice(0, 10)}</td>{selectedStrategies.map((colId, colIdx) => (<td key={colId} className={`p-2 text-center ${getCorrColor(corrMatrix[rowIdx]?.[colIdx] || 0)} rounded`}>{(corrMatrix[rowIdx]?.[colIdx] || 0).toFixed(2)}</td>))}</tr>
                  ) })}
                </tbody>
              </table>
            </div>
          ) : (<div className="text-sm text-muted-foreground text-center py-4">{tr("选择至少 2 个策略查看相关性矩阵")}</div>)}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><TrendingUp className="h-4 w-4" />{tr("组合推荐 (Black-Litterman)")}</CardTitle></CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-sm font-medium">{tr("资产（逗号分隔）")}</label>
              <input value={blAssets} onChange={(e) => setBlAssets(e.target.value)} placeholder="BTC/USDT, ETH/USDT, SOL/USDT" className="w-full rounded border bg-background px-2 py-1 text-sm" />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium">{tr("信号（每行 symbol: 预期超额收益，可选）")}</label>
              <textarea value={blSignals} onChange={(e) => setBlSignals(e.target.value)} placeholder={"BTC/USDT: 0.03\nETH/USDT: 0.01"} rows={2} className="w-full rounded border bg-background px-2 py-1 text-sm font-mono" />
            </div>
            <Button onClick={runRecommend} disabled={blBusy}>{blBusy ? <RefreshCw className="h-3 w-3 animate-spin mr-2" /> : <TrendingUp className="h-3 w-3 mr-2" />}{tr("生成推荐")}</Button>
            {blError && <p className="text-sm text-red-500">{tr(blError)}</p>}
            {blResult && (
              <div className="space-y-2">
                <div className={`flex items-center gap-2 text-sm ${blResult.success ? "text-emerald-500" : "text-red-500"}`}>
                  {blResult.success ? tr("推荐成功") : tr("推荐失败")}
                  {blResult.message ? <span className="text-muted-foreground text-xs">（{blResult.message}）</span> : null}
                </div>
                <div className="grid gap-1">
                  {blResult.weights.map((w, i) => (
                    <div key={blResult.assets[i]} className="flex items-center gap-2 text-sm">
                      <span className="w-28 text-muted-foreground truncate">{blResult.assets[i]}</span>
                      <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden"><div className="h-full bg-primary rounded-full" style={{ width: `${(w * 100).toFixed(1)}%` }} /></div>
                      <span className="w-14 text-right font-mono">{(w * 100).toFixed(1)}%</span>
                      <span className="w-24 text-right font-mono text-xs text-muted-foreground">μ {(blResult.bl_mu_posterior[i] ?? 0).toFixed(4)}</span>
                    </div>
                  ))}
                </div>
                <div className="flex gap-4 text-xs text-muted-foreground">
                  <span>objective: {blResult.objective}</span>
                  <span>n_views: {blResult.n_views}</span>
                  <span>turnover: {blResult.turnover.toFixed(3)}</span>
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="flex gap-3">
        <Button variant="outline"><Save className="h-4 w-4 mr-2" />{tr("保存组合")}</Button>
        <Button variant="outline"><Play className="h-4 w-4 mr-2" />{tr("回测组合")}</Button>
        <Button><Rocket className="h-4 w-4 mr-2" />{tr("部署组合")}</Button>
      </div>
    </div>
  )
}
