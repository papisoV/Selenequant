"use client"

import React, { useState, useCallback, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CurveChart } from "@/components/strategy/shared-charts"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { api, apiPost, apiPatch } from "@/lib/api-client"
import { useContextStore } from "@/lib/store/context"
import { toSymbolPair } from "@/lib/symbol-utils"
import { getFormulaDetail, getTrainStatus, getTrainLogs, getFormulaExplain, listArtifacts,
  exportArtifacts, importArtifacts, listMiningSessions, resumeTraining,
  startBatchTraining, getBatchStatus, validateFormula,
  listDataFiles, syncKlinePartition, getKlineCoverage,
} from "@/lib/api/alpha"
import type {
  FormulaDetail, TrainingHistory, TrainStatus, FormulaExplain, ArtifactInfo,
  MiningSession, MinedFormulaInfo, BatchStatus, FormulaVerdict, BacktestDetail,
} from "@/lib/api/alpha"
import { backtestMinedFormula } from "@/lib/api/alpha"
import type { MinedFormulaBacktest } from "@/lib/api/alpha"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog"
import { TrainingChartCard } from "@/components/research/alpha-mining-charts"
import { WorkbenchTabBar, GuideStrip, WbPane } from "@/components/research/alpha-mining-tabs"
import { ValidationPane } from "@/components/research/alpha-mining-validation"
import { BacktestPane } from "@/components/research/alpha-mining-backtest"
import { MonitorPane } from "@/components/research/alpha-mining-monitor"
import { GovernancePane } from "@/components/research/alpha-mining-governance"
import type { DeployLogEntry } from "@/components/research/alpha-mining-governance"
import {
  Sparkles, Play, Square, RefreshCw, Loader2, Brain, TrendingUp, Zap,
  CheckCircle2, XCircle, Clock, FolderOpen, Rocket, Info, Database, Sparkle, Archive, Download, Upload, BarChart3,
} from "lucide-react"

interface TearsheetData {
  strategy_name: string
  formula_readable: string
  available: boolean
  reason?: string
  n_bars?: number
  n_valid?: number
  summary?: { observations: number; ic_mean: number | null; turnover: number; abs_mean: number | null }
  ic_series?: (number | null)[]
  quantile_returns?: (number | null)[]
  timestamps?: number[]
}

interface TearsheetCompareResponse {
  symbol: string
  interval: string
  tearsheets: TearsheetData[]
  missing: string[]
}

interface SavedStrategy {
  name: string
  version: string
  formula_readable: string
  formula_tokens: number[]
  profiles: Array<Record<string, unknown>>
  review_status?: string
}

// 三套生命周期词表并存(刀6 UI 消歧义,零迁移):
// ① saved-strategies review_status(此处)= 业务审读流 → 徽章词头「审读:」
// ② episode lifecycle(episode-drawer)= 观察周期 → 徽章词头「周期:」
// ③ factor state(factor-lifecycle-panel)= IC 衰减判定 → 语境独立,无词头
const REVIEW_STATUS_LABELS: Record<string, { label: string; className: string }> = {
  discovered: { label: "审读:未审读", className: "bg-muted text-muted-foreground" },
  inspected: { label: "审读:已审读", className: "bg-blue-500/15 text-blue-500" },
  paper_trading: { label: "审读:模拟盘", className: "bg-amber-500/15 text-amber-500" },
  live: { label: "审读:实盘", className: "bg-green-500/15 text-green-500" },
  retired: { label: "审读:已退役", className: "bg-red-500/15 text-red-500" },
}

// 增量合并历史:后端 status 每次返回全量 history,去重累计(step 单调递增,
// 避免因轮询/重连重复 append)。stable_rank 是稀疏数组,不能靠 step 索引合并,
// 只能整体替换(后端始终返回完整稀疏数组)。
function mergeHistory(prev: TrainingHistory, next: TrainingHistory): TrainingHistory {
  if (next.step.length === 0) return prev
  const lastPrevStep = prev.step.length > 0 ? prev.step[prev.step.length - 1] : -1
  const cutoff = next.step.findIndex((s) => s > lastPrevStep)
  if (cutoff === -1) return prev
  const merged: TrainingHistory = {
    step: [...prev.step, ...next.step.slice(cutoff)],
    avg_reward: [...prev.avg_reward, ...next.avg_reward.slice(cutoff)],
    best_score: [...prev.best_score, ...next.best_score.slice(cutoff)],
    stable_rank: next.stable_rank,
  }
  // reward_std 与 step 同频稠密,按同一 cutoff 追加
  if (next.reward_std) {
    merged.reward_std = [...(prev.reward_std ?? []), ...next.reward_std.slice(cutoff)]
  }
  // rank_step 与 stable_rank 同分支生成(稀疏),同样整体替换
  if (next.rank_step) {
    merged.rank_step = next.rank_step
  }
  return merged
}

