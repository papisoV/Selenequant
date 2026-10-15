"use client"

import React, { useState, useCallback, useRef, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Brain, Play, Square, RefreshCw, CheckCircle2, XCircle, Clock, Loader2, Sparkles, BarChart3 } from "lucide-react"
import { apiPost } from "@/lib/api-client"
import { useTuningWs } from "@/lib/ws/use-tuning-ws"
import { useWorkflowStore } from "@/lib/store/workflow"

type TuningMode = "optuna" | "smart"

const ALL_STRATEGIES = [
  { id: "bb_v3", name: "布林带升级版" }, { id: "rsi_ma_delta", name: "RSI偏离均线" },
  { id: "bb_atr_trend", name: "布林ATR趋势" }, { id: "dmi_delta", name: "DMI差值" },
  { id: "p_signal", name: "P信号熵" }, { id: "stoch_supertrend", name: "随机超级趋势" },
  { id: "vw_ema_cross", name: "量加权EMA交叉" }, { id: "ema_triple_cross", name: "三均线交叉" },
  { id: "calendar_effect", name: "日历效应" }, { id: "tema_channel", name: "TEMA通道" },
  { id: "keltner_v2", name: "肯特纳增强版" }, { id: "ma_extended_runs", name: "均线连续偏离" },
  { id: "dca_grid", name: "DCA网格" },
]

const INTERVALS = ["1d", "4h", "1h", "30m", "5m"]
const EXCHANGES = ["binance", "okx", "yahoo"]
const MARKET_TYPES = ["futures", "spot", "us_stock"]

// Yahoo only serves US stock partitions (yahoo/us_stock/<SYM>), so the two
// selects are locked together: selecting either yahoo or us_stock forces the
// other half of the pair. US tickers mirror us_stock_source._normalize_symbol.
const US_SYMBOL_RE = /^[A-Z0-9-]{1,10}$/
const CRYPTO_DEFAULT_SYMBOL = "BTC/USDT"
const US_DEFAULT_SYMBOL = "AAPL"
const isYahooExchange = (ex: string) => ex === "yahoo"
const isUsStockMarket = (mt: string) => mt === "us_stock"

const VOLATILITY_OPTIONS = [
  { value: "low", label: "低波动", desc: "窄幅震荡" },
  { value: "medium", label: "中波动", desc: "正常波动" },
  { value: "high", label: "高波动", desc: "剧烈波动" },
]

const TREND_OPTIONS = [
  { value: "weak", label: "弱趋势", desc: "横盘整理" },
  { value: "moderate", label: "中趋势", desc: "方向明确" },
  { value: "strong", label: "强趋势", desc: "单边行情" },
]

const MARKET_RECOMMENDATIONS: Record<string, string> = {
  low_weak: "均值回归 + 网格 -- 横盘市场适合震荡策略", low_moderate: "趋势 + 振荡 -- 离仓顺势，注意回调", low_strong: "趋势跟踪 -- 低波强趋势最宜趋势策略",
  medium_weak: "振荡 + 均值回归 -- 波动适中但无方向", medium_moderate: "均衡配置 -- 趋势+振荡+均值回归各选", medium_strong: "趋势优先 -- 趋势策略为主，振荡为辅",
  high_weak: "振荡 + 网格 -- 高波无方向，用振荡策略", high_moderate: "趋势 + 振荡 -- 高波有方向，趋势为主", high_strong: "趋势 + 网格 -- 强趋势加仓，网格控风险",
}

interface TuningProgress { current: number; total: number; elapsed_seconds: number; eta_seconds: number | null; current_strategy?: string; best_sharpe_so_far: number; completed_strategies: CompletedStrategy[] }
interface CompletedStrategy { strategy: string; exchange: string; sharpe: number; skipped?: boolean; interval?: string; total_return?: number; score?: number }

