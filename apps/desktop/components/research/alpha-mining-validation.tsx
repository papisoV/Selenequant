"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { ApiError } from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import { IcDecayChartCard } from "@/components/research/alpha-mining-charts"
import {
  deployFormula,
  getValidationReport,
  listFormulas,
  type DatasetValidation,
  type FoldMetrics,
  type FormulaValidation,
  type MinedFormulaInfo,
  type ValidationReportConfig,
  type ValidationReportData,
} from "@/lib/api/alpha"

// ============================== 纯映射(导出供测试锁行为) ==============================

const DECISION_LABEL: Record<string, string> = {
  accept: "可部署",
  hold: "暂缓(可研究,不可部署)",
  reject: "禁止部署",
  defer: "样本不足,延后",
}

/** 折窗几何:训练窗统一从 0 起(扩展窗),OOS 窗逐折前推。 */
export function formatFoldWindow(f: FoldMetrics): { train: string; oos: string } {
  return {
    train: `[${f.train_start}, ${f.train_end})`,
    oos: `[${f.test_start}, ${f.test_end})`,
  }
}

/** cost_stress 排序对:按成本倍数升序;null/空 → []。 */
export function sortedCostStress(
  costStress: Record<string, number> | null | undefined,
): Array<[number, number]> {
  if (!costStress) return []
  return Object.entries(costStress)
    .map(([m, v]): [number, number] => [Number(m), Number(v)])
    .filter(([m, v]) => Number.isFinite(m) && Number.isFinite(v))
    .sort((a, b) => a[0] - b[0])
}

/**
 * T4 部署门控判定:accept / 无报告条目(None) → 放行;
 * hold/reject/defer → 禁用并给出原因(与后端 L684-695 同语义,
 * "absence of evidence is not a refusal")。
 */
export function deployBlockReason(
  decision: string | null | undefined,
  reason: string,
): string | null {
  const d = (decision ?? "").toLowerCase()
  if (!d || d === "accept") return null
  const label = DECISION_LABEL[d] ?? d
  const why = reason ? ` — ${reason}` : ""
  return `T4 门控拒绝部署 (${d}:${label})${why}`
}

/** 报告公式 → 本地产物公式的 tokens 等值联结(与后端 _lookup_t4_decision 同 join 键)。 */
export function matchFormulaByTokens(
  formula: Pick<FormulaValidation, "tokens">,
  formulas: MinedFormulaInfo[],
): MinedFormulaInfo | null {
  return formulas.find(
    (f) => f.formula_tokens.length === formula.tokens.length
      && formula.tokens.every((t, i) => f.formula_tokens[i] === t),
  ) ?? null
}

function verdictClass(v: string): string {
  if (v === "VALID") return "valid"
  if (v === "INVALID") return "invalid"
  return "susp"
}

