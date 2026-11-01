"use client"

import { useMemo, useState } from "react"

import { useSvgScale } from "./alpha-mining-charts"
import type {
  BacktestDetail,
  FormulaBacktestParams,
  MinedFormulaBacktest,
  MinedFormulaInfo,
  SavedStrategy,
  StrategyBacktestParams,
} from "@/lib/api/alpha"
import { backtestMinedFormula, backtestSavedStrategy, deploySavedStrategy } from "@/lib/api/alpha"

import { Button } from "@/components/ui/button"
import { Loader2, TrendingUp, Play } from "lucide-react"
import { useUiText } from "@/components/i18n/use-ui-text"

// ---------------------------------------------------------------------------
// 纯几何/聚合助手(导出供 vitest 直接断言)
// ---------------------------------------------------------------------------

/** 回撤区间:[start, end] 开区间对(equity 低于当前峰值且未回到峰值的连续区间)。 */
export function drawdownRuns(equity: number[]): Array<[number, number]> {
  const runs: Array<[number, number]> = []
  let peak = -Infinity
  let start: number | null = null
  for (let i = 0; i < equity.length; i++) {
    const v = equity[i]
    if (v >= peak) {
      // 创新高 = 回撤结束(含起点处 buffer)
      peak = v
      if (start !== null) {
        runs.push([start, i])
        start = null
      }
    } else if (start === null) {
      start = i
    }
  }
  if (start !== null) runs.push([start, equity.length - 1])
  return runs
}

export interface SignSeg {
  sign: 1 | -1 | 0
  d: string
}

/** 滚动夏普按符号切段,负值染 #d94f4f 负向色(y=基线)。 */
export function rollingSignSegments(
  series: number[],
  x: (i: number) => number,
  y: (v: number) => number,
  baseY: number,
): SignSeg[] {
  const segs: SignSeg[] = []
  for (let i = 0; i < series.length; i++) {
    const sign: 1 | -1 | 0 = series[i] > 0 ? 1 : series[i] < 0 ? -1 : 0
    const nx = i === 0 ? x(0) : x(i - 1) // 起点夹在上一段(2px 线连续,不画 0 宽段)
    const cx = x(i)
    const ny = y(series[i])
    // 每采样点自成一折段:从上一 x 的基线拉到当前点,按符号切段染色
    segs.push({ sign, d: `M ${nx.toFixed(2)} ${baseY.toFixed(2)} L ${cx.toFixed(2)} ${ny.toFixed(2)}` })
  }
  return segs.length ? segs : []
}

export interface PnlBin {
  lo: number
  hi: number
  count: number
}

/** 交易 pnl 直方图(策略源信号分布)。 */
export function buildPnlBins(pnls: number[], nBins = 9): PnlBin[] {
  if (!pnls.length) return []
  const lo = Math.min(...pnls)
  const hi = Math.max(...pnls)
  if (hi - lo < 1e-12) {
    return [{ lo: lo - 0.5, hi: hi + 0.5, count: pnls.length }]
  }
  const w = (hi - lo) / nBins
  const bins: PnlBin[] = Array.from({ length: nBins }, (_, i) => ({
    lo: lo + i * w,
    hi: lo + (i + 1) * w,
    count: 0,
  }))
  for (const p of pnls) {
    let b = Math.floor((p - lo) / w)
    if (b >= nBins) b = nBins - 1
    if (b < 0) b = 0
    bins[b].count += 1
  }
  return bins
}

export interface SigBar {
  label: string
  count: number
  pct: number
}

/** 公式源信号分布(value_counts dict)按 count 降序投影。 */
export function signalCountBars(signal_counts: Record<string, number> | null | undefined): SigBar[] {
  if (!signal_counts) return []
  const entries = Object.entries(signal_counts)
  const total = entries.reduce((s, e) => s + (e[1] || 0), 0) || 1
  return entries
    .map(([label, count]) => ({ label, count, pct: (count / total) * 100 }))
    .sort((a, b) => b.count - a.count)
}

// ---------------------------------------------------------------------------
// 呈现常量
// ---------------------------------------------------------------------------

const EQ_GOLD = "#e3b650"
const POS = "#4fbf7a"
const NEG = "#d94f4f"
const GRID = "#1d232a"
const LINE = "#494d51"
const EQ_VW = 440
const EQ_VH = 130
const RS_VW = 440
const RS_VH = 64
const AXIS_LEFT = 34
const RS_BASE_Y = RS_VH - 10