export function TuningControlPanel() {
  const tuning = useWorkflowStore((s) => s.tuning)
  const setTuning = useWorkflowStore((s) => s.setTuning)

  const mode: TuningMode = tuning?.mode ?? "optuna"
  const setMode = (v: TuningMode) => setTuning({ mode: v })
  const selectedStrategies: string[] = tuning?.selectedStrategies ?? ALL_STRATEGIES.map(s => s.id)
  const setSelectedStrategies = (v: string[]) => setTuning({ selectedStrategies: v })
  const selectedIntervals: string[] = tuning?.selectedIntervals ?? ["1d", "4h", "1h"]
  const setSelectedIntervals = (v: string[]) => setTuning({ selectedIntervals: v })
  const exchange: string = tuning?.exchange ?? "binance"
  const marketType: string = tuning?.marketType ?? "futures"
  const symbol: string = tuning?.symbol ?? "BTC/USDT"
  const trials: number = tuning?.trials ?? 30
  const setTrials = (v: number) => setTuning({ trials: v })
  const volatility: string = tuning?.volatility ?? "medium"
  const setVolatility = (v: string) => setTuning({ volatility: v })
  const trendStrength: string = tuning?.trendStrength ?? "moderate"
  const setTrendStrength = (v: string) => setTuning({ trendStrength: v })
  const maxParams: number = tuning?.maxParams ?? 5
  const setMaxParams = (v: number) => setTuning({ maxParams: v })
  const taskId: string | null = tuning?.taskId ?? null
  const setTaskId = (v: string | null) => setTuning({ taskId: v })
  const status: "idle" | "running" | "completed" | "failed" | "cancelled" = tuning?.status ?? "idle"
  const setStatus = (v: "idle" | "running" | "completed" | "failed" | "cancelled") => setTuning({ status: v })
  const progress: TuningProgress | null = (tuning?.progress as TuningProgress | null) ?? null
  const setProgress = (v: TuningProgress | null) => setTuning({ progress: v })

  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // WS 实时进度推送（WS 断时 hook 内部自动 fallback 回 3s 轮询）
  const { taskState: wsTaskState, streamState: tuningStreamState } = useTuningWs({
    taskId,
  })
  // 同步 WS 推送的 taskState 到 store progress/status（每次 taskState 变更都同步）
  useEffect(() => {
    if (!wsTaskState) return
    if (wsTaskState.progress) setProgress(wsTaskState.progress as unknown as TuningProgress)
    setStatus(wsTaskState.status as typeof status)
  }, [wsTaskState])
  void tuningStreamState

  const totalTasks = mode === "optuna" ? selectedStrategies.length * selectedIntervals.length : 0
  const isRunning = status === "running"
  const progressPct = progress && progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0
  const marketKey = `${volatility}_${trendStrength}`
  const recommendation = MARKET_RECOMMENDATIONS[marketKey] || "均衡配置"

  const startTuning = useCallback(async () => {
    setStarting(true); setError(null)
    try {
      const endpoint = mode === "optuna" ? "/api/v1/research/tuning/start" : "/api/v1/research/tuning/smart-start"
      const body = mode === "optuna"
        ? { strategies: selectedStrategies, intervals: selectedIntervals, exchange: [exchange], market_type: [marketType], symbol, trials }
        : { market_state: { volatility, trend_strength: trendStrength }, symbol, exchange, market_type: marketType, intervals: selectedIntervals, max_params_per_strategy: maxParams }
      const data = await apiPost<{ task_id: string }>(endpoint, body)
      setTaskId(data.task_id); setStatus("running")
      // 进度由 useTuningWs WS 实时推送，不再 setInterval 轮询
    } catch (e) { setError(String(e)) }
    finally { setStarting(false) }
  }, [mode, selectedStrategies, selectedIntervals, exchange, marketType, symbol, trials, volatility, trendStrength, maxParams])

  const cancelTuning = useCallback(async () => {
    if (!taskId) return
    try { await apiPost(`/api/v1/research/tuning/tasks/${taskId}/cancel`) } catch { /* ignore */ }
    setStatus("cancelled")
  }, [taskId])

  const toggleStrategy = (id: string) => { setSelectedStrategies(selectedStrategies.includes(id) ? selectedStrategies.filter(s => s !== id) : [...selectedStrategies, id]) }
  const toggleInterval = (iv: string) => { setSelectedIntervals(selectedIntervals.includes(iv) ? selectedIntervals.filter(i => i !== iv) : [...selectedIntervals, iv]) }

  // 四向联动(双向防垃圾 combo):yahoo↔us_stock 强制成对出现,错配一侧自动归位。
  // symbol 双向还原:进 yahoo 时非美股形状 → "AAPL";切离 yahoo 时还原 "BTC/USDT"
  // (store 共享状态,不还原会让 crypto 提交 body 带 symbol:"AAPL" → tuner 查无分区静默空转)。
  const changeExchange = (next: string) => {
    if (isYahooExchange(next)) {
      const patchedSymbol = US_SYMBOL_RE.test(symbol) ? symbol : US_DEFAULT_SYMBOL
      setTuning({ exchange: next, marketType: "us_stock", symbol: patchedSymbol })
    } else if (isUsStockMarket(marketType)) {
      setTuning({ exchange: next, marketType: "futures", symbol: CRYPTO_DEFAULT_SYMBOL })
    } else {
      setTuning({ exchange: next })
    }
  }
  const changeMarketType = (next: string) => {
    if (isUsStockMarket(next)) {
      const patchedSymbol = US_SYMBOL_RE.test(symbol) ? symbol : US_DEFAULT_SYMBOL
      setTuning({ marketType: next, exchange: "yahoo", symbol: patchedSymbol })
    } else if (isYahooExchange(exchange)) {
      setTuning({ marketType: next, exchange: "binance", symbol: CRYPTO_DEFAULT_SYMBOL })
    } else {
      setTuning({ marketType: next })
    }
  }
  const yahooMode = isYahooExchange(exchange) && isUsStockMarket(marketType)
  const symbolValid = yahooMode ? US_SYMBOL_RE.test(symbol) : true

  const formatTime = (seconds: number | null) => { if (seconds === null || seconds === undefined) return "--:--"; const m = Math.floor(seconds / 60); const s = Math.floor(seconds % 60); return `${m}:${s.toString().padStart(2, "0")}` }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Brain className="h-5 w-5" />策略参数优化
          {isRunning && (<Badge variant="default" className="ml-2 bg-blue-600"><Loader2 className="h-3 w-3 mr-1 animate-spin" />运行中</Badge>)}
          {status === "completed" && (<Badge variant="default" className="ml-2 bg-green-600"><CheckCircle2 className="h-3 w-3 mr-1" />完成</Badge>)}
          {status === "failed" && (<Badge variant="destructive" className="ml-2"><XCircle className="h-3 w-3 mr-1" />失败</Badge>)}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {!isRunning && status !== "completed" && status !== "failed" && status !== "cancelled" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">模式</span>
              <button onClick={() => setMode("optuna")} className={`h-7 px-3 rounded text-xs border transition-colors flex items-center gap-1 ${mode === "optuna" ? "bg-primary text-primary-foreground border-primary" : "bg-background border-input text-muted-foreground hover:bg-muted"}`}><BarChart3 className="h-3 w-3" />贝叶斯优化</button>
              <button onClick={() => setMode("smart")} className={`h-7 px-3 rounded text-xs border transition-colors flex items-center gap-1 ${mode === "smart" ? "bg-primary text-primary-foreground border-primary" : "bg-background border-input text-muted-foreground hover:bg-muted"}`}><Sparkles className="h-3 w-3" />智能调参</button>
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">交易所</span><select value={exchange} onChange={e => changeExchange(e.target.value)} className="h-8 rounded-md border border-input bg-background px-2 text-sm">{EXCHANGES.map(ex => (<option key={ex} value={ex}>{ex}</option>))}</select></div>
              <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">市场</span><select value={marketType} onChange={e => changeMarketType(e.target.value)} className="h-8 rounded-md border border-input bg-background px-2 text-sm">{MARKET_TYPES.map(mt => (<option key={mt} value={mt}>{mt}</option>))}</select></div>
              <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">交易对</span>
                {yahooMode ? (
                  <input
                    value={symbol}
                    onChange={e => setTuning({ symbol: e.target.value })}
                    placeholder="AAPL"
                    aria-label="美股代码"
                    className={`h-8 w-24 rounded-md border bg-background px-2 text-sm ${symbolValid ? "border-input" : "border-red-500"}`}
                  />
                ) : (
                  <span className="text-sm font-medium">{symbol}</span>
                )}
                {!symbolValid && (<span className="text-xs text-red-500">仅支持大写字母/数字/短横线(≤10位),如 AAPL</span>)}
              </div>
            </div>
            <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">周期</span>{INTERVALS.map(iv => (<button key={iv} onClick={() => toggleInterval(iv)} className={`h-7 px-2 rounded text-xs border transition-colors ${selectedIntervals.includes(iv) ? "bg-primary text-primary-foreground border-primary" : "bg-background border-input text-muted-foreground hover:bg-muted"}`}>{iv}</button>))}</div>
            {mode === "optuna" && (
              <>
                <div className="flex items-start gap-2 flex-wrap"><span className="text-xs text-muted-foreground mt-1">策略</span>{ALL_STRATEGIES.map(s => (<button key={s.id} onClick={() => toggleStrategy(s.id)} className={`h-7 px-2 rounded text-xs border transition-colors ${selectedStrategies.includes(s.id) ? "bg-primary text-primary-foreground border-primary" : "bg-background border-input text-muted-foreground hover:bg-muted"}`}>{s.name}</button>))}</div>
                <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">迭代次数</span><input type="number" value={trials} onChange={e => setTrials(Number(e.target.value))} min={5} max={200} className="h-8 w-16 rounded-md border border-input bg-background px-2 text-sm" /></div>
              </>
            )}
            {mode === "smart" && (
              <>
                <div className="space-y-2 p-3 rounded-md bg-muted/30 border border-border/50">
                  <div className="flex items-center gap-1 text-xs text-muted-foreground"><Sparkles className="h-3 w-3" />市场状态判断</div>
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-2"><span className="text-xs">波动率</span>{VOLATILITY_OPTIONS.map(opt => (<button key={opt.value} onClick={() => setVolatility(opt.value)} className={`h-7 px-2 rounded text-xs border transition-colors ${volatility === opt.value ? "bg-amber-600 text-white border-amber-600" : "bg-background border-input text-muted-foreground hover:bg-muted"}`} title={opt.desc}>{opt.label}</button>))}</div>
                    <div className="flex items-center gap-2"><span className="text-xs">趋势</span>{TREND_OPTIONS.map(opt => (<button key={opt.value} onClick={() => setTrendStrength(opt.value)} className={`h-7 px-2 rounded text-xs border transition-colors ${trendStrength === opt.value ? "bg-blue-600 text-white border-blue-600" : "bg-background border-input text-muted-foreground hover:bg-muted"}`} title={opt.desc}>{opt.label}</button>))}</div>
                  </div>
                  <p className="text-xs text-muted-foreground">推荐: <span className="text-foreground font-medium">{recommendation}</span></p>
                </div>
                <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">每策略参数组合数</span><input type="number" value={maxParams} onChange={e => setMaxParams(Number(e.target.value))} min={1} max={20} className="h-8 w-16 rounded-md border border-input bg-background px-2 text-sm" /></div>
              </>
            )}
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">{mode === "optuna" ? `共 ${totalTasks} 个调参任务` : "根据市场状态智能选择策略和参数范围"}</span>
              <Button onClick={startTuning} disabled={starting || !symbolValid || (mode === "optuna" && (selectedStrategies.length === 0 || selectedIntervals.length === 0)) || selectedIntervals.length === 0} size="sm" className="gap-2">{starting ? <Loader2 className="h-4 w-4 animate-spin" /> : mode === "smart" ? <Sparkles className="h-4 w-4" /> : <Play className="h-4 w-4" />}{mode === "smart" ? "智能优化" : "开始优化"}</Button>
            </div>
            {error && <p className="text-sm text-red-500">{error}</p>}
          </div>
        )}
        {(isRunning || status === "completed" || status === "failed" || status === "cancelled") && progress && (
          <div className="space-y-3">
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs"><span className="text-muted-foreground">{progress.current}/{progress.total} 完成</span><span className="text-muted-foreground flex items-center gap-1"><Clock className="h-3 w-3" />{formatTime(progress.elapsed_seconds)}{progress.eta_seconds !== null && isRunning && (<> / ETA {formatTime(progress.eta_seconds)}</>)}</span></div>
              <div className="h-2 rounded-full bg-muted overflow-hidden"><div className={`h-full rounded-full transition-all duration-300 ${status === "completed" ? "bg-green-500" : status === "failed" ? "bg-red-500" : mode === "smart" ? "bg-amber-500" : "bg-blue-500"}`} style={{ width: `${progressPct}%` }} /></div>
            </div>
            {isRunning && progress.current_strategy && (<div className="flex items-center gap-2 text-sm"><Loader2 className="h-3 w-3 animate-spin text-blue-500" /><span>当前: {progress.current_strategy}</span>{progress.best_sharpe_so_far > 0 && (<span className="text-muted-foreground">| 最优净夏普: <span className="text-green-500 font-medium">{progress.best_sharpe_so_far.toFixed(3)}</span></span>)}</div>)}
            {progress.completed_strategies.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead><tr className="border-b border-border"><th className="py-1 text-left text-muted-foreground font-normal">策略</th><th className="py-1 text-left text-muted-foreground font-normal">市场</th><th className="py-1 text-right text-muted-foreground font-normal">净夏普</th></tr></thead>
                  <tbody>
                    {progress.completed_strategies.filter(s => !s.skipped && s.strategy).sort((a, b) => b.sharpe - a.sharpe).map((s, i) => (
                      <tr key={i} className="border-b border-border/50">
                        <td className="py-1">{s.strategy}</td><td className="py-1 text-muted-foreground">{s.exchange}</td>
                        <td className={`py-1 text-right font-medium ${s.sharpe > 1 ? "text-green-500" : s.sharpe > 0 ? "text-yellow-500" : "text-red-500"}`}>{s.sharpe.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="flex items-center gap-2">
              {isRunning && (<Button variant="destructive" size="sm" onClick={cancelTuning} className="gap-2"><Square className="h-3 w-3" />停止</Button>)}
              {(status === "completed" || status === "failed" || status === "cancelled") && (<Button variant="outline" size="sm" onClick={() => { setTaskId(null); setStatus("idle"); setProgress(null) }} className="gap-2"><RefreshCw className="h-3 w-3" />重新配置</Button>)}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
