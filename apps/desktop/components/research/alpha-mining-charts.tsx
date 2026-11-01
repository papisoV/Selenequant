"use client"

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react"
import { ApiError } from "@/lib/api-client"
import {
  getIcDecay,
  type IcDecayData,
  type IcDecayParams,
  type TrainingHistory,
} from "@/lib/api/alpha"
import { useUiText } from "@/components/i18n/use-ui-text"

const VW = 400
const VH = 55
const SPARK_VW = 400
const SPARK_VH = 20
const SPARK_PAD_X = 3
const SPARK_PAD_Y = 1.5
const IC_VH = 90
const GOLD = "#e3b650"
const BLUE = "#309cfb"
const GRID = "#1d232a"
const AXIS_LEFT = 30

// ============================== 纯几何(导出供测试锁行为) ==============================

/** 优势带闭合:best 正向 + avg 反向,L..Z 围出多边形(两条 x 单调线)。 */
export function buildAdvBand(
  bestPts: Array<[number, number]>,
  avgPts: Array<[number, number]>,
): string {
  if (bestPts.length < 2 || avgPts.length < 2) return ""
  const fmt = ([x, y]: [number, number]) => `${x.toFixed(2)},${y.toFixed(2)}`
  const fwd = bestPts.map(fmt).join(" L")
  const rev = [...avgPts].reverse().map(fmt).join(" L")
  return `M${fwd} L${rev} Z`
}

/** LoRD 阶梯 path:仅 H/V 指令(不假设采样等距),值保持到下一采样。 */
export function buildStaircasePath(pts: Array<[number, number]>): string {
  if (pts.length === 0) return ""
  let d = `M${pts[0][0].toFixed(2)},${pts[0][1].toFixed(2)}`
  for (let i = 1; i < pts.length; i++) {
    const [px] = pts[i - 1]
    const [x, y] = pts[i]
    if (x !== px) d += ` H${x.toFixed(2)}`
    d += ` V${y.toFixed(2)}`
  }
  return d
}

/**
 * rank 对齐对:优先 rank_step zip stable_rank(US-001 契约:zip 即 [(step, rank)]);
 * 旧 checkpoint 无 rank_step 时退化为"第 k 个样本 = step k*100"
 * (engine `for step in pbar` 从 0 起,step % 100 == 0 时 append)。
 */
export function rankPairs(history: TrainingHistory): Array<[number, number | null]> {
  const ranks = history.stable_rank ?? []
  if (history.rank_step && history.rank_step.length === ranks.length) {
    return history.rank_step.map((s, i) => [s, ranks[i]] as [number, number | null])
  }
  return ranks.map((r, i) => [i * 100, r] as [number, number | null])
}

// ============================== scale 补偿 ==============================

/**
 * 量 svg 渲染宽 / viewBox 宽。jsdom getBoundingClientRect 为 0 → 回退 1
 * (否则 r = 4/0 = NaN)。禁写死 2.655(那是某一稿的偶然值)。
 */
function useSvgScale(viewWidth: number): [RefObject<SVGSVGElement | null>, number] {
  const ref = useRef<SVGSVGElement>(null)
  const [scale, setScale] = useState(1)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      const w = el.getBoundingClientRect().width
      setScale(w > 0 ? w / viewWidth : 1)
    }
    measure()
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null
    ro?.observe(el)
    window.addEventListener("resize", measure)
    return () => {
      ro?.disconnect()
      window.removeEventListener("resize", measure)
    }
  }, [viewWidth])
  return [ref, scale]
}
export { useSvgScale }

const fmt3 = (v: number) => v.toFixed(3)
const fmt1 = (v: number) => v.toFixed(1)
const fmtTick = (v: number) => (Math.abs(v) >= 1000 ? String(Math.round(v)) : v.toFixed(2))

// ============================== 主图:优势带训练曲线 ==============================