export function AlphaMiningPanel() {
  const { pageFilter } = useContextStore()
  const [active, setActive] = useState(0)
  const [deployLog, setDeployLog] = useState<DeployLogEntry[]>([])
  const [training, setTraining] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [status, setStatus] = useState<TrainStatus | null>(null)
  const [history, setHistory] = useState<TrainingHistory>({ step: [], avg_reward: [], best_score: [], stable_rank: [] })
  const [logs, setLogs] = useState<string[]>([])
  const [formulas, setFormulas] = useState<MinedFormulaInfo[]>([])
  const [trainSteps, setTrainSteps] = useState(100)
  const [batchSize, setBatchSize] = useState(256)
  const [useLORD, setUseLORD] = useState(true)
  // 训练配置 localStorage 持久化(刀A 动线压缩):进面板自动恢复上次配置。
  const LS_KEY = "alpha-mining-config-v1"
  type MiningConfig = {
    exchange: string; symbol: string; market_type: string; interval: string;
    trainSteps: number; batchSize: number; useLORD: boolean; dataFile: string; batchMode: boolean;
  }
  const [dataSource, setDataSource] = useState(() => {
    try {
      const saved = localStorage.getItem(LS_KEY)
      if (saved) {
        const cfg = JSON.parse(saved) as Partial<MiningConfig>
        return {
          exchange: cfg.exchange ?? "binance",
          symbol: cfg.symbol ?? "BTC/USDT",
          market_type: cfg.market_type ?? "futures",
          interval: cfg.interval ?? "1h",
        }
      }
    } catch { /* corrupted storage → defaults */ }
    return { exchange: "binance", symbol: "BTC/USDT", market_type: "futures", interval: "1h" }
  })
  const [coverage, setCoverage] = useState<{ count: number; latest_iso: string | null } | null>(null)
  const [dataFile, setDataFile] = useState(() => {
    try {
      const saved = localStorage.getItem(LS_KEY)
      if (saved) return (JSON.parse(saved) as Partial<MiningConfig>).dataFile ?? ""
    } catch { /* corrupted storage → default */ }
    return ""
  })
  const [dataFiles, setDataFiles] = useState<{ name: string; path: string }[]>([])
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)
  const [coverageTick, setCoverageTick] = useState(0)
  const [coverageLoading, setCoverageLoading] = useState(false)
  const [coverageError, setCoverageError] = useState<string | null>(null)
  const [savedStrategies, setSavedStrategies] = useState<SavedStrategy[]>([])
  const [selectedStrategies, setSelectedStrategies] = useState<Set<string>>(new Set())
  const [batchUpdating, setBatchUpdating] = useState(false)
  const [tearsheets, setTearsheets] = useState<TearsheetData[]>([])
  const [tearsheetLoading, setTearsheetLoading] = useState(false)
  const [backtestResult, setBacktestResult] = useState<BacktestDetail | null>(null)
  const [backtestLoading, setBacktestLoading] = useState(false)
  const [deployingStrategy, setDeployingStrategy] = useState<string | null>(null)
  const [deployStatus, setDeployStatus] = useState<Record<string, { ok: boolean; msg: string; regime?: string | null }> | null>(null)
  const [formulaDeployStatus, setFormulaDeployStatus] = useState<Record<string, { ok: boolean; msg: string }> | null>(null)
  const [detailFormula, setDetailFormula] = useState<FormulaDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [explain, setExplain] = useState<FormulaExplain | null>(null)
  const [explainLoading, setExplainLoading] = useState(false)
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([])
  const [artifactsLoading, setArtifactsLoading] = useState(false)
  const [artifactsMsg, setArtifactsMsg] = useState<string | null>(null)
  const [artifactsError, setArtifactsError] = useState<string | null>(null)
  const [resumableSessions, setResumableSessions] = useState<MiningSession[]>([])
  const [resumingId, setResumingId] = useState<string | null>(null)
  const [resumeError, setResumeError] = useState<string | null>(null)

  // 批量模式:符号/周期多选 → POST /train/batch 笛卡尔积 fan-out,轮询聚合状态。
  const [batchMode, setBatchMode] = useState(() => {
    try {
      const saved = localStorage.getItem(LS_KEY)
      if (saved) return (JSON.parse(saved) as Partial<MiningConfig>).batchMode ?? false
    } catch { /* corrupted storage → default */ }
    return false
  })
  const [batchSymbols, setBatchSymbols] = useState<string[]>(["BTC/USDT"])
  const [batchIntervals, setBatchIntervals] = useState<string[]>(["1h"])
  const [batchConcurrency, setBatchConcurrency] = useState(4)
  const [batchId, setBatchId] = useState<string | null>(null)
  const [batch, setBatch] = useState<BatchStatus | null>(null)
  const [batchError, setBatchError] = useState<string | null>(null)
  // 逐条 T4 裁决(L2):formula_id(session_id) → verdict;validating 集合标记进行中
  const [verdicts, setVerdicts] = useState<Record<string, FormulaVerdict>>({})
  const [validating, setValidating] = useState<Set<string>>(new Set())

  // 配置变化即持久化(含 trainSteps/batchSize/useLORD/dataFile/batchMode)
  useEffect(() => {
    const cfg: MiningConfig = {
      exchange: dataSource.exchange, symbol: dataSource.symbol,
      market_type: dataSource.market_type, interval: dataSource.interval,
      trainSteps, batchSize, useLORD, dataFile, batchMode,
    }
    try { localStorage.setItem(LS_KEY, JSON.stringify(cfg)) } catch { /* storage full */ }
  }, [dataSource, trainSteps, batchSize, useLORD, dataFile, batchMode])

  // 沙箱根 parquet 枚举(刀A 下拉数据源)。挂载风暴期每个 POST 都会
  // abortAllPendingGets()(api-client),GET 会被反复掐死 → 退避重试 3 次
  useEffect(() => {
    let cancelled = false
    const attempt = (n: number): Promise<void> =>
      listDataFiles()
        .then((files) => { if (!cancelled) setDataFiles(files.map((f) => ({ name: f.name, path: f.path }))) })
        .catch(() => {
          if (cancelled || n <= 0) return
          return new Promise<void>((r) => setTimeout(r, 2000 * (4 - n))).then(() => attempt(n - 1))
        })
    attempt(3)
    return () => { cancelled = true }
  }, [])

  const handleSyncKline = useCallback(async () => {
    setSyncing(true)
    setSyncMsg(null)
    try {
      const r = await syncKlinePartition(dataSource)
      setSyncMsg(`已写入 ${r.written} 根`)
      setCoverageTick((t) => t + 1)
    } catch {
      setSyncMsg("同步失败(交易所不可达?)")
    } finally {
      setSyncing(false)
    }
  }, [dataSource])

  const validateOne = useCallback(async (fid: string) => {
    setValidating((prev) => new Set(prev).add(fid))
    try {
      const v = await validateFormula(fid)
      setVerdicts((prev) => ({ ...prev, [fid]: v }))
    } catch {
      // 404/409:公式不存在或无 best_formula — 沉默,网格保持无徽章
    } finally {
      setValidating((prev) => {
        const next = new Set(prev)
        next.delete(fid)
        return next
      })
    }
  }, [])

  // 一键送验证:所有完成行批量触发(逐条端点,前端并发)
  const validateAllCompleted = useCallback(() => {
    if (!batch) return
    for (const s of batch.sessions) {
      if (s.status === "completed" && !verdicts[s.session_id] && !validating.has(s.session_id)) {
        void validateOne(s.session_id)
      }
    }
  }, [batch, verdicts, validating, validateOne])

  const verdictBadge = (fid: string) => {
    const v = verdicts[fid]
    if (validating.has(fid)) return <Badge variant="outline" className="text-[10px] text-muted-foreground">验证中…</Badge>
    if (!v) return null
    if (v.decision === "accept") return <Badge variant="secondary" className="text-[10px] bg-emerald-500/15 text-emerald-600">T4 通过</Badge>
    if (v.decision === "hold" || v.decision === "defer") return <Badge variant="outline" className="text-[10px] border-amber-500/50 text-amber-600" title={v.reason}>T4 暂缓</Badge>
    return <Badge variant="destructive" className="text-[10px]" title={v.reason}>T4 拒绝</Badge>
  }

  const openFormulaDetail = useCallback(async (formulaId: string) => {
    setDetailLoading(true)
    setDetailOpen(true)
    setDetailFormula(null)
    try {
      const data = await getFormulaDetail(formulaId)
      setDetailFormula(data)
    } catch {
      // ignore
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const requestExplain = useCallback(async () => {
    if (!sessionId) return
    try {
      setExplainLoading(true)
      const data = await getFormulaExplain(sessionId)
      setExplain(data)
    } catch {
      // ignore
    } finally {
      setExplainLoading(false)
    }
  }, [sessionId])

  // 本地 klines 覆盖:根数 + 最新时间(kline-coverage COUNT 查询,负载极小;
  // 不用 getKline 1500 根大包 — 挂载并发风暴下会撞 12s GET 超时)
  useEffect(() => {
    let cancelled = false
    setCoverageLoading(true)
    setCoverageError(null)
    const fetchOnce = () =>
      getKlineCoverage(dataSource).then((cov) => {
        if (cancelled) return
        setCoverage({ count: cov.count, latest_iso: cov.latest_iso })
        if (cov.count === 0) setCoverageError("无本地数据,训练将使用合成数据")
      })
    fetchOnce()
      .catch(() =>
        // 挂载时几十个请求齐发,dev 代理偶发拥塞 → 重试一次
        new Promise((r) => setTimeout(r, 1500))
          .then(fetchOnce)
          .catch(() => { if (!cancelled) setCoverageError("无法读取本地 klines 覆盖") }),
      )
      .finally(() => {
        if (!cancelled) setCoverageLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [dataSource, coverageTick])

  const startTraining = useCallback(async () => {
    try {
      setTraining(true)
      setHistory({ step: [], avg_reward: [], best_score: [], stable_rank: [] })
      setLogs([])
      const data = await apiPost<{ session_id: string }>("/api/v1/alpha/train", {
        train_steps: trainSteps,
        batch_size: batchSize,
        use_lord: useLORD,
        symbol: dataSource.symbol,
        exchange: dataSource.exchange,
        market_type: dataSource.market_type,
        interval: dataSource.interval,
        use_synthetic_data: false,
        ...(dataFile.trim() ? { data_file: dataFile.trim() } : {}),
      })
      setSessionId(data.session_id)
    } catch {
      setTraining(false)
    }
  }, [trainSteps, batchSize, useLORD, dataSource, dataFile])

  const stopTraining = useCallback(async () => {
    if (!sessionId) return
    try {
      await apiPost(`/api/v1/alpha/train/${sessionId}/stop`)
    } catch {
      // ignore
    }
  }, [sessionId])

  const startBatchTrainingRun = useCallback(async () => {
    setBatchError(null)
    try {
      setTraining(true)
      const data = await startBatchTraining({
        symbols: batchSymbols,
        intervals: batchIntervals,
        max_concurrency: batchConcurrency,
        train_steps: trainSteps,
        batch_size: batchSize,
        use_lord: useLORD,
        exchange: dataSource.exchange,
        market_type: dataSource.market_type,
      })
      setBatchId(data.batch_id)
    } catch {
      setTraining(false)
      setBatchError("批量启动失败")
    }
  }, [batchSymbols, batchIntervals, batchConcurrency, trainSteps, batchSize, useLORD, dataSource])

  const fetchFormulas = useCallback(async () => {
    try {
      const data = await api<{ formulas: MinedFormulaInfo[] }>("/api/v1/alpha/formulas")
      setFormulas(data.formulas ?? [])
    } catch {
      // ignore
    }
  }, [])

  // 批量轮询:聚合状态写穿;全部终态后停轮询并刷新公式。
  useEffect(() => {
    if (!batchId || !training) return
    let stopped = false
    const poll = async () => {
      try {
        const data = await getBatchStatus(batchId)
        if (stopped) return
        setBatch(data)
        const terminal = ["completed", "failed", "stopped"]
        const allDone = data.sessions.length > 0 && data.sessions.every((s) => terminal.includes(s.status))
        if (allDone) {
          setTraining(false)
          fetchFormulas()
        }
      } catch {
        // ignore poll errors
      }
    }
    poll()
    const interval = setInterval(poll, 3000)
    return () => {
      stopped = true
      clearInterval(interval)
    }
  }, [batchId, training, fetchFormulas])

  // 训练状态轮询:实时 history 写穿 + 结束时刷新公式
  useEffect(() => {
    if (!sessionId || !training) return
    const interval = setInterval(async () => {
      try {
        const data = await getTrainStatus(sessionId)
        setStatus(data)
        if (data.history && data.history.step.length > 0) {
          setHistory((prev) => mergeHistory(prev, data.history!))
        }
        if (data.status === "completed" || data.status === "failed" || data.status === "stopped") {
          setTraining(false)
          fetchFormulas()
        }
      } catch {
        // ignore poll errors
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [sessionId, training, fetchFormulas])

  // 实时日志面板:独立轮询 logs 端点(环形缓冲,后端已封顶 200 条)
  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    const pollLogs = async () => {
      try {
        const data = await getTrainLogs(sessionId)
        if (!cancelled) setLogs(data.logs ?? [])
      } catch {
        // ignore
      }
    }
    pollLogs()
    const interval = setInterval(pollLogs, 2000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [sessionId])

  const deployFormula = useCallback(async (formulaId: string) => {
    // 批量会话发起的部署在日志里带 batch: 前缀,治理 tab 据此分组展示。
    const source = batchId && formulaId.startsWith("alpha_") && formulaId.includes(batchId.slice(-8)) ? `batch:${batchId}` : null
    try {
      await apiPost(`/api/v1/alpha/formulas/${formulaId}/deploy`)
      setFormulaDeployStatus((prev) => ({
        ...prev,
        [formulaId]: { ok: true, msg: "已部署(配置已写入 deployed/)" },
      }))
      setDeployLog((prev) => [...prev, { target: formulaId, ok: true, at: new Date().toISOString(), note: source ?? "T4 放行" }])
      fetchFormulas()
    } catch (err) {
      // T4 gate refusal surfaces as 403 with the verdict reason in detail.
      const msg = err instanceof Error ? err.message : String(err)
      setFormulaDeployStatus((prev) => ({
        ...prev,
        [formulaId]: { ok: false, msg },
      }))
      setDeployLog((prev) => [...prev, { target: formulaId, ok: false, at: new Date().toISOString(), note: source ? `${source} ${msg}` : msg }])
    }
  }, [fetchFormulas, batchId])

  // 刀③ 产物自动流转:完成行"回测"就地跑 + 结果内联展示(不切 tab、不丢训练现场)。
  const [inlineBacktest, setInlineBacktest] = useState<{
    fid: string
    running: boolean
    err: string | null
    result: MinedFormulaBacktest | null
  } | null>(null)

  const runFormulaBacktestInline = useCallback(
    async (formulaId: string) => {
      setInlineBacktest({ fid: formulaId, running: true, err: null, result: null })
      try {
        const result = await backtestMinedFormula(formulaId, {
          symbol: dataSource.symbol,
          exchange: dataSource.exchange,
          market_type: dataSource.market_type,
          interval: dataSource.interval,
        })
        setInlineBacktest({ fid: formulaId, running: false, err: null, result })
      } catch (e) {
        setInlineBacktest({
          fid: formulaId,
          running: false,
          err: e instanceof Error ? e.message : String(e),
          result: null,
        })
      }
    },
    [dataSource],
  )

  const fetchSavedStrategies = useCallback(async () => {
    try {
      const data = await api<{ strategies: SavedStrategy[] }>("/api/v1/alpha/saved-strategies")
      setSavedStrategies(data.strategies ?? [])
    } catch {
      // ignore
    }
  }, [])

  const fetchTearsheets = useCallback(async (names: string[]) => {
    if (names.length === 0) return
    try {
      setTearsheetLoading(true)
      const data = await apiPost<TearsheetCompareResponse>("/api/v1/alpha/saved-strategies/tearsheet/compare", {
        strategy_names: names,
        symbol: "000001.SZ",
        interval: "1d",
        exchange: "astock",
      })
      setTearsheets(data.tearsheets ?? [])
    } catch {
      // ignore
    } finally {
      setTearsheetLoading(false)
    }
  }, [])

  const showTearsheet = useCallback((strategyName: string) => {
    fetchTearsheets([strategyName])
  }, [fetchTearsheets])

  const compareTearsheets = useCallback(() => {
    fetchTearsheets([...selectedStrategies].slice(0, 5))
  }, [selectedStrategies, fetchTearsheets])

  const batchUpdateReviewStatus = useCallback(async (reviewStatus: string) => {
    if (selectedStrategies.size === 0) return
    try {
      setBatchUpdating(true)
      await apiPatch("/api/v1/alpha/saved-strategies/review-status/batch", {
        strategy_names: [...selectedStrategies],
        review_status: reviewStatus,
      })
      await fetchSavedStrategies()
    } catch {
      // ignore
    } finally {
      setBatchUpdating(false)
    }
  }, [selectedStrategies, fetchSavedStrategies])

  const fetchArtifacts = useCallback(async () => {
    setArtifactsLoading(true)
    setArtifactsError(null)
    try {
      const data = await listArtifacts()
      setArtifacts(data)
    } catch {
      setArtifactsError("无法读取产物列表")
    } finally {
      setArtifactsLoading(false)
    }
  }, [])

  // 可恢复会话:stopped/failed 的训练会话(有 checkpoint)可继续训练。
  // 列表仅来自内存 _sessions;重启后端后 stopped 会话仍可凭 checkpoint 恢复,
  // 但此列表不再展示(会话重建发生在 resume 端点内)。
  const fetchResumableSessions = useCallback(async () => {
    try {
      const sessions = await listMiningSessions()
      setResumableSessions(
        sessions.filter((s) => s.status === "stopped" || s.status === "failed")
      )
    } catch {
      // ignore
    }
  }, [])

  const handleResume = useCallback(async (sid: string) => {
    setResumeError(null)
    setResumingId(sid)
    try {
      await resumeTraining(sid)
      // 复用现有训练轮询:切到该会话并进入训练态,status 端点返回衔接后的 history。
      setSessionId(sid)
      setTraining(true)
    } catch {
      setResumeError("恢复失败:checkpoint 不可用或会话正在运行")
    } finally {
      setResumingId(null)
    }
  }, [])

  const handleExportArtifacts = useCallback(async () => {
    setArtifactsMsg(null)
    setArtifactsError(null)
    try {
      const data = await exportArtifacts()
      if (!data.zip_base64) {
        setArtifactsError("当前没有可导出的产物")
        return
      }
      // base64 → Blob → download
      const bytes = Uint8Array.from(atob(data.zip_base64), (c) => c.charCodeAt(0))
      const blob = new Blob([bytes], { type: "application/zip" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `alpha-artifacts-${new Date().toISOString().slice(0, 10)}.zip`
      a.click()
      URL.revokeObjectURL(url)
      setArtifactsMsg(`已导出 ${data.count} 个产物`)
    } catch {
      setArtifactsError("导出失败")
    }
  }, [])

  const handleImportArtifacts = useCallback(async (file: File) => {
    setArtifactsMsg(null)
    setArtifactsError(null)
    try {
      const bytes = new Uint8Array(await file.arrayBuffer())
      let bin = ""
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
      const b64 = btoa(bin)
      // Bare .pt/.json goes through the raw channel; zips stay zipped.
      const isZip = file.name.toLowerCase().endsWith(".zip")
      const data = await importArtifacts(
        isZip ? { zip_base64: b64 } : { raw_name: file.name, raw_base64: b64 }
      )
      setArtifactsMsg(`已导入 ${data.imported.length} 个产物${data.skipped.length ? `,跳过 ${data.skipped.length} 个` : ""}`)
      fetchArtifacts()
      fetchFormulas()
    } catch {
      setArtifactsError("导入失败:文件格式无效")
    }
  }, [fetchArtifacts, fetchFormulas])

  const onDeployStrategy = useCallback((log: DeployLogEntry) => {
    setDeployLog((prev) => [...prev, log])
  }, [])

  const backtestSavedStrategy = useCallback(async (strategyName: string, interval: string = "15m") => {
    try {
      setBacktestLoading(true)
      setBacktestResult(null)
      const data = await apiPost<BacktestDetail>(
        `/api/v1/alpha/saved-strategies/${encodeURIComponent(strategyName)}/backtest`,
        { symbol: toSymbolPair(pageFilter.symbol), interval, exchange: "binance", market_type: "futures", initial_capital: 10000 }
      )
      setBacktestResult(data)
    } catch {
      // ignore
    } finally {
      setBacktestLoading(false)
    }
  }, [pageFilter.symbol])

  const deploySavedStrategy = useCallback(async (strategyName: string, interval: string = "15m") => {
    try {
      setDeployingStrategy(strategyName)
      const data = await apiPost<{ deployment_id: string; market_regime?: string | null; regime_warning?: string | null }>(
        `/api/v1/alpha/saved-strategies/${encodeURIComponent(strategyName)}/deploy`,
        { symbol: toSymbolPair(pageFilter.symbol), interval }
      )
      setDeployStatus((prev) => ({
        ...prev,
        [strategyName]: { ok: true, msg: `已部署 -> ${data.deployment_id}`, regime: data.regime_warning ?? null },
      }))
    } catch (err) {
      setDeployStatus((prev) => ({
        ...prev,
        [strategyName]: { ok: false, msg: String(err) },
      }))
    } finally {
      setDeployingStrategy(null)
    }
  }, [pageFilter.symbol])

  const goValidation = useCallback(() => setActive(1), [])

  useEffect(() => {
    fetchFormulas()
    fetchSavedStrategies()
    fetchArtifacts()
    fetchResumableSessions()
  }, [fetchFormulas, fetchSavedStrategies, fetchArtifacts, fetchResumableSessions])

  const historyHasData = history.step.length > 0
  const useLordActive = useLORD

  return (
    <div className="h-full overflow-y-auto">
      <div className="space-y-4 pb-8">
      <WorkbenchTabBar active={active} onChange={setActive} />
      <GuideStrip active={active} onChange={setActive} />

      <WbPane active={active} index={0} testId="pane-train">
        <div className="space-y-4">
          {/* 数据源选择:真实 klines 覆盖显示 + 训练数据源 */}
          <div className="grid grid-cols-4 gap-3">
            <div>
              <label htmlFor="data-source-exchange" className="text-xs text-muted-foreground">交易所</label>
              <select
                id="data-source-exchange"
                value={dataSource.exchange}
                onChange={(e) => setDataSource((d) => ({ ...d, exchange: e.target.value }))}
                className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background"
                disabled={training}
              >
                <option value="binance">binance</option>
                <option value="okx">okx</option>
              </select>
            </div>
            <div>
              <label htmlFor="data-source-symbol" className="text-xs text-muted-foreground">品种</label>
              <select
                id="data-source-symbol"
                value={dataSource.symbol}
                onChange={(e) => setDataSource((d) => ({ ...d, symbol: e.target.value }))}
                className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background"
                disabled={training}
              >
                <option value="BTC/USDT">BTC/USDT</option>
                <option value="ETH/USDT">ETH/USDT</option>
                <option value="SOL/USDT">SOL/USDT</option>
              </select>
            </div>
            <div>
              <label htmlFor="data-source-interval" className="text-xs text-muted-foreground">周期</label>
              <select
                id="data-source-interval"
                value={dataSource.interval}
                onChange={(e) => setDataSource((d) => ({ ...d, interval: e.target.value }))}
                className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background"
                disabled={training}
              >
                {["1m", "5m", "15m", "30m", "1h", "4h"].map((iv) => (
                  <option key={iv} value={iv}>{iv}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="data-source-market-type" className="text-xs text-muted-foreground">市场类型</label>
              <select
                id="data-source-market-type"
                value={dataSource.market_type}
                onChange={(e) => setDataSource((d) => ({ ...d, market_type: e.target.value }))}
                className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background"
                disabled={training}
              >
                <option value="futures">futures</option>
                <option value="spot">spot</option>
              </select>
            </div>
          </div>
          <div className="text-xs text-muted-foreground flex items-center gap-2">
            <Database className="h-3.5 w-3.5" />
            本地 klines 覆盖:
            {coverageLoading ? (
              <span className="flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> 读取中...</span>
            ) : coverageError ? (
              <span className="text-amber-500">{coverageError}</span>
            ) : coverage ? (
              <span>{coverage.count.toLocaleString()} 根 · 至 {coverage.latest_iso ?? "—"}</span>
            ) : null}
            {coverage && coverage.count === 0 && !coverageLoading && (
              <Button onClick={handleSyncKline} variant="outline" size="sm" className="h-6 px-2 text-[11px] gap-1" disabled={syncing}>
                {syncing ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                同步 {dataSource.symbol} 365 天
              </Button>
            )}
            {syncMsg && <span className={syncMsg.startsWith("已写入") ? "text-emerald-600" : "text-red-500"}>{syncMsg}</span>}
          </div>
          <div>
            <label htmlFor="data-source-parquet" className="text-xs text-muted-foreground">Parquet 文件(可选,留空用 klines.db)</label>
            {dataFiles.length > 0 ? (
              <select
                id="data-source-parquet"
                value={dataFile}
                onChange={(e) => setDataFile(e.target.value)}
                className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background font-mono"
                disabled={training}
              >
                <option value="">(不使用,读 klines.db)</option>
                {dataFiles.map((f) => (
                  <option key={f.path} value={f.name}>{f.name}</option>
                ))}
              </select>
            ) : (
              <input
                id="data-source-parquet"
                type="text"
                value={dataFile}
                onChange={(e) => setDataFile(e.target.value)}
                placeholder="如 btc_1h.parquet(~/.selenespace/data 或 demo/data 下)"
                className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background font-mono"
                disabled={training}
              />
            )}
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">训练步数</label>
              <input type="number" value={trainSteps} onChange={(e) => setTrainSteps(Number(e.target.value))} className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background" disabled={training} />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">批量大小</label>
              <input type="number" value={batchSize} onChange={(e) => setBatchSize(Number(e.target.value))} className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background" disabled={training} />
            </div>
            <div className="flex items-end gap-2">
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                <input type="checkbox" checked={useLORD} onChange={(e) => setUseLORD(e.target.checked)} disabled={training} />
                LoRD 正则化
              </label>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <label className="flex items-center gap-1.5 text-muted-foreground cursor-pointer">
              <input type="checkbox" checked={batchMode} onChange={(e) => setBatchMode(e.target.checked)} disabled={training} />
              批量模式
            </label>
            {batchMode && <span className="text-muted-foreground">按 (品种 × 周期) 笛卡尔积每对一个训练会话</span>}
          </div>

          {batchMode ? (
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">品种(多选,Ctrl+点击)</label>
                <select
                  multiple
                  value={batchSymbols}
                  onChange={(e) => setBatchSymbols(Array.from(e.target.selectedOptions).map((o) => o.value))}
                  className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background h-20"
                  disabled={training}
                >
                  {["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"].map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">周期(多选,Ctrl+点击)</label>
                <select
                  multiple
                  value={batchIntervals}
                  onChange={(e) => setBatchIntervals(Array.from(e.target.selectedOptions).map((o) => o.value))}
                  className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background h-20"
                  disabled={training}
                >
                  {["15m", "1h", "4h"].map((iv) => (
                    <option key={iv} value={iv}>{iv}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">并发上限</label>
                <input type="number" min={1} max={32} value={batchConcurrency} onChange={(e) => setBatchConcurrency(Number(e.target.value))} className="w-full mt-1 px-2 py-1 text-sm border rounded bg-background" disabled={training} />
                <div className="text-[11px] text-muted-foreground mt-1">
                  将启动 {batchSymbols.length * batchIntervals.length} 个会话
                </div>
              </div>
            </div>
          ) : null}

          <div className="flex gap-2">
            {!training ? (
              batchMode ? (
                <Button onClick={startBatchTrainingRun} size="sm" className="gap-1.5" disabled={batchSymbols.length === 0 || batchIntervals.length === 0}>
                  <Play className="h-3.5 w-3.5" />批量挖矿({batchSymbols.length * batchIntervals.length})
                </Button>
              ) : (
                <Button onClick={startTraining} size="sm" className="gap-1.5"><Play className="h-3.5 w-3.5" />开始挖矿</Button>
              )
            ) : (
              <Button onClick={stopTraining} variant="destructive" size="sm" className="gap-1.5"><Square className="h-3.5 w-3.5" />停止</Button>
            )}
            <Button onClick={fetchFormulas} variant="outline" size="sm" className="gap-1.5"><RefreshCw className="h-3.5 w-3.5" />刷新公式</Button>
          </div>
          {batchError && <div className="text-xs text-red-500">{batchError}</div>}

          {/* 公式流大屏:常驻(hero score + 空态),取值来自最近一次轮询的 status,无会话时显示空态。 */}
          <div className="rounded-lg border border-border/70 bg-muted/30 p-4" data-testid="formula-stream">
            <div className="flex items-start justify-between gap-6">
              <div className="min-w-0">
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground">当前最优公式</div>
                {status?.formula_readable ? (
                  <code
                    className="mt-1.5 block truncate font-mono text-sm text-foreground"
                    title={status.formula_readable}
                    data-testid="formula-stream-decoded"
                  >
                    {status.formula_readable}
                  </code>
                ) : (
                  <div className="mt-1.5 text-sm text-muted-foreground">
                    {status?.status === "running" || status?.status === "starting" ? "矿机预热中,尚未发现首个因子…" : "暂无最优因子"}
                  </div>
                )}
              </div>
              <div className="shrink-0 text-right">
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground">Best Score</div>
                <div className="text-5xl leading-tight text-foreground" data-testid="formula-stream-score">
                  {status?.best_score != null ? status.best_score.toFixed(2) : "—"}
                </div>
              </div>
            </div>
            {status?.train_steps ? (
              <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-border">
                <div
                  className="h-full rounded-full bg-primary transition-[width] duration-500"
                  style={{ width: `${Math.min(100, ((status.step ?? 0) / status.train_steps) * 100).toFixed(1)}%` }}
                />
              </div>
            ) : null}
          </div>

          {status && (
            <div className="space-y-2">
              {status.data_source === "synthetic" && (
                <div className="text-xs text-amber-500 flex items-center gap-1.5">
                  <Info className="h-3 w-3" />
                  本地无该品种/周期数据,本次训练使用合成数据{status.fallback_reason ? ` (${status.fallback_reason})` : ""}
                </div>
              )}
              {status.data_source === "parquet" && (
                <div className="text-xs text-muted-foreground flex items-center gap-1.5">
                  <Info className="h-3 w-3" />
                  已加载 parquet 文件{dataFile.trim() ? `: ${dataFile.trim()}` : ""}
                </div>
              )}
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {status.status === "running" && <span className="flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> 训练中...</span>}
                  {status.status === "stopping" && <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> 正在停止...</span>}
                  {status.status === "completed" && <span className="flex items-center gap-1 text-green-500"><CheckCircle2 className="h-3 w-3" /> 已完成</span>}
                  {status.status === "stopped" && <span className="flex items-center gap-1 text-amber-500"><Square className="h-3 w-3" /> 已停止</span>}
                  {status.status === "failed" && <span className="flex items-center gap-1 text-red-500"><XCircle className="h-3 w-3" /> 失败</span>}
                  {status.status === "starting" && <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> 启动中...</span>}
                </span>
                <span>步数 {status.step}/{status.train_steps} | 最佳分数: {status.best_score?.toFixed(2) ?? "N/A"}</span>
              </div>
            </div>
          )}

          {/* 批量会话网格:每 (symbol,interval) 一行,聚合轮询写穿 */}
          {batchId && batch && (
            <div className="space-y-2 rounded-lg border border-border/70 p-3" data-testid="batch-grid">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><Info className="h-3.5 w-3.5" />批量会话 {batch.batch_id}</span>
                <span className="flex items-center gap-2">
                  <button
                    type="button"
                    className="text-[11px] underline-offset-2 hover:underline"
                    onClick={validateAllCompleted}
                    data-testid="validate-all-btn"
                  >
                    一键送验证
                  </button>
                  <span className="font-mono">并发 {batch.max_concurrency ?? "?"} · {batch.sessions.length} 个</span>
                </span>
              </div>
              <div className="space-y-1">
                {batch.sessions.map((s) => (
                  <div key={s.session_id} className="text-xs py-1 border-b border-border/40 last:border-b-0">
                    <div className="flex items-center justify-between">
                      <span className="font-mono">{s.symbol} {s.interval}</span>
                      <span className="flex items-center gap-3 text-muted-foreground">
                        <span>{s.step ?? 0}/{s.train_steps ?? "?"}</span>
                        <span>分数 {s.best_score?.toFixed(2) ?? "N/A"}</span>
                        <Badge
                          variant={s.status === "completed" ? "secondary" : s.status === "failed" ? "destructive" : "outline"}
                          className="text-[10px]"
                        >
                          {s.status === "completed" ? "完成" : s.status === "failed" ? "失败" : s.status === "running" ? "训练中" : s.status === "queued" ? "排队" : s.status === "stopping" ? "停止中" : s.status === "stopped" ? "已停止" : s.status}
                        </Badge>
                      </span>
                    </div>
                    {s.formula_readable && (
                      <code className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground" title={s.formula_readable}>
                        {s.formula_readable}
                      </code>
                    )}
                    {s.status === "completed" && (
                      <div className="mt-0.5 flex items-center justify-end gap-1.5">
                        {verdictBadge(s.session_id)}
                        {!verdicts[s.session_id] && (
                          <button
                            type="button"
                            className="text-[11px] underline-offset-2 hover:underline"
                            onClick={() => validateOne(s.session_id)}
                            disabled={validating.has(s.session_id)}
                          >
                            送验证
                          </button>
                        )}
                        <button type="button" className="text-[11px] underline-offset-2 hover:underline" onClick={() => goValidation()}>验证tab</button>
                        <button type="button" className="text-[11px] underline-offset-2 hover:underline" onClick={() => runFormulaBacktestInline(s.session_id)}>回测</button>
                        {(() => {
                          const v = verdicts[s.session_id]
                          const blocked = v && v.decision !== "accept"
                          return (
                            <button
                              type="button"
                              className={`text-[11px] underline-offset-2 ${blocked ? "line-through text-muted-foreground cursor-not-allowed" : "hover:underline"}`}
                              onClick={() => !blocked && deployFormula(s.session_id)}
                              title={blocked ? `T4 ${v.decision}: ${v.reason}` : "部署(经门控)"}
                            >
                              部署
                            </button>
                          )
                        })()}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 内联回测结果(刀③):就地显示,不切回测 tab。深度分析仍走回测 tab。 */}
          {inlineBacktest && (
            <div className="space-y-2 rounded-lg border border-border/70 p-3" data-testid="inline-backtest">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><TrendingUp className="h-3.5 w-3.5" />回测 · {inlineBacktest.fid}</span>
                <button
                  type="button"
                  className="text-[11px] underline-offset-2 hover:underline"
                  onClick={() => setInlineBacktest(null)}
                >
                  关闭
                </button>
              </div>
              {inlineBacktest.running ? (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" /> 跑回测中...
                </div>
              ) : inlineBacktest.err ? (
                <div className="text-xs text-red-500">{inlineBacktest.err}</div>
              ) : inlineBacktest.result ? (
                <div className="space-y-2">
                  <code className="block truncate font-mono text-[11px] text-foreground" title={inlineBacktest.result.formula_readable}>
                    {inlineBacktest.result.formula_readable}
                  </code>
                  <div className="flex flex-wrap gap-2 text-[11px]">
                    <Badge variant="outline" className="text-[11px]">
                      {inlineBacktest.result.source === "real" ? `真实数据 ${inlineBacktest.result.total_bars.toLocaleString()} 根` : "合成数据"}
                    </Badge>
                    <Badge variant="outline" className="text-[11px]">{inlineBacktest.result.symbol} {inlineBacktest.result.interval}</Badge>
                    <Badge variant="outline" className="text-[11px]">latest score {inlineBacktest.result.latest_score.toFixed(3)}</Badge>
                    {Object.entries(inlineBacktest.result.signal_counts).map(([sig, n]) => (
                      <Badge key={sig} variant="secondary" className="text-[11px]">{sig}: {n.toLocaleString()}</Badge>
                    ))}
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    净值曲线/滚动夏普等深度分析 → 回测 tab 选该公式跑富回测
                  </div>
                </div>
              ) : null}
            </div>
          )}

          {/* 双线训练曲线:best_score 水位线 + avg_reward 摆动,优势带 + LoRD 阶梯 sparkline。 */}
          {historyHasData && (
            <div className="rounded-lg border border-border/70 p-3">
              <TrainingChartCard history={history} useLord={useLordActive} />
            </div>
          )}

          {/* 实时日志面板(环形缓冲,后端封顶 200 条) */}
          {sessionId && (
            <div className="space-y-2 rounded-lg border border-border/70 p-3">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><Info className="h-3.5 w-3.5" />训练日志</span>
                <span className="font-mono">{logs.length} 条</span>
              </div>
              <div className="max-h-40 overflow-y-auto rounded bg-muted/50 p-2 font-mono text-[11px] leading-relaxed">
                {logs.length === 0 ? (
                  <span className="text-muted-foreground">暂无日志。训练中会输出新最佳因子。</span>
                ) : (
                  logs.map((line, i) => (
                    <div key={i} className={line.includes("New Best") ? "text-green-500" : "text-muted-foreground"}>{line}</div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* AI 解读卡片:调用 explain 端点,LLM 未配置时自动降级为规则解读 */}
          {sessionId && !training && (
            <div className="space-y-2 rounded-lg border border-border/70 p-3">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><Sparkle className="h-3.5 w-3.5 text-amber-500" />AI 解读</span>
                <Button onClick={requestExplain} variant="outline" size="sm" className="h-7 gap-1 text-xs" disabled={explainLoading}>
                  {explainLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkle className="h-3 w-3" />}{explain ? "重新解读" : "生成解读"}
                </Button>
              </div>
              {explain ? (
                <div className="space-y-2">
                  <p className="text-xs leading-relaxed text-foreground/90">{explain.explanation}</p>
                  <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                    <Badge variant="outline" className="text-[11px]">来源: {explain.source === "llm" ? "LLM" : "规则降级"}</Badge>
                    <Badge variant="outline" className="text-[11px]">步数 {explain.summary.step_count}</Badge>
                    <Badge variant="outline" className="text-[11px]">最佳分数 {explain.summary.best_score?.toFixed(2) ?? "N/A"}</Badge>
                  </div>
                </div>
              ) : explainLoading ? (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" /> 正在调用 AI 解读...
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">训练结束后可生成 AI 解读,分析最新因子含义与是否值得继续挖。</p>
              )}
            </div>
          )}

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2"><FolderOpen className="h-4 w-4 text-blue-500" />已保存的 Alpha 策略</CardTitle>
            <Button onClick={fetchSavedStrategies} variant="ghost" size="sm" className="h-7 gap-1 text-xs"><RefreshCw className="h-3 w-3" />刷新</Button>
          </div>
        </CardHeader>
        <CardContent>
          {savedStrategies.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground text-sm">暂无已保存的策略。运行 Optuna 公式搜索脚本以生成策略。</div>
          ) : (
            <div className="space-y-2">
              {selectedStrategies.size > 0 && (
                <div className="flex items-center gap-2 p-2 border rounded-lg bg-muted/40 sticky top-0 z-10">
                  <span className="text-xs text-muted-foreground">已选 {selectedStrategies.size} 项 → 设为</span>
                  {(["inspected", "paper_trading", "live", "retired"] as const).map((st) => (
                    <Button key={st} onClick={() => batchUpdateReviewStatus(st)} variant="outline" size="sm" className="h-6 text-xs" disabled={batchUpdating}>
                      {REVIEW_STATUS_LABELS[st].label}
                    </Button>
                  ))}
                  <Button onClick={() => setSelectedStrategies(new Set())} variant="ghost" size="sm" className="h-6 text-xs">取消</Button>
                  {selectedStrategies.size >= 2 && (
                    <Button onClick={compareTearsheets} variant="secondary" size="sm" className="h-6 text-xs gap-1" disabled={tearsheetLoading}>
                      {tearsheetLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <BarChart3 className="h-3 w-3" />}对比透视
                    </Button>
                  )}
                </div>
              )}
              {savedStrategies.map((s) => {
                const review = REVIEW_STATUS_LABELS[s.review_status || "discovered"] || REVIEW_STATUS_LABELS.discovered
                const checked = selectedStrategies.has(s.name)
                return (
                <div key={`${s.version}-${s.name}`} className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/50 transition-colors">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          const next = new Set(selectedStrategies)
                          if (e.target.checked) next.add(s.name)
                          else next.delete(s.name)
                          setSelectedStrategies(next)
                        }}
                        className="h-3.5 w-3.5 accent-blue-500"
                      />
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono truncate max-w-md">{s.formula_readable}</code>
                      <Badge variant="secondary" className="text-xs">{s.version}</Badge>
                      <Badge variant="outline" className="text-xs">{s.profiles.length} 配置</Badge>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${review.className}`}>{review.label}</span>
                    </div>
                    <div className="text-xs text-muted-foreground mt-1">{s.name} | {s.formula_tokens.length} tokens | {s.profiles.map((p) => (p as Record<string, unknown>).base_interval || "15m").join("/")}</div>
                  </div>
                  <div className="flex gap-1.5 ml-2">
                    {(s.profiles as Array<Record<string, unknown>>).map((p, idx) => {
                      const interval = String(p.base_interval || "15m")
                      return (
                        <Button key={idx} onClick={() => backtestSavedStrategy(s.name, interval)} variant="outline" size="sm" className="h-7 text-xs gap-1" disabled={backtestLoading}>
                          {backtestLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <TrendingUp className="h-3 w-3" />}{interval}
                        </Button>
                      )
                    })}
                    <Button onClick={() => showTearsheet(s.name)} variant="outline" size="sm" className="h-7 text-xs gap-1" disabled={tearsheetLoading}>
                      <BarChart3 className="h-3 w-3" />透视
                    </Button>
                    <Button onClick={() => deploySavedStrategy(s.name, String((s.profiles[0] as Record<string, unknown>)?.base_interval || "15m"))} size="sm" className="h-7 text-xs gap-1" disabled={deployingStrategy === s.name}>
                      {deployingStrategy === s.name ? <Loader2 className="h-3 w-3 animate-spin" /> : <Rocket className="h-3 w-3" />}模拟
                    </Button>
                  </div>
                  {deployStatus?.[s.name] && (
                    <div className={`text-xs ml-2 ${deployStatus[s.name].ok ? "text-green-500" : "text-red-500"}`}>
                      {deployStatus[s.name].ok ? <CheckCircle2 className="h-3 w-3 inline mr-1" /> : <XCircle className="h-3 w-3 inline mr-1" />}{deployStatus[s.name].msg}
                      {deployStatus[s.name].ok && deployStatus[s.name].regime && (
                        <span className="text-amber-500 ml-1">⚠ {deployStatus[s.name].regime}</span>
                      )}
                    </div>
                  )}
                </div>
              )})}
              {tearsheets.length > 0 && (
                <div className="mt-3 p-3 border rounded-lg bg-muted/30 space-y-3">
                  <div className="text-xs font-medium flex items-center gap-1.5">
                    <BarChart3 className="h-3.5 w-3.5" />因子透视
                    <span className="text-muted-foreground font-normal ml-1">000001.SZ 1d | 滚动窗口20 | 分位5档</span>
                    <Button onClick={() => setTearsheets([])} variant="ghost" size="sm" className="h-6 ml-auto text-xs">关闭</Button>
                  </div>
                  <div className={`grid gap-3 ${tearsheets.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
                    {tearsheets.map((t) => (
                      <div key={t.strategy_name} className="p-2.5 border rounded bg-background space-y-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-xs font-medium truncate">{t.strategy_name}</span>
                          <code className="text-[10px] bg-muted px-1 py-0.5 rounded font-mono truncate">{t.formula_readable}</code>
                        </div>
                        {!t.available ? (
                          <div className="text-xs text-amber-600 py-2">⚠ {t.reason ?? "数据不足或公式无法执行"}</div>
                        ) : (
                          <>
                            <div className="grid grid-cols-4 gap-2 text-xs">
                              <div><span className="text-muted-foreground">IC均值</span><div className={`font-mono font-medium ${(t.summary?.ic_mean ?? 0) > 0.02 ? "text-green-500" : (t.summary?.ic_mean ?? 0) < -0.02 ? "text-red-500" : ""}`}>{t.summary?.ic_mean != null ? t.summary.ic_mean.toFixed(4) : "N/A"}</div></div>
                              <div><span className="text-muted-foreground">换手</span><div className="font-mono font-medium">{(t.summary?.turnover ?? 0).toFixed(3)}</div></div>
                              <div><span className="text-muted-foreground">样本</span><div className="font-mono font-medium">{t.summary?.observations ?? 0}</div></div>
                              <div><span className="text-muted-foreground">K线数</span><div className="font-mono font-medium">{t.n_bars ?? 0}</div></div>
                            </div>
                            <div>
                              <div className="text-[10px] text-muted-foreground mb-1">分位收益（Q1最低档 → Q5最高档，%）</div>
                              <div className="flex gap-1 items-end h-10">
                                {(t.quantile_returns ?? []).map((q, i) => {
                                  const maxAbs = Math.max(...(t.quantile_returns ?? [1]).map((v) => Math.abs(v ?? 0)), 0.01)
                                  const h = Math.max(Math.abs(q ?? 0) / maxAbs * 100, 4)
                                  return (
                                    <div key={i} className="flex-1 flex flex-col items-center gap-0.5">
                                      <div className={`w-full rounded-sm ${q != null && q > 0 ? "bg-green-500/70" : "bg-red-500/70"}`} style={{ height: `${h}%` }} />
                                      <span className="text-[9px] text-muted-foreground">Q{i + 1}</span>
                                    </div>
                                  )
                                })}
                              </div>
                            </div>
                            <div>
                              <div className="text-[10px] text-muted-foreground mb-1">滚动 IC</div>
                              <svg viewBox="0 0 100 24" className="w-full h-8" preserveAspectRatio="none">
                                <line x1="0" y1="12" x2="100" y2="12" stroke="currentColor" strokeWidth="0.3" className="text-muted-foreground/30" />
                                {(() => {
                                  const ic = (t.ic_series ?? []).filter((v): v is number => v != null)
                                  if (ic.length < 2) return null
                                  const step = Math.max(1, Math.floor(ic.length / 200))
                                  const pts = ic.filter((_, i) => i % step === 0)
                                  const maxAbs = Math.max(...pts.map(Math.abs), 0.01)
                                  const path = pts.map((v, i) => `${i === 0 ? "M" : "L"}${(i / (pts.length - 1)) * 100},${12 - (v / maxAbs) * 10}`).join(" ")
                                  return <path d={path} fill="none" strokeWidth="0.6" className="stroke-blue-500" />
                                })()}
                              </svg>
                            </div>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {backtestResult && (
                <div className="mt-3 p-3 border rounded-lg bg-muted/30 space-y-3">
                  <div className="text-xs font-medium flex items-center gap-1.5"><TrendingUp className="h-3.5 w-3.5" />回测结果 -- {String(backtestResult.strategy_name)} ({String(backtestResult.symbol)} {String(backtestResult.interval)})</div>
                  <div className="grid grid-cols-4 gap-3 text-xs md:grid-cols-8">
                    <div><span className="text-muted-foreground">总收益</span><div className={`font-mono font-medium ${Number(backtestResult.total_return || 0) >= 0 ? "text-green-500" : "text-red-500"}`}>{Number(backtestResult.total_return || 0) >= 0 ? "+" : ""}{(Number(backtestResult.total_return || 0) * 100).toFixed(1)}%</div></div>
                    <div><span className="text-muted-foreground">年化收益</span><div className={`font-mono font-medium ${Number(backtestResult.annual_return || 0) >= 0 ? "text-green-500" : "text-red-500"}`}>{Number(backtestResult.annual_return || 0) >= 0 ? "+" : ""}{(Number(backtestResult.annual_return || 0) * 100).toFixed(1)}%</div></div>
                    <div><span className="text-muted-foreground">夏普</span><div className="font-mono font-medium">{Number(backtestResult.sharpe_ratio || 0).toFixed(2)}</div></div>
                    <div><span className="text-muted-foreground">Sortino</span><div className="font-mono font-medium">{Number(backtestResult.sortino_ratio || 0).toFixed(2)}</div></div>
                    <div><span className="text-muted-foreground">最大回撤</span><div className="font-mono font-medium text-red-500">{(Number(backtestResult.max_drawdown || 0) * 100).toFixed(1)}%</div></div>
                    <div><span className="text-muted-foreground">胜率</span><div className="font-mono font-medium">{(Number(backtestResult.win_rate || 0) * 100).toFixed(0)}%</div></div>
                    <div><span className="text-muted-foreground">盈亏比</span><div className="font-mono font-medium">{Number(backtestResult.profit_factor || 0).toFixed(2)}</div></div>
                    <div><span className="text-muted-foreground">交易数</span><div className="font-mono font-medium">{Number(backtestResult.total_trades || 0)}</div></div>
                  </div>
                  <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                    {backtestResult.equity_curve?.length >= 2 && (
                      <div>
                        <div className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground mb-1.5">资金曲线</div>
                        <CurveChart curve={backtestResult.equity_curve} height={160} />
                      </div>
                    )}
                    {backtestResult.rolling_sharpe?.length >= 2 && (
                      <div>
                        <div className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground mb-1.5">滚动夏普</div>
                        <CurveChart curve={backtestResult.rolling_sharpe} height={160} ariaLabel="滚动夏普" />
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base flex items-center gap-2"><Archive className="h-4 w-4 text-teal-500" />产物管理</CardTitle>
                <div className="flex gap-2">
                  <Button onClick={fetchArtifacts} variant="ghost" size="sm" className="h-7 gap-1 text-xs"><RefreshCw className="h-3 w-3" />刷新</Button>
                  <Button onClick={handleExportArtifacts} variant="outline" size="sm" className="h-7 gap-1 text-xs"><Download className="h-3 w-3" />导出 zip</Button>
                  <label className="inline-flex cursor-pointer items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted">
                    <Upload className="h-3 w-3" />导入产物
                    <input
                      type="file"
                      accept=".zip,.pt,.json"
                      className="hidden"
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        if (file) handleImportArtifacts(file)
                        e.target.value = ""
                      }}
                    />
                  </label>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {artifactsError && <div className="text-xs text-red-500 mb-2">{artifactsError}</div>}
              {artifactsMsg && <div className="text-xs text-green-600 mb-2">{artifactsMsg}</div>}
              {artifactsLoading ? (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" />读取中...</div>
              ) : artifacts.length === 0 ? (
                <div className="text-center py-4 text-muted-foreground text-sm">暂无产物。训练结束后会生成公式与训练历史。</div>
              ) : (
                <div className="space-y-1.5">
                  {artifacts.map((a) => (
                    <div key={a.name} className="flex items-center justify-between py-1 text-xs">
                      <span className="font-mono truncate">{a.name}</span>
                      <span className="flex items-center gap-2 text-muted-foreground shrink-0">
                        <Badge
                          variant={a.kind === "history" ? "secondary" : "outline"}
                          className={a.kind === "checkpoint" ? "text-[10px] border-purple-500/50 text-purple-500" : "text-[10px]"}
                        >
                          {a.kind === "history" ? "历史" : a.kind === "checkpoint" ? "检查点" : "公式"}
                        </Badge>
                        <span>{(a.size / 1024).toFixed(1)} KB</span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2"><Clock className="h-4 w-4 text-amber-500" />可恢复会话</CardTitle>
            </CardHeader>
            <CardContent>
              {resumeError && <div className="text-xs text-red-500 mb-2">{resumeError}</div>}
              {resumableSessions.length === 0 ? (
                <div className="text-center py-6 text-muted-foreground text-sm">
                  暂无已停止/失败的会话。训练中途停止后可从此处继续。
                </div>
              ) : (
                <div className="space-y-2">
                  {resumableSessions.map((s) => (
                    <div key={s.session_id} className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/50 transition-colors">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <Badge variant={s.status === "failed" ? "destructive" : "outline"} className="text-xs">
                            {s.status === "failed" ? "失败" : "已停止"}
                          </Badge>
                          <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono truncate max-w-xs">{s.session_id}</code>
                        </div>
                        <div className="text-xs text-muted-foreground mt-1">
                          步数 {s.step}/{s.train_steps ?? "?"} | 最佳分数: {s.best_score?.toFixed(2) ?? "N/A"}
                        </div>
                      </div>
                      <Button onClick={() => handleResume(s.session_id)} size="sm" className="h-7 text-xs gap-1 ml-2" disabled={resumingId === s.session_id}>
                        {resumingId === s.session_id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}继续训练
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2"><Brain className="h-4 w-4 text-purple-500" />已发现的公式</CardTitle>
            </CardHeader>
            <CardContent>
              {formulas.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">暂无发现的公式。开始挖矿以发现 alpha 因子。</div>
              ) : (
                <div className="space-y-2">
                  {formulas.map((f) => (
                    <div key={f.formula_id} className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/50 transition-colors">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono truncate max-w-md">{f.formula_readable}</code>
                          <Badge variant="secondary" className="text-xs">分数: {f.best_score?.toFixed(2) ?? "N/A"}</Badge>
                        </div>
                        <div className="text-xs text-muted-foreground mt-1">{f.formula_tokens.length} tokens | {new Date(f.created_at * 1000).toLocaleString()}</div>
                        {formulaDeployStatus?.[f.formula_id] && (
                          <div className={`text-xs mt-1 ${formulaDeployStatus[f.formula_id].ok ? "text-green-600" : "text-red-500"}`}>
                            {formulaDeployStatus[f.formula_id].msg}
                          </div>
                        )}
                      </div>
                      <div className="flex gap-1.5 ml-2">
                        <Button onClick={() => openFormulaDetail(f.formula_id)} variant="outline" size="sm" className="h-7 text-xs gap-1"><Info className="h-3 w-3" />详情</Button>
                        <Button onClick={() => runFormulaBacktestInline(f.formula_id)} variant="outline" size="sm" className="h-7 text-xs gap-1"><TrendingUp className="h-3 w-3" />回测</Button>
                        <Button onClick={() => deployFormula(f.formula_id)} size="sm" className="h-7 text-xs gap-1"><Zap className="h-3 w-3" />部署</Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </WbPane>

      <WbPane active={active} index={1} testId="pane-validate">
        <ValidationPane
          symbol={dataSource.symbol}
          interval={dataSource.interval}
          exchange={dataSource.exchange}
          marketType={dataSource.market_type}
        />
      </WbPane>

      <WbPane active={active} index={2} testId="pane-backtest">
        <BacktestPane
          formulas={formulas}
          strategies={savedStrategies}
          symbol={dataSource.symbol}
          interval={dataSource.interval}
          exchange={dataSource.exchange}
          marketType={dataSource.market_type}
          onDeployStrategy={onDeployStrategy}
        />
      </WbPane>

      <WbPane active={active} index={3} testId="pane-monitor">
        <MonitorPane />
      </WbPane>

      <WbPane active={active} index={4} testId="pane-governance">
        <GovernancePane
          formulas={formulas}
          deployLog={deployLog}
          onDeployFormula={deployFormula}
          onGoValidation={goValidation}
        />
      </WbPane>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Info className="h-4 w-4 text-blue-500" />
              公式详情
            </DialogTitle>
            <DialogDescription>
              {detailFormula?.formula_id ?? "加载中..."}
            </DialogDescription>
          </DialogHeader>
          {detailLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : detailFormula ? (
            <div className="space-y-3">
              <div>
                <div className="text-xs text-muted-foreground mb-1">公式表达式</div>
                <code className="block text-xs bg-muted px-2 py-1.5 rounded font-mono break-all">
                  {detailFormula.formula_readable}
                </code>
              </div>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <div className="text-muted-foreground">Best Score</div>
                  <div className="font-mono font-medium">
                    {detailFormula.best_score?.toFixed(4) ?? "N/A"}
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">Tokens 数</div>
                  <div className="font-mono font-medium">{detailFormula.formula_tokens.length}</div>
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Tokens</div>
                <code className="block text-[10px] bg-muted px-2 py-1.5 rounded font-mono break-all max-h-32 overflow-auto">
                  [{detailFormula.formula_tokens.join(", ")}]
                </code>
              </div>
              {detailFormula.training_history && Object.keys(detailFormula.training_history).length > 0 && (
                <div>
                  <div className="text-xs text-muted-foreground mb-1">训练历史</div>
                  <pre className="text-[10px] bg-muted px-2 py-1.5 rounded font-mono max-h-48 overflow-auto">
                    {JSON.stringify(detailFormula.training_history, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground text-sm">加载失败</div>
          )}
        </DialogContent>
      </Dialog>
      </div>
    </div>
  )
}