const fmtPct = (v: number | null | undefined, digits = 1) =>
  v == null || !Number.isFinite(v) ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`
const fmtNum = (v: number | null | undefined, digits = 3) =>
  v == null || !Number.isFinite(v) ? "—" : v.toFixed(digits)

// ============================== 公式级块 ==============================

function ProvenanceStrip({
  formula,
  config,
  generatedAt,
}: {
  formula: FormulaValidation
  config: ValidationReportConfig
  generatedAt: string | null
}) {
  const fc = config.fold_config
  const geometry = fc
    ? `${fc.min_train}/${fc.fold_size} × ${fc.n_folds}`
    : "—"
  const hash = formula.canonical_hash ? formula.canonical_hash.slice(0, 8) : "—"
  const dsCount = Object.keys(formula.datasets ?? {}).length
  return (
    <div className="prov-strip">
      <span className="pv-lab">来源</span>
      <span className="prov-chip">
        <span className="pk">公式指纹</span>
        <span className="pv hash">{hash}</span>
      </span>
      <span className="prov-chip">
        <span className="pk">折几何</span>
        <span className="pv">{geometry}</span>
      </span>
      <span className="prov-chip">
        <span className="pk">数据集</span>
        <span className="pv">{dsCount} 组</span>
      </span>
      <span className="prov-chip">
        <span className="pk">生成于</span>
        <span className="pv">{generatedAt ?? "—"}</span>
      </span>
      <span className="pv-note">同指纹 = 同公式,可用于跨会话比对</span>
    </div>
  )
}

function DecisionRow({
  formula,
  config,
  formulaId,
  onDeploy,
  deployState,
}: {
  formula: FormulaValidation
  config: ValidationReportConfig
  formulaId: string | null
  onDeploy: () => void
  deployState: { ok: boolean; msg: string } | null
}) {
  const decision = (formula.decision ?? "").toLowerCase()
  const dCellClass = decision === "accept" || !decision ? "pass" : "fail"
  const block = deployBlockReason(formula.decision, formula.reason)
  const missingFormula = formulaId == null
  const disabled = block != null || missingFormula
  const title =
    block ??
    (missingFormula ? "本地产物中无 tokens 匹配的公式,无法部署" : `T4 判定 ${decision},可部署`)
  const thr = (config.thresholds?.dsr_threshold ?? 0.1) as number
  const dsrPass = formula.dsr == null ? null : formula.dsr >= thr
  const pboPass = formula.pbo == null ? null : formula.pbo <= 0.25

  return (
    <>
      <div className="decision-row">
        <div className={`d-cell ${dCellClass}`}>
          <span className="dl">
            判定 <span className="mark">{formula.verdict}</span>
          </span>
          <span className={`dv ${dCellClass === "fail" ? "warn" : ""}`}>{decision || "未判定"}</span>
          <span className="dd">{formula.reason || `decision = ${formula.decision ?? "无报告条目(放行)"}`}</span>
        </div>
        <div className={`d-cell ${dsrPass === false ? "fail" : "pass"}`}>
          <span className="dl">
            DSR <span className="mark">{dsrPass === false ? "FAIL" : "PASS"}</span>
          </span>
          <span className={`dv ${dsrPass === false ? "warn" : ""}`}>{fmtNum(formula.dsr, 2)}</span>
          <span className="dd">阈值 ≥{fmtNum(thr, 2)} · 通胀后仍显著</span>
        </div>
        <div className={`d-cell ${pboPass === false ? "fail" : "pass"}`}>
          <span className="dl">
            PBO <span className="mark">{pboPass === false ? "FAIL" : "PASS"}</span>
          </span>
          <span className={`dv ${pboPass === false ? "warn" : ""}`}>{fmtNum(formula.pbo, 2)}</span>
          <span className="dd">回测过拟合概率,越低越好</span>
        </div>
      </div>
      <div className="decision-row deploy-row">
        <Button
          size="sm"
          disabled={disabled}
          title={title}
          onClick={onDeploy}
          data-testid="deploy-formula"
        >
          {block ? "部署被 T4 门控拒绝" : "部署此公式"}
        </Button>
        <span className="dep-note">
          {block
            ? `T4 判定 ${formula.decision}(非 accept)→ 与后端 403 同语义禁用`
            : title}
        </span>
        {deployState && (
          <span className={`dep-msg ${deployState.ok ? "ok" : "err"}`}>{deployState.msg}</span>
        )}
      </div>
    </>
  )
}

function FormulaTable({
  formulas,
  selected,
  onSelect,
}: {
  formulas: FormulaValidation[]
  selected: number
  onSelect: (i: number) => void
}) {
  return (
    <table className="wt-table">
      <thead>
        <tr>
          <th>#</th>
          <th>公式</th>
          <th className="r">train IC</th>
          <th className="r">OOS 池化 IC</th>
          <th className="r">OOS Sharpe</th>
          <th className="r">DSR</th>
          <th className="r">判定</th>
        </tr>
      </thead>
      <tbody>
        {formulas.map((f, i) => {
          const ds = firstDataset(f)
          return (
            <tr
              key={f.canonical_hash ?? i}
              className={i === selected ? "sel" : undefined}
              onClick={() => onSelect(i)}
              data-testid={`formula-row-${i}`}
            >
              <td className="num">{i + 1}</td>
              <td className="num" style={{ maxWidth: 220 }}>
                {f.readable}
              </td>
              <td className="r num">{fmtNum(f.train_mean_ic)}</td>
              <td className="r num">{fmtNum(f.mean_ic)}</td>
              <td className="r num">{fmtNum(ds?.oos_ls_sharpe ?? null, 2)}</td>
              <td className="r num">{fmtNum(f.dsr, 2)}</td>
              <td className="r">
                <span className={`verdict ${verdictClass(f.verdict)}`} title={`decision=${f.decision} · ${f.reason}`}>
                  {f.verdict}
                </span>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ============================== 数据集级块 ==============================

function firstDataset(f: FormulaValidation): DatasetValidation | null {
  const keys = Object.keys(f.datasets ?? {})
  return keys.length ? f.datasets[keys[0]] : null
}

function FoldBlock({ ds }: { ds: DatasetValidation | null }) {
  if (!ds || ds.folds.length === 0) {
    return (
      <div className="fold-block">
        <div className="fb-head">
          <span className="fb-t">walk-forward 折窗</span>
          <span className="fb-n">无折明细</span>
        </div>
        <div className="gov-foot">该数据集未产出有效 walk-forward 折,无法展示逐折几何</div>
      </div>
    )
  }
  const oosIcs = ds.folds.map((f) => f.oos_rank_ic).filter((v): v is number => v != null && Number.isFinite(v))
  const mean = oosIcs.length ? oosIcs.reduce((a, b) => a + b, 0) / oosIcs.length : null
  const min = oosIcs.length ? Math.min(...oosIcs) : null
  const max = oosIcs.length ? Math.max(...oosIcs) : null
  const sumObs = ds.folds.reduce((a, f) => a + (f.oos_n_obs || 0), 0)
  return (
    <div className="fold-block">
      <div className="fb-head">
        <span className="fb-t">walk-forward 折窗</span>
        <span className="fb-n">扩展窗:训练窗只增不减,OOS 窗逐折前推</span>
        <span className="fb-fx num">{ds.effective_folds} 折 · 累计 {sumObs} obs</span>
      </div>
      <table className="fold-table">
        <thead>
          <tr>
            <th>fold</th>
            <th>训练窗</th>
            <th>OOS 窗</th>
            <th className="r">train_rank_ic</th>
            <th className="r">oos_rank_ic</th>
            <th className="r">oos_n_obs</th>
          </tr>
        </thead>
        <tbody>
          {ds.folds.map((f) => {
            const win = formatFoldWindow(f)
            const ic = f.oos_rank_ic
            return (
              <tr key={f.fold_index}>
                <td className="fi">{f.fold_index + 1}</td>
                <td className="win">{win.train}</td>
                <td className="win oos">{win.oos}</td>
                <td className="r ic train">{fmtNum(f.train_rank_ic)}</td>
                <td className={`r ic ${ic != null && ic < 0 ? "neg" : "pos"}`}>{fmtNum(ic)}</td>
                <td className="r ic">{f.oos_n_obs}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="gov-foot">
        {mean != null
          ? `OOS rank IC 均值 ${fmtNum(mean)} · 区间 [${fmtNum(min)}, ${fmtNum(max)}] — 逐折排序 IC 的形状`
          : "无有效 OOS rank IC 数据"}
      </div>
    </div>
  )
}

function SoloList({ ds }: { ds: DatasetValidation | null }) {
  const solos = useMemo(() => {
    if (!ds) return []
    return Object.values(ds.solo ?? {}).sort((a, b) => a.symbol.localeCompare(b.symbol))
  }, [ds])
  if (solos.length === 0) {
    return <div className="solo-list"><div className="solo-row"><span className="sm">无逐 symbol 分解数据</span></div></div>
  }
  return (
    <div className="solo-list">
      {solos.map((s) => {
        const downgraded = s.verdict === "SUSPICIOUS" || s.verdict === "INSUFFICIENT_DATA"
        const cls = s.verdict === "PASS" ? "valid" : s.verdict === "INVALID" ? "invalid" : "susp"
        return (
          <div key={s.symbol} className={`solo-row ${downgraded ? "stack" : ""}`}>
            <span className="ss num">{s.symbol}</span>
            <span className="sm">
              ann_ret {fmtPct(s.ann_ret)} · SR {fmtNum(s.sharpe, 2)} · DD {fmtPct(s.max_drawdown)}
            </span>
            <span className={`sv2 ${s.ann_ret != null && s.ann_ret < 0 ? "neg" : "pos"}`}>
              {fmtPct(s.ann_ret)}
            </span>
            <span className={`verdict ${cls}`} title={s.reason}>
              {s.verdict}
            </span>
            {downgraded && <span className="snote">{s.reason}</span>}
          </div>
        )
      })}
    </div>
  )
}

function CostStressList({ ds }: { ds: DatasetValidation | null }) {
  if (!ds) return <div className="stress-list"><div className="stress-row"><span className="sm">无数据</span></div></div>
  const rows = useMemo(() => {
    const stress = sortedCostStress(ds.cost_stress)
    const all: Array<[number, number]> = [[1, ds.oos_ls_sharpe ?? 0], ...stress]
    const maxAbs = Math.max(...all.map(([, v]) => Math.abs(v)), 0.001)
    return { all, stress, maxAbs }
  }, [ds])
  const enabled = rows.stress.length > 0
  const firstNeg = rows.stress.find(([, v]) => v <= 0)
  const footNote = !enabled
    ? `本次未开启成本压力档(fold config 未设 cost_stress_multipliers);基线 ${fmtNum(ds.oos_ls_sharpe, 2)} 未做费率敏感性检查`
    : firstNeg
      ? `在 ${fmtNum(firstNeg[0], 1)}× 成本下转负:换手率是这条公式最脆的一环`
      : `最高 ${fmtNum(rows.stress[rows.stress.length - 1][0], 1)}× 成本下仍保持正收益,换手不是瓶颈`
  return (
    <>
      <div className="stress-list">
        {rows.all.map(([m, v]) => {
          const width = Math.max(2, (Math.abs(v) / rows.maxAbs) * 100)
          const dead = v <= 0
          const fade = !dead && m > 1
          return (
            <div key={m} className="stress-row">
              <span className="xk num">{fmtNum(m, m % 1 === 0 ? 0 : 1)}×</span>
              <span className="xbar">
                <i style={{ width: `${width}%` }} className={dead ? "dead" : fade ? "fade" : undefined} />
              </span>
              <span className={`xv ${v < 0 ? "neg" : ""}`}>{fmtNum(v, 2)}</span>
            </div>
          )
        })}
      </div>
      <div className="gov-foot">{footNote}</div>
    </>
  )
}

function GovSplit({ ds }: { ds: DatasetValidation | null }) {
  return (
    <div className="gov-split">
      <div className="gov-box">
        <div className="gb-head">
          <span className="gb-t">逐 symbol 分解</span>
          <span className="gb-n">solo 单独跑,不是组合贡献</span>
        </div>
        <SoloList ds={ds} />
      </div>
      <div className="gov-box">
        <div className="gb-head">
          <span className="gb-t">成本压力</span>
          <span className="gb-n">费率翻倍后还剩多少</span>
        </div>
        <CostStressList ds={ds} />
      </div>
    </div>
  )
}

function DatasetDetails({ formula }: { formula: FormulaValidation }) {
  const ds = firstDataset(formula)
  const overfit = ds?.overfit
  return (
    <details className="disclosure">
      <summary>
        完整审计明细({Object.keys(formula.datasets ?? {}).length} 组数据集 · 过拟合检查 / 显著性 / 样本)
      </summary>
      <div className="disc-body">
        {ds ? (
          <div className="stat-grid g5">
            <div className="stat-cell">
              <span className="sl">数据集</span>
              <span className="sv num" style={{ fontSize: 11 }}>{Object.keys(formula.datasets)[0]}</span>
            </div>
            <div className="stat-cell">
              <span className="sl">符号数</span>
              <span className="sv num">{ds.n_symbols}</span>
              <span className="sd">本数据集</span>
            </div>
            <div className="stat-cell">
              <span className="sl">OOS Sharpe</span>
              <span className={`sv num ${ds.oos_ls_sharpe != null && ds.oos_ls_sharpe < 0 ? "neg" : ""}`}>
                {fmtNum(ds.oos_ls_sharpe, 2)}
              </span>
              <span className="sd">净费前 LS</span>
            </div>
            <div className="stat-cell">
              <span className="sl">换手</span>
              <span className="sv num">{fmtPct(ds.oos_turnover)}</span>
              <span className="sd">OOS 日均</span>
            </div>
            <div className="stat-cell">
              <span className="sl">n_oos_obs</span>
              <span className="sv num">{ds.n_oos_obs}</span>
              <span className="sd">OOS 样本</span>
            </div>
            {overfit ? (
              <>
                <div className="stat-cell">
                  <span className="sl">Observed SR</span>
                  <span className="sv num">{fmtNum(overfit.observed_sharpe_annual, 2)}</span>
                  <span className="sd">年化观测</span>
                </div>
                <div className="stat-cell">
                  <span className="sl">PSR vs 0</span>
                  <span className={`sv ${overfit.psr_vs_zero >= 0.9 ? "pos" : ""} num`}>
                    {fmtPct(overfit.psr_vs_zero, 1)}
                  </span>
                  <span className="sd">概率显著</span>
                </div>
                <div className="stat-cell">
                  <span className="sl">Haircut</span>
                  <span className={`sv ${overfit.haircut_pct > 0.2 ? "warn" : ""} num`}>
                    {fmtPct(-overfit.haircut_pct)}
                  </span>
                  <span className="sd">回撤比例</span>
                </div>
                <div className="stat-cell">
                  <span className="sl">PBO</span>
                  <span className="sv num">{fmtNum(overfit.pbo ?? formula.pbo, 2)}</span>
                  <span className="sd">过拟合概率</span>
                </div>
                <div className="stat-cell">
                  <span className="sl">n_trials</span>
                  <span className="sv num">{overfit.n_trials}</span>
                  <span className="sd">挖矿预算</span>
                </div>
              </>
            ) : null}
          </div>
        ) : (
          <span className="pv-note">该公式无数据集明细</span>
        )}
      </div>
    </details>
  )
}

// ============================== 态 ==============================

function MissingReportState({ detail, onRetry }: { detail: string; onRetry: () => void }) {
  return (
    <div className="vp" data-testid="vp-missing">
      <div className="vp-state warn">先跑验证管线:验证报告(validation_report.json)尚未生成</div>
      <div className="vp-cmd">{detail}</div>
      <div className="vp-actions">
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      </div>
    </div>
  )
}

// ============================== 主 Pane ==============================

export interface ValidationPaneProps {
  symbol?: string
  interval?: string
  exchange?: string
  marketType?: string
}

export function ValidationPane({
  symbol = "BTC/USDT",
  interval = "1h",
  exchange = "binance",
  marketType = "futures",
}: ValidationPaneProps) {
  const [report, setReport] = useState<ValidationReportData | null>(null)
  const [live, setLive] = useState<MinedFormulaInfo[]>([])
  const [state, setState] = useState<"loading" | "missing" | "error" | "empty" | "ready">("loading")
  const [err, setErr] = useState<string | null>(null)
  const [selIdx, setSelIdx] = useState(0)
  const [deployState, setDeployState] = useState<{ ok: boolean; msg: string } | null>(null)

  const load = useCallback(async () => {
    setState("loading")
    setErr(null)
    try {
      const [reportRes, formRes] = await Promise.allSettled([getValidationReport(), listFormulas()])
      if (reportRes.status === "rejected") {
        const e = reportRes.reason
        setErr(e instanceof Error ? e.message : String(e))
        setState(e instanceof ApiError && e.status === 404 ? "missing" : "error")
        return
      }
      setReport(reportRes.value.report)
      setLive(formRes.status === "fulfilled" ? formRes.value : [])
      setState(reportRes.value.report.formulas.length > 0 ? "ready" : "empty")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setState("error")
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const formula = report?.formulas[selIdx] ?? null
  const icParams = useMemo(
    () => ({ symbol, exchange, market_type: marketType, interval }),
    [symbol, exchange, marketType, interval],
  )

  const handleDeploy = useCallback(async () => {
    if (!formula) return
    const matched = matchFormulaByTokens(formula, live)
    if (!matched) return
    setDeployState(null)
    try {
      await deployFormula(matched.formula_id)
      setDeployState({ ok: true, msg: `已部署 (${matched.formula_id})` })
    } catch (e) {
      setDeployState({
        ok: false,
        msg: e instanceof Error ? e.message : String(e),
      })
    }
  }, [formula, live])

  if (state === "loading") {
    return <div className="vp" data-testid="vp-loading"><div className="vp-state">正在读取 T4 验证报告…</div></div>
  }
  if (state === "missing") {
    return <MissingReportState detail={err ?? ""} onRetry={() => void load()} />
  }
  if (state === "error") {
    return (
      <div className="vp">
        <div className="vp-state err">{err}</div>
        <div className="vp-actions">
          <Button variant="outline" size="sm" onClick={() => void load()}>重试</Button>
        </div>
      </div>
    )
  }
  if (!report || state === "empty") {
    return (
      <div className="vp">
        <div className="vp-state">验证报告已生成,但 formulas 为空(T4 尚未对任何公式作出判定)</div>
      </div>
    )
  }

  const matched = formula ? matchFormulaByTokens(formula, live) : null
  const ds = formula ? firstDataset(formula) : null

  return (
    <div className="vp" data-testid="vp-ready">
      {formula && (
        <>
          <DecisionRow
            formula={formula}
            config={report.config}
            formulaId={matched?.formula_id ?? null}
            onDeploy={() => void handleDeploy()}
            deployState={deployState}
          />
          <ProvenanceStrip formula={formula} config={report.config} generatedAt={report.generated_at} />
          <FormulaTable formulas={report.formulas} selected={selIdx} onSelect={setSelIdx} />
          <FoldBlock ds={ds} />
          <GovSplit ds={ds} />
          {matched ? (
            <IcDecayChartCard formulaId={matched.formula_id} params={icParams} />
          ) : (
            <div className="chart-card">
              <div className="ch-head">
                <span className="ch-title">IC 衰减</span>
                <span className="ch-note">不可用</span>
              </div>
              <div className="vp-state">该报告公式在本地产物中无 tokens 等值匹配,无法拉取 IC 衰减(需先训练产物登记为 live formula)</div>
            </div>
          )}
          <DatasetDetails formula={formula} />
        </>
      )}
    </div>
  )
}