function TrainAdvPlot({ history }: { history: TrainingHistory }) {
  const tr = useUiText()

  const n = history.step.length
  const wrapRef = useRef<HTMLDivElement>(null)
  const [svgRef, scale] = useSvgScale(VW)
  const [hover, setHover] = useState<{ idx: number; px: number; py: number; flip: boolean } | null>(null)

  const geom = useMemo(() => {
    const best = history.best_score
    const avg = history.avg_reward
    const vals = [...best, ...avg].filter((v): v is number => v != null && Number.isFinite(v))
    const lo = vals.length > 0 ? Math.min(...vals) : 0
    const hi = vals.length > 0 ? Math.max(...vals) : 1
    const pad = (hi - lo) * 0.15 || 0.1
    const dLo = lo - pad
    const dHi = hi + pad
    const y = (v: number) => ((dHi - v) / (dHi - dLo)) * VH
    const invY = (py: number) => dHi - (py / VH) * (dHi - dLo)
    const x = (i: number) => (n > 1 ? (i / (n - 1)) * VW : VW / 2)
    // best_score 允许 null(引擎尚未发现首个因子):折线只画非空点,不造数
    const bestPts: Array<[number, number]> = []
    best.forEach((v, i) => {
      if (v != null) bestPts.push([x(i), y(v)])
    })
    const avgPts = avg.map((v, i) => [x(i), y(v)] as [number, number])
    return { y, invY, x, bestPts, avgPts }
  }, [history, n])

  // 轮询 tick 后隐藏 crosshair/tooltip(不跨 tick 残留)
  useEffect(() => {
    setHover(null)
  }, [history])

  const handleMove = (e: { clientX: number; clientY: number }) => {
    const svg = svgRef.current
    const wrap = wrapRef.current
    if (!svg || !wrap || n < 2) return
    const rect = svg.getBoundingClientRect()
    const wrapRect = wrap.getBoundingClientRect()
    if (rect.width <= 0) return
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    const idx = Math.round(frac * (n - 1))
    const svgX = geom.x(idx)
    const px = rect.left - wrapRect.left + (svgX / VW) * rect.width
    const py = Math.min(Math.max(e.clientY - wrapRect.top, 4), Math.max(wrapRect.height - 56, 4))
    setHover({ idx, px, py, flip: px + 128 > wrap.clientWidth })
  }

  // live-read:三读数从线端几何反解(与渲染共用同一 scaleY,单一事实源)
  const lastIdx = n - 1
  const hasBest = geom.bestPts.length > 0
  const hasAvg = geom.avgPts.length > 0
  const bestRead = hasBest ? geom.invY(geom.bestPts[geom.bestPts.length - 1][1]) : NaN
  const avgRead = hasAvg ? geom.invY(geom.avgPts[lastIdx][1]) : NaN
  const gapRead = bestRead - avgRead
  const gapStr = hasBest && hasAvg ? `${gapRead >= 0 ? "+" : ""}${fmt3(gapRead)}` : "—"

  const band = buildAdvBand(geom.bestPts, geom.avgPts)
  const ptsStr = (pts: Array<[number, number]>) =>
    pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ")
  const firstBest = history.best_score.find((v) => v != null) ?? null
  const refY = firstBest != null ? geom.y(firstBest) : null
  const nbIdx = [] as number[]
  for (let i = 1; i < n; i++) {
    if (history.best_score[i] != null && history.best_score[i]! > history.best_score[i - 1]!) nbIdx.push(i)
  }
  const r = 4 / scale
  const yTicks = [0.25, 0.5, 0.75].map((f) => {
    const py = VH * f
    return { py, label: fmtTick(geom.invY(py)) }
  })
  const xTicks = [0, 1 / 3, 2 / 3, 1].map((f) => Math.round(f * (n - 1)))
  const uniqX = [...new Set(xTicks)]

  return (
    <>
      <div className="ch-head">
        <span className="ch-title">{tr("优势带 · 训练曲线")}</span>
        <span className="ch-note font-mono">step {n}</span>
        <div className="live-read" aria-hidden="false">
          <span className="lr"><span className="key" style={{ background: GOLD }} />best_score <span className="num">{hasBest ? fmt3(bestRead) : "—"}</span></span>
          <span className="lr"><span className="key" style={{ background: BLUE }} />avg_reward <span className="num">{hasAvg ? fmt3(avgRead) : "—"}</span></span>
          <span className="lr"><span className="key" style={{ background: BLUE, opacity: 0.45 }} />gap <span className="num">{gapStr}</span></span>
        </div>
      </div>
      <p className="ch-sub">{tr("两线之间的填充 = 优势区(best − avg),即引擎里 adv=(r−r̄)/σ 的方向;带越宽 = 当前批次均分离最好一次越远")}</p>
      <div className="plot-wrap" ref={wrapRef} onPointerMove={handleMove} onPointerLeave={() => setHover(null)}>
        <div className="y-axis">
          {yTicks.map((t) => (
            <span key={t.py} className="yt" style={{ top: `${(t.py / VH) * 100}%` }}>{t.label}</span>
          ))}
        </div>
        {refY != null && (
          <span className="ann" style={{ left: `${AXIS_LEFT + 2}px`, top: `${(refY / VH) * 100}%` }}>{tr("起始水位")} {fmt3(firstBest!)} →</span>
        )}
        <svg ref={svgRef} data-chart="train-chart" viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="none" role="img" aria-label={tr("训练优势带曲线")}>
          <defs>
            <linearGradient id="g-adv" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={GOLD} stopOpacity="0.20" />
              <stop offset="1" stopColor={BLUE} stopOpacity="0.08" />
            </linearGradient>
          </defs>
          {yTicks.map((t) => (
            <line key={t.py} x1="0" x2={VW} y1={t.py} y2={t.py} stroke={GRID} strokeWidth="1" vectorEffect="non-scaling-stroke" />
          ))}
          {refY != null && (
            <line x1="0" x2={VW} y1={refY} y2={refY} stroke="#494d51" strokeWidth="1" strokeDasharray="4 3" opacity="0.6" vectorEffect="non-scaling-stroke" />
          )}
          <path className="adv-band" d={band} pointerEvents="none" />
          <g stroke={GOLD} strokeWidth="1" opacity="0.6" pointerEvents="none">
            {nbIdx.map((i) => (
              <line key={i} x1={geom.x(i)} x2={geom.x(i)} y1="2.5" y2="6.5" vectorEffect="non-scaling-stroke" />
            ))}
          </g>
          <polyline points={ptsStr(geom.avgPts)} fill="none" stroke={BLUE} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          <polyline points={ptsStr(geom.bestPts)} fill="none" stroke={GOLD} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          <circle data-dot="avg" cx={geom.avgPts[lastIdx][0]} cy={geom.avgPts[lastIdx][1]} r={r} fill={BLUE} stroke="#0f1419" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          {hasBest && (
            <circle data-dot="best" cx={geom.bestPts[geom.bestPts.length - 1][0]} cy={geom.bestPts[geom.bestPts.length - 1][1]} r={r} fill={GOLD} stroke="#0f1419" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          )}
        </svg>
        {hover && (
          <>
            <div className="crosshair" style={{ display: "block", left: `${hover.px}px` }} />
            <div
              className="plot-tip"
              style={{ display: "block", left: `${hover.flip ? hover.px - 128 : hover.px + 8}px`, top: `${hover.py}px` }}
            >
              <div className="tr"><span className="lbl">step {history.step[hover.idx]}</span></div>
              <div className="tr"><span className="key" style={{ background: GOLD }} /><span className="lbl">best_score</span><span className="val">{history.best_score[hover.idx] != null ? fmt3(history.best_score[hover.idx]!) : "—"}</span></div>
              <div className="tr"><span className="key" style={{ background: BLUE }} /><span className="lbl">avg_reward</span><span className="val">{fmt3(history.avg_reward[hover.idx])}</span></div>
            </div>
          </>
        )}
      </div>
      <div className="x-axis" aria-hidden="true">
        {uniqX.map((i) => (
          <span key={i} className="xt">{i === 0 ? `step ${history.step[i]}` : String(Math.round(history.step[i]))}</span>
        ))}
      </div>
    </>
  )
}