const METRICS = [
  { key: "total_return", label: "总收益", fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
  { key: "annual_return", label: "年化收益", fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
  { key: "sharpe_ratio", label: "夏普", fmt: (v: number) => v.toFixed(2) },
  { key: "sortino_ratio", label: "Sortino", fmt: (v: number) => v.toFixed(2) },
  { key: "max_drawdown", label: "最大回撤", fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
  { key: "win_rate", label: "胜率", fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
  { key: "total_trades", label: "交易数", fmt: (v: number) => String(v) },
  { key: "profit_factor", label: "盈亏比", fmt: (v: number) => v.toFixed(2) },
] as const

function metric(key: string, d: BacktestDetail) {
  return METRICS.find((m) => m.key === key)?.fmt((d as unknown as Record<string, number>)[key] ?? 0) ?? "—"
}

/** 净值曲线:g-nav 渐变填充 + 回撤染色块(峰值→谷底) + 均线。 */
function EquityCurve({ equity }: { equity: number[] }) {
  const [ref, scale] = useSvgScale(EQ_VW)
  const min = Math.min(...equity)
  const max = Math.max(...equity)
  const pad = (max - min) * 0.08 || max * 0.01 || 1
  const lo = min - pad
  const hi = max + pad
  const x = (i: number) => AXIS_LEFT + (i / Math.max(equity.length - 1, 1)) * (EQ_VW - AXIS_LEFT - 6)
  const y = (v: number) => 8 + (1 - (v - lo) / (hi - lo || 1)) * (EQ_VH - 16)
  const pts = equity.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`)
  const line = pts.join(" ")
  const area = `M ${pts[0]} L ${line.replace(/ /g, " L ")} L ${x(equity.length - 1).toFixed(2)} ${(EQ_VH - 8).toFixed(2)} Z`
  const runs = drawdownRuns(equity)
  return (
    <svg ref={ref} viewBox={`0 0 ${EQ_VW} ${EQ_VH}`} className="eq-cv" preserveAspectRatio="none" data-testid="eq-curve">
      <defs>
        <linearGradient id="g-nav" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={EQ_GOLD} stopOpacity="0.20" />
          <stop offset="100%" stopColor="#309cfb" stopOpacity="0.08" />
        </linearGradient>
      </defs>
      {[0, 1, 2, 3].map((i) => (
        <line key={i} x1={AXIS_LEFT} x2={EQ_VW - 6} y1={y(lo + ((hi - lo) * i) / 3)} y2={y(lo + ((hi - lo) * i) / 3)} stroke={GRID} strokeWidth={1} vectorEffect="non-scaling-stroke" />
      ))}
      {runs.map(([s, e], i) => (
        <rect
          key={`${s}-${e}-${i}`}
          x={x(s)}
          y={8}
          width={Math.max(x(e) - x(s), 0.5)}
          height={EQ_VH - 16}
          fill={NEG}
          opacity={0.10}
        />
      ))}
      <path d={area} fill="url(#g-nav)" />
      <polyline points={line} fill="none" stroke={EQ_GOLD} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      <circle cx={x(equity.length - 1)} cy={y(equity[equity.length - 1])} r={4 / scale} fill={EQ_GOLD} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

/** 滚动夏普:正负分段染色(#4fbf7a/#d94f4f 色盲对)+ 0 基线。 */
function RollingSharpe({ series }: { series: number[] }) {
  const [ref, scale] = useSvgScale(RS_VW)
  const vals = series.filter((v) => Number.isFinite(v))
  const max = Math.max(0, ...vals.map((v) => Math.abs(v)))
  const amp = max || 1
  const x = (i: number) => AXIS_LEFT + (i / Math.max(series.length - 1, 1)) * (RS_VW - AXIS_LEFT - 6)
  const y = (v: number) => RS_BASE_Y - (v / amp) * (RS_VH - 24)
  const segs = rollingSignSegments(series, x, y, RS_BASE_Y)
  return (
    <svg ref={ref} viewBox={`0 0 ${RS_VW} ${RS_VH}`} className="rs-cv" preserveAspectRatio="none" data-testid="rs-sharpe">
      <line x1={AXIS_LEFT} x2={RS_VW - 6} y1={RS_BASE_Y} y2={RS_BASE_Y} stroke={LINE} strokeWidth={1} vectorEffect="non-scaling-stroke" strokeDasharray="2 2" />
      {segs.map((s, i) => (
        <path key={i} d={s.d} fill="none" stroke={s.sign >= 0 ? POS : NEG} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      ))}
      <circle cx={x(series.length - 1)} cy={y(series[series.length - 1])} r={4 / scale} fill={series[series.length - 1] >= 0 ? POS : NEG} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function PnlHistogram({ pnls }: { pnls: number[] }) {
  const tr = useUiText()

  const bins = buildPnlBins(pnls)
  if (!bins.length) return <p className="bt-empty">{tr("无成交数据")}</p>
  const maxC = Math.max(...bins.map((b) => b.count))
  return (
    <div className="sig-hist">
      {bins.map((b, i) => {
        const neg = b.hi <= 0
        const pos = b.lo >= 0
        return (
          <div key={i} className={`sig-bin ${neg ? "negbar" : pos ? "" : "neut"}`} style={{ height: `${Math.max((b.count / maxC) * 100, 4)}%` }} title={`${b.lo.toFixed(2)} ~ ${b.hi.toFixed(2)}: ${b.count}`} />
        )
      })}
    </div>
  )
}

/** 回测指标卡组(全真数据,无 mock)。 */
export function MetricGrid({ d }: { d: BacktestDetail }) {
  const tr = useUiText()

  return (
    <div className="stat-grid g4 bt-metrics">
      {METRICS.map((m) => (
        <div className="metric-cell" key={m.key}>
          <span className="m-label">{tr(m.label)}</span>
          <span className="m-value tnum" data-testid={`metric-${m.key}`}>{metric(m.key, d)}</span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 回测 tab 主面板:双源(公式 lean / 已保存策略 rich)
// ---------------------------------------------------------------------------

export interface BacktestPaneProps {
  formulas: MinedFormulaInfo[]
  strategies: SavedStrategy[]
  symbol?: string
  interval?: string
  exchange?: string
  marketType?: string
  /** 已保存策略部署(模拟)成功后回调 → 治理 tab deployLog。 */
  onDeployStrategy?: (log: { target: string; ok: boolean; at: string; note?: string }) => void
}

export function BacktestPane({
  formulas,
  strategies,
  symbol = "BTC/USDT",
  interval = "15m",
  exchange = "binance",
  marketType = "futures",
  onDeployStrategy,
}: BacktestPaneProps) {
  const tr = useUiText()

  const [fx, setFx] = useState("strategy") // formula | strategy(默认策略源,8 指标富回测)
  const [fid, setFid] = useState<string>("")
  const [sname, setSname] = useState<string>("")
  const [bpi, setBpi] = useState<Partial<StrategyBacktestParams>>({})
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<MinedFormulaBacktest | BacktestDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [deploying, setDeploying] = useState<string | null>(null)

  const formulaParams: FormulaBacktestParams = useMemo(
    () => ({ symbol, exchange, market_type: marketType, interval }),
    [symbol, exchange, marketType, interval],
  )

  async function runBacktest() {
    setErr(null)
    setRunning(true)
    setResult(null)
    try {
      if (fx === "formula") {
        if (!fid) return
        setResult(await backtestMinedFormula(fid, formulaParams))
      } else {
        if (!sname) return
        setResult(await backtestSavedStrategy(sname, { symbol, interval, exchange, market_type: marketType, ...bpi }))
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  async function deployStrategy(name: string) {
    setDeploying(name)
    try {
      await deploySavedStrategy(name, { symbol, interval })
      onDeployStrategy?.({ target: name, ok: true, at: new Date().toISOString(), note: "模拟部署" })
    } catch (e) {
      onDeployStrategy?.({
        target: name,
        ok: false,
        at: new Date().toISOString(),
        note: e instanceof Error ? e.message : String(e),
      })
    } finally {
      setDeploying(null)
    }
  }

  /** 策略行区间按钮:单击即跑该区间回测(无需先选中再点"跑回测")。 */
  async function runStrategyRow(name: string, interval: string) {
    setErr(null)
    setRunning(true)
    setResult(null)
    try {
      setSname(name)
      setResult(await backtestSavedStrategy(name, { symbol, interval, exchange, market_type: marketType }))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  const formulaCount = formulas.length
  const strategyCount = strategies.length

  return (
    <section className="wb-section">
      <div className="wb-toolbar">
        <div className="wb-tool-group">
          <button type="button" className={`btt ${fx === "formula" ? "on" : ""}`} onClick={() => setFx("formula")}>
            {tr("公式回测")}
          </button>
          <button type="button" className={`btc ${fx === "strategy" ? "on" : ""}`} onClick={() => setFx("strategy")}>
            {tr("策略回测")}
          </button>
        </div>
        {fx === "formula" ? (
          <select className="wb-select" value={fid} onChange={(e) => setFid(e.target.value)} data-testid="fx-select">
            <option value="">{tr("选择已验证公式 (")}{formulaCount})</option>
            {formulas.map((f) => (
              <option key={f.formula_id} value={f.formula_id}>
                {f.formula_readable} · {f.best_score.toFixed(3)}
              </option>
            ))}
          </select>
        ) : (
          <select className="wb-select" value={sname} onChange={(e) => setSname(e.target.value)} data-testid="st-select">
            <option value="">{tr("选择已保存策略 (")}{strategyCount})</option>
            {strategies.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        )}
        <Button className="wb-btn" onClick={runBacktest} disabled={running || (fx === "formula" ? !fid : !sname)} data-testid="run-backtest">
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {tr("跑回测")}
        </Button>
      </div>

      {fx === "strategy" && (
        <div className="bt-strategy-list" data-testid="strategy-list">
          <h4 className="bt-strategy-title">{tr("已保存的 Alpha 策略")}</h4>
          {strategies.length === 0 ? (
            <p className="bt-empty">{tr("暂无已保存的策略。运行 Optuna 公式搜索脚本以生成策略。")}</p>
          ) : (
            <ul className="bt-strategy-rows">
              {strategies.map((s) => {
                const iv = (s as { profiles?: Array<{ base_interval?: string }> }).profiles?.[0]?.base_interval ?? "15m"
                return (
                  <li key={s.name} className="bt-strategy-row">
                    <span className="fname">{s.name}</span>
                    <span className="fscore">{s.formula_readable}</span>
                    <span className="bt-row-actions">
                      {[iv, "1h", "4h"].map((it) => (
                        <button
                          key={it}
                          type="button"
                          className={`btc ivbtn ${bpi.interval === it ? "on" : ""}`}
                          onClick={() => {
                            setBpi((p) => ({ ...p, interval: it }))
                            void runStrategyRow(s.name, it)
                          }}
                          data-testid={`iv-${s.name}-${it}`}
                        >
                          {it}
                        </button>
                      ))}
                      <Button className="bt-btn" size="sm" disabled={deploying === s.name} onClick={() => deployStrategy(s.name)}>
                        {deploying === s.name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TrendingUp className="h-3.5 w-3.5" />}
                        {tr("模拟部署")}
                      </Button>
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}

      {err && <p className="bt-err" data-testid="bt-error">{err}</p>}

      {result && fx === "formula" ? (
        <FormulaResult r={result as MinedFormulaBacktest} />
      ) : result && fx === "strategy" ? (
        <StrategyResult r={result as BacktestDetail} />
      ) : null}
    </section>
  )
}

function FormulaResult({ r }: { r: MinedFormulaBacktest }) {
  const tr = useUiText()

  const bars = signalCountBars(r.signal_counts)
  return (
    <div className="bt-result" data-testid="formula-result">
      <h4 className="bt-rhead">
        {tr("回测结果 ·")} {r.formula_readable}
        <span className="bs-hint">
          {r.source} · {r.symbol} · {r.interval} · {r.exchange}/{r.market_type} · {r.total_bars} {tr("根")}
        </span>
      </h4>
      <div className="bt-summary">
        <div className="metric-cell">
          <span className="m-label">{tr("最新得分")}</span>
          <span className="m-value">{r.latest_score.toFixed(3)}</span>
        </div>
        <div className="metric-cell">
          <span className="m-label">{tr("信号取值")}</span>
          <span className="m-value">{Object.keys(r.signal_counts ?? {}).length}</span>
        </div>
        <div className="metric-cell">
          <span className="m-label">{tr("数据源")}</span>
          <span className="m-value">{r.source === "real" ? tr("真实") : tr("合成")}</span>
        </div>
      </div>
      <div className="bt-block">
        <h5 className="bt-title">{tr("信号分布")}</h5>
        <div className="sig-bars">
          {bars.length === 0 ? (
            <p className="bt-empty">{tr("无信号分布数据")}</p>
          ) : (
            bars.map((b) => (
              <div key={b.label} className="sig-row">
                <span className="sig-label">{b.label}</span>
                <div className="sig-track">
                  <div className="sig-bar" style={{ width: `${b.pct}%` }} data-testid="sig-bar" />
                </div>
                <span className="sig-count tnum">{b.count}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

function StrategyResult({ r }: { r: BacktestDetail }) {
  const tr = useUiText()

  return (
    <div className="bt-result" data-testid="strategy-result">
      <h4 className="bt-rhead">
        {tr("回测结果 ·")} {r.strategy_name}
        <span className="bs-hint">
          {r.symbol} · {r.interval}
        </span>
      </h4>
      <MetricGrid d={r} />
      <div className="bt-charts">
        <div className="bt-block">
          <h5 className="bt-title">{tr("资金曲线")}</h5>
          <EquityCurve equity={r.equity_curve} />
        </div>
        <div className="bt-block">
          <h5 className="bt-title">{tr("滚动夏普")}</h5>
          {r.rolling_sharpe && r.rolling_sharpe.length ? <RollingSharpe series={r.rolling_sharpe} /> : <p className="bt-empty">{tr("无滚动数据")}</p>}
        </div>
      </div>
      <div className="bt-block">
        <h5 className="bt-title">{tr("信号分布")}</h5>
        <PnlHistogram pnls={r.trades.map((t) => t.pnl)} />
      </div>
    </div>
  )
}
