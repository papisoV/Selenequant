"use client"

import React, { useState, useCallback, useMemo, useEffect, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { TrendingUp, Calculator, BarChart3, RefreshCw, RotateCcw, Sparkles } from "lucide-react"
import {
  ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
} from "recharts"
import { api, apiPost } from "@/lib/api-client"
import { useContextStore } from "@/lib/store/context"
import { useWorkflowStore } from "@/lib/store/workflow"
import { toSymbolPair } from "@/lib/symbol-utils"

const DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
const DEFAULT_INTERVALS = ["1h", "4h", "1d"]

interface FactorInfo {
  id: string
  name: string
  category: string
}

interface FactorCorrelation {
  factor1: string
  factor2: string
  correlation: number
}

interface ICIRResultData {
  factor: string
  factor_name: string
  category: string
  ic: number
  ir: number
  ic_std: number
  positive_pct: number
  rank: number
}

interface ScatterSample {
  factor_value: number
  forward_return: number
}

export function FactorResearchPanel() {
  const { pageFilter } = useContextStore()
  const selectedTemplate = useWorkflowStore((s) => s.template)
  const [availableFactors, setAvailableFactors] = useState<FactorInfo[]>([])
  const [loadingFactors, setLoadingFactors] = useState(true)
  const [selectedFactors, setSelectedFactors] = useState<string[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState("BTC/USDT")
  const [selectedInterval, setSelectedInterval] = useState("1h")
  const userTouchedSymbol = useRef(false)
  const userTouchedInterval = useRef(false)
  const [correlationData, setCorrelationData] = useState<FactorCorrelation[] | null>(null)
  const [icirData, setIcirData] = useState<ICIRResultData[] | null>(null)
  const [calculating, setCalculating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [scatterData, setScatterData] = useState<Record<string, ScatterSample[]> | null>(null)
  const [scatterFactor, setScatterFactor] = useState<string>("")

  useEffect(() => { loadFactors() }, [])

  useEffect(() => {
    if (!userTouchedSymbol.current && pageFilter.symbol) {
      setSelectedSymbol(toSymbolPair(pageFilter.symbol))
    }
  }, [pageFilter.symbol])

  useEffect(() => {
    if (!userTouchedInterval.current && pageFilter.tf && DEFAULT_INTERVALS.includes(pageFilter.tf)) {
      setSelectedInterval(pageFilter.tf)
    }
  }, [pageFilter.tf])

  const loadFactors = async () => {
    setLoadingFactors(true)
    try {
      const data = await api<FactorInfo[]>("/api/v1/research/factors")
      setAvailableFactors(data ?? [])
      if (data && data.length > 0) {
        setSelectedFactors(data.slice(0, 4).map(f => f.id))
      }
    } catch {
      // fallback empty
    }
    setLoadingFactors(false)
  }

  const toggleFactor = useCallback((factorId: string) => {
    setSelectedFactors((prev) => prev.includes(factorId) ? prev.filter((f) => f !== factorId) : [...prev, factorId])
    setCorrelationData(null)
    setIcirData(null)
    setScatterData(null)
  }, [])

  const calculateICIR = useCallback(async () => {
    if (selectedFactors.length === 0) { setError("请选择至少一个因子"); return }
    setCalculating(true)
    setError(null)
    try {
      const data = await apiPost<{ ic_ir_results: ICIRResultData[]; correlations: FactorCorrelation[]; scatter_samples: Record<string, ScatterSample[]> }>("/api/v1/research/factors/ic-ir", {
        symbol: selectedSymbol, interval: selectedInterval, factors: selectedFactors, lookback_bars: 500, forward_bars: 1,
      })
      setIcirData(data.ic_ir_results ?? [])
      setCorrelationData(data.correlations ?? [])
      const scatter = data.scatter_samples ?? {}
      setScatterData(scatter)
      setScatterFactor(Object.keys(scatter)[0] ?? "")
    } catch (e) {
      setError(e instanceof Error ? e.message : "计算失败")
    }
    setCalculating(false)
  }, [selectedFactors, selectedSymbol, selectedInterval])

  const correlationMatrix = useMemo(() => {
    if (!correlationData) return null
    const matrix: Record<string, Record<string, number>> = {}
    for (const f of selectedFactors) { matrix[f] = {} }
    for (const c of correlationData) { if (matrix[c.factor1]) { matrix[c.factor1][c.factor2] = c.correlation } }
    return matrix
  }, [correlationData, selectedFactors])

  const getCorrelationColor = (value: number): string => {
    if (value >= 0.8) return "bg-emerald-500"
    if (value >= 0.5) return "bg-emerald-400"
    if (value >= 0.2) return "bg-emerald-300"
    if (value >= -0.2) return "bg-gray-200"
    if (value >= -0.5) return "bg-red-300"
    if (value >= -0.8) return "bg-red-400"
    return "bg-red-500"
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {selectedTemplate && (
        <div className="lg:col-span-2 flex items-center gap-2 px-4 py-2 bg-amber-50 text-amber-800 text-sm rounded-md">
          <Sparkles className="h-3.5 w-3.5" />
          已选模板: <strong>{selectedTemplate.name}</strong>
          <span className="text-amber-600">({selectedTemplate.category})</span>
        </div>
      )}
      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><TrendingUp className="h-4 w-4" />因子选择器</CardTitle></CardHeader>
        <CardContent>
          {loadingFactors ? (
            <div className="space-y-2"><Skeleton className="h-6 w-full" /><p className="text-sm text-muted-foreground">加载因子列表...</p></div>
          ) : (
            <>
              <div className="flex gap-4 mb-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">交易对</label>
                  <div className="flex items-center gap-1">
                    <Select value={selectedSymbol} onValueChange={(v) => { userTouchedSymbol.current = true; setSelectedSymbol(v) }}><SelectTrigger className="w-[140px]"><SelectValue /></SelectTrigger><SelectContent>{DEFAULT_SYMBOLS.map((s) => (<SelectItem key={s} value={s}>{s}</SelectItem>))}</SelectContent></Select>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => {
                        userTouchedSymbol.current = false
                        if (pageFilter.symbol) setSelectedSymbol(toSymbolPair(pageFilter.symbol))
                      }}
                      title="重置为 pageFilter symbol"
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">周期</label>
                  <div className="flex items-center gap-1">
                    <Select value={selectedInterval} onValueChange={(v) => { userTouchedInterval.current = true; setSelectedInterval(v) }}><SelectTrigger className="w-[100px]"><SelectValue /></SelectTrigger><SelectContent>{DEFAULT_INTERVALS.map((i) => (<SelectItem key={i} value={i}>{i}</SelectItem>))}</SelectContent></Select>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => {
                        userTouchedInterval.current = false
                        if (pageFilter.tf && DEFAULT_INTERVALS.includes(pageFilter.tf)) setSelectedInterval(pageFilter.tf)
                      }}
                      title="重置为 pageFilter tf"
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </div>
              <div className="space-y-3">
                {availableFactors.map((factor) => (
                  <div key={factor.id} className="flex items-center gap-3">
                    <Checkbox id={factor.id} checked={selectedFactors.includes(factor.id)} onCheckedChange={() => toggleFactor(factor.id)} />
                    <label htmlFor={factor.id} className="text-sm flex-1">
                      {factor.name}
                      {factor.category === "event" && (
                        <span className="ml-2 text-xs text-amber-600">方向已证伪,仅表达波动/收益关联</span>
                      )}
                    </label>
                    <Badge variant="outline" className="text-xs">{factor.category}</Badge>
                  </div>
                ))}
              </div>
              <div className="mt-4 flex gap-2">
                <Button onClick={calculateICIR} disabled={calculating || selectedFactors.length < 2}>
                  {calculating ? <RefreshCw className="h-3 w-3 animate-spin mr-2" /> : <Calculator className="h-3 w-3 mr-2" />}计算 IC/IR
                </Button>
              </div>
              {error && <p className="mt-2 text-sm text-red-500">{error}</p>}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base">相关性热力图</CardTitle></CardHeader>
        <CardContent>
          {correlationMatrix ? (
            <div className="overflow-auto">
              <table className="w-full text-xs">
                <thead><tr><th className="p-1"></th>{selectedFactors.map((f) => (<th key={f} className="p-1 text-center font-medium">{f.toUpperCase()}</th>))}</tr></thead>
                <tbody>
                  {selectedFactors.map((rowFactor) => (
                    <tr key={rowFactor}>
                      <td className="p-1 font-medium">{rowFactor.toUpperCase()}</td>
                      {selectedFactors.map((colFactor) => (<td key={colFactor} className={`p-1 text-center ${getCorrelationColor(correlationMatrix[rowFactor][colFactor] || 0)} rounded`}>{(correlationMatrix[rowFactor][colFactor] || 0).toFixed(2)}</td>))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-sm text-muted-foreground text-center py-8">选择因子 (至少2个) 后点击"计算 IC/IR"</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base">IC/IR 分析</CardTitle></CardHeader>
        <CardContent>
          {icirData ? (
            <Table>
              <TableHeader><TableRow><TableHead>因子</TableHead><TableHead className="text-right">IC</TableHead><TableHead className="text-right">IR</TableHead><TableHead className="text-right">排名</TableHead></TableRow></TableHeader>
              <TableBody>
                {icirData.map((row) => (
                  <TableRow key={row.factor}>
                    <TableCell className="font-medium">{row.factor.toUpperCase()}</TableCell>
                    <TableCell className="text-right"><span className={(row.ic ?? 0) >= 0 ? "text-emerald-500" : "text-red-500"}>{row.ic?.toFixed(3) ?? "—"}</span></TableCell>
                    <TableCell className="text-right">{row.ir?.toFixed(2) ?? "—"}</TableCell>
                    <TableCell className="text-right">{row.rank}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="text-sm text-muted-foreground text-center py-8">点击"计算 IC/IR"分析因子有效性</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base">因子预测性</CardTitle></CardHeader>
        <CardContent>
          {scatterData && Object.keys(scatterData).length > 0 ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium">因子：</span>
                <Select value={scatterFactor} onValueChange={setScatterFactor}>
                  <SelectTrigger className="w-[200px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Object.keys(scatterData).map((f) => (
                      <SelectItem key={f} value={f}>{f}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className="text-xs text-muted-foreground">x=因子值, y=未来 {selectedInterval} 收益</span>
              </div>
              <div className="h-[260px] border rounded-lg">
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis type="number" dataKey="factor_value" name="因子值" tick={{ fontSize: 10 }} />
                    <YAxis type="number" dataKey="forward_return" name="未来收益" tick={{ fontSize: 10 }} />
                    <Tooltip cursor={{ strokeDasharray: "3 3" }} />
                    <ReferenceLine y={0} stroke="#999" />
                    <Scatter data={scatterData[scatterFactor] ?? []} fill="#8884d8" />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <div className="h-[200px] flex items-center justify-center border rounded-lg bg-muted/30">
              <div className="text-sm text-muted-foreground">选择因子后点击"计算 IC/IR"查看因子值 vs 未来收益散点</div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