// ============================== LoRD 有效秩 sparkline ==============================

function LordRankSpark({ history }: { history: TrainingHistory }) {
  const tr = useUiText()

  const [svgRef, scale] = useSvgScale(SPARK_VW)
  const pairs = useMemo(
    () => rankPairs(history).filter((p): p is [number, number] => p[1] != null),
    [history],
  )
  if (pairs.length === 0) return null
  const sMin = Math.min(...pairs.map(([s]) => s))
  const sMax = Math.max(...pairs.map(([s]) => s))
  const vMin = Math.min(...pairs.map(([, v]) => v))
  const vMax = Math.max(...pairs.map(([, v]) => v))
  const sx = (s: number) => (sMax > sMin ? SPARK_PAD_X + ((s - sMin) / (sMax - sMin)) * (SPARK_VW - 2 * SPARK_PAD_X) : SPARK_PAD_X)
  const sy = (v: number) => (vMax > vMin ? SPARK_PAD_Y + ((vMax - v) / (vMax - vMin)) * (SPARK_VH - 2 * SPARK_PAD_Y) : SPARK_VH / 2)
  const pts = pairs.map(([s, v]) => [sx(s), sy(v)] as [number, number])
  const d = buildStaircasePath(pts)
  const r = 4 / scale
  const first = pairs[0][1]
  const last = pairs[pairs.length - 1][1]

  return (
    <>
      <div className="ch-head split tight">
        <span className="ch-title">{tr("LoRD 有效秩 ‖S‖²/S₁²")}</span>
        <span className="ch-note">{tr("每 100 步采样,阶梯保持到下一采样 · 秩下降 = 低秩正则压模型几何 ·")} <span className="num">{fmt1(first)} → {fmt1(last)}</span></span>
      </div>
      <div className="spark-wrap">
        <svg ref={svgRef} data-chart="rank-chart" viewBox={`0 0 ${SPARK_VW} ${SPARK_VH}`} preserveAspectRatio="none" role="img" aria-label={tr("LoRD 有效秩阶梯")}>
          <path d={d} fill="none" stroke={BLUE} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          <g fill={BLUE}>
            <circle cx={pts[0][0]} cy={pts[0][1]} r={r} />
            <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r={r} />
          </g>
        </svg>
      </div>
    </>
  )
}

// ============================== 卡片组装 ==============================

export function TrainingChartCard({ history, useLord }: { history: TrainingHistory; useLord: boolean }) {
  return (
    <div className="chart-card">
      <TrainAdvPlot history={history} />
      {useLord && <LordRankSpark history={history} />}
    </div>
  )
}

// ============================== IC 衰减(验证 tab 图表卡) ==============================

/** lag→IC 排序对(数值键排序,lag/IC 都须有限,否则点在图上无意义)。 */
export function sortedIcPairs(icByLag: Record<string, number>): Array<[number, number]> {
  return Object.entries(icByLag)
    .map(([lag, v]): [number, number] => [Number(lag), Number(v)])
    .filter(([lag, v]) => Number.isFinite(lag) && Number.isFinite(v))
    .sort((a, b) => a[0] - b[0])
}

function IcDecayPlot({ data }: { data: IcDecayData }) {
  const tr = useUiText()

  const wrapRef = useRef<HTMLDivElement>(null)
  const [svgRef, scale] = useSvgScale(VW)
  const [hover, setHover] = useState<{ idx: number; px: number; py: number; flip: boolean } | null>(null)

  const pts = useMemo(() => sortedIcPairs(data.ic_by_lag), [data.ic_by_lag])
  const geom = useMemo(() => {
    const xMax = Math.max(...pts.map(([l]) => l), 1)
    const vals = pts.map(([, v]) => v)
    const lo = Math.min(0, ...vals)
    const hi = Math.max(0, ...vals)
    const pad = hi - lo > 0 ? (hi - lo) * 0.15 : 0.02
    const dLo = lo - pad
    const dHi = hi + pad
    return {
      xMax,
      y: (v: number) => ((dHi - v) / (dHi - dLo)) * IC_VH,
      invY: (py: number) => dHi - (py / IC_VH) * (dHi - dLo),
      x: (lag: number) => (lag / xMax) * VW,
    }
  }, [pts])

  useEffect(() => setHover(null), [data])

  const handleMove = (e: { clientX: number; clientY: number }) => {
    const svg = svgRef.current
    const wrap = wrapRef.current
    if (!svg || !wrap || pts.length < 2) return
    const rect = svg.getBoundingClientRect()
    const wrapRect = wrap.getBoundingClientRect()
    if (rect.width <= 0) return
    const mouseX = e.clientX - rect.left
    let idx = 0
    let best = Infinity
    for (let i = 0; i < pts.length; i++) {
      const d = Math.abs((pts[i][0] / geom.xMax) * rect.width - mouseX)
      if (d < best) {
        best = d
        idx = i
      }
    }
    const px = rect.left - wrapRect.left + (pts[idx][0] / geom.xMax) * rect.width
    const py = Math.min(Math.max(e.clientY - wrapRect.top, 4), Math.max(wrapRect.height - 56, 4))
    setHover({ idx, px, py, flip: px + 128 > wrap.clientWidth })
  }

  const r = 4 / scale
  const linePts = pts.map(([l, v]) => `${geom.x(l).toFixed(2)},${geom.y(v).toFixed(2)}`).join(" ")
  const yTicks = [0.25, 0.5, 0.75].map((f) => {
    const py = IC_VH * f
    return { py, label: fmtTick(geom.invY(py)) }
  })
  const halfLag = data.half_life != null ? Math.min(data.half_life, geom.xMax) : null
  const xHalf = halfLag != null ? geom.x(halfLag) : null
  const peakPt =
    data.peak_lag != null && data.peak_ic != null && Number.isFinite(data.peak_ic)
      ? { px: geom.x(data.peak_lag), py: geom.y(data.peak_ic) }
      : null
  const halfNote =
    data.half_life == null ? tr("半衰期超出观测窗(未衰减)") : tr("半衰期 {n} bar", { n: Math.round(halfLag!) })
  const srcNote = data.source === "real" ? tr("真实 kline") : tr("合成数据")

  return (
    <>
      <div className="ch-head">
        <span className="ch-title">{tr("IC 衰减")} {data.formula_readable ? `· ${data.formula_readable}` : ""}</span>
        <span className="ch-note">
          {srcNote} · {data.symbol} {data.interval} {tr("· 峰值 IC")}{" "}
          {peakPt ? fmtTick(data.peak_ic!) : "—"} @ lag {data.peak_lag ?? "0"} · {halfNote}
          {data.turnover_adjusted_ic != null
            ? ` · turnover-adj ${fmtTick(data.turnover_adjusted_ic)}`
            : ""}
        </span>
      </div>
      <p className="ch-sub">{tr("滞后 N 根 K 线后，公式得分对前向收益的秩相关还剩下多少；金虚线 = 半衰期语义参考线")}</p>
      <div
        className="plot-wrap"
        ref={wrapRef}
        onPointerMove={handleMove}
        onPointerLeave={() => setHover(null)}
      >
        <div className="y-axis">
          {yTicks.map((t) => (
            <span key={t.py} className="yt" style={{ top: `${(t.py / IC_VH) * 100}%` }}>
              {t.label}
            </span>
          ))}
        </div>
        {/* 仅语义虚线 = 半衰期参考线;网格线一律实线 */}
        <svg ref={svgRef} data-chart="ic-decay" viewBox={`0 0 ${VW} ${IC_VH}`} preserveAspectRatio="none" role="img" aria-label={tr("IC 衰减曲线")}>
          {yTicks.map((t) => (
            <line key={t.py} x1="0" x2={VW} y1={t.py} y2={t.py} stroke={GRID} strokeWidth="1" vectorEffect="non-scaling-stroke" />
          ))}
          <polyline
            points={linePts}
            fill="none"
            stroke={BLUE}
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          {peakPt && (
            <circle
              data-dot="peak"
              cx={peakPt.px}
              cy={peakPt.py}
              r={r}
              fill={GOLD}
              stroke="#0f1419"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {xHalf != null && (
            <>
              <line
                data-line="half-life"
                x1={xHalf}
                x2={xHalf}
                y1="0"
                y2={IC_VH}
                stroke={GOLD}
                strokeWidth="1"
                strokeDasharray="5 4"
                opacity="0.9"
                vectorEffect="non-scaling-stroke"
              />
              <span className="ann" style={{ left: `${xHalf + 4}px`, top: "0%" }}>
                {halfNote} →
              </span>
            </>
          )}
        </svg>
        {hover && (
          <>
            <div className="crosshair" style={{ display: "block", left: `${hover.px}px` }} />
            <div
              className="plot-tip"
              style={{ display: "block", left: `${hover.flip ? hover.px - 128 : hover.px + 8}px`, top: `${hover.py}px` }}
            >
              <div className="tr">
                <span className="lbl">lag {pts[hover.idx][0]} bar</span>
              </div>
              <div className="tr">
                <span className="key" style={{ background: BLUE }} />
                <span className="lbl">IC</span>
                <span className="val">{fmtTick(pts[hover.idx][1])}</span>
              </div>
              {halfLag != null && pts[hover.idx][0] === Math.round(halfLag) && (
                <div className="tr">
                  <span className="key" style={{ background: GOLD }} />
                  <span className="lbl">{tr("半衰期参考线")}</span>
                </div>
              )}
            </div>
          </>
        )}
      </div>
      <div className="x-axis" aria-hidden="true">
        <span className="xt">lag 0</span>
        <span className="xt">lag {Math.round(geom.xMax / 2)}</span>
        <span className="xt">lag {Math.round(geom.xMax)}</span>
      </div>
    </>
  )
}

/** IC 衰减图表卡:消费 GET /formulas/{id}/ic-decay;404/422 语义显形,不假造。 */
export function IcDecayChartCard({
  formulaId,
  params,
}: {
  formulaId: string
  params?: IcDecayParams
}) {
  const tr = useUiText()

  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "data"; data: IcDecayData }
    | { kind: "error"; status: number; message: string }
  >({ kind: "loading" })

  const load = useCallback(async () => {
    setState({ kind: "loading" })
    try {
      const data = await getIcDecay(formulaId, params)
      setState({ kind: "data", data })
    } catch (e) {
      setState({
        kind: "error",
        status: e instanceof ApiError ? e.status : 0,
        message: e instanceof Error ? e.message : String(e),
      })
    }
  }, [formulaId, params])

  useEffect(() => {
    void load()
  }, [load])

  if (state.kind === "loading") {
    return (
      <div className="chart-card">
        <div className="ch-head">
          <span className="ch-title">{tr("IC 衰减")}</span>
          <span className="ch-note">{tr("计算中…")}</span>
        </div>
        <div className="vp-state">{tr("正在计算:拉取 K 线上公式得分对前向收益的滞后相关")}</div>
      </div>
    )
  }

  if (state.kind === "error") {
    return (
      <div className="chart-card">
        <div className="ch-head">
          <span className="ch-title">{tr("IC 衰减")}</span>
          <span className="ch-note">{tr("不可用")}</span>
        </div>
        <div className={state.status === 422 ? "vp-state warn" : "vp-state err"}>{state.message}</div>
      </div>
    )
  }

  return (
    <div className="chart-card">
      <IcDecayPlot data={state.data} />
    </div>
  )
}
