"use client"

import { useEffect, useState } from "react"

import { deployBlockReason, matchFormulaByTokens } from "./alpha-mining-validation"
import { getValidationReport } from "@/lib/api/alpha"
import type { FormulaValidation, MinedFormulaInfo } from "@/lib/api/alpha"
import { useUiText } from "@/components/i18n/use-ui-text"

export interface DeployLogEntry {
  target: string
  ok: boolean
  at: string
  note?: string
}

type Phase = "loading" | "ready" | "err" | "missing"

/** 治理 tab:deploy 历史(会话级 log,后端无 deploy 历史端点)+ T4 verdict 展示(US-002 数据源)。
 *  deploy 按钮门控与 ValidationPane 同语义(decision None/accept → 可点;hold/reject/defer → 禁用+原因)。 */
export function GovernancePane({
  formulas,
  deployLog,
  onDeployFormula,
  onGoValidation,
}: {
  formulas: MinedFormulaInfo[]
  deployLog: DeployLogEntry[]
  onDeployFormula: (formulaId: string) => Promise<void>
  onGoValidation: () => void
}) {
  const tr = useUiText()

  const [verdicts, setVerdicts] = useState<FormulaValidation[] | null>(null)
  const [phase, setPhase] = useState<Phase>("loading")
  const [err, setErr] = useState<string | null>(null)
  const [deploying, setDeploying] = useState<string | null>(null)

  async function loadReport() {
    setPhase("loading")
    setErr(null)
    try {
      const env = await getValidationReport()
      const report = env?.report
      if (!report || !Array.isArray(report.formulas) || report.formulas.length === 0) {
        setPhase("missing")
        return
      }
      setVerdicts(report.formulas)
      setPhase("ready")
    } catch (e) {
      // 404 = 报告未生成(语义显形)。
      setErr(e instanceof Error ? e.message : String(e))
      setPhase("missing")
    }
  }

  useEffect(() => {
    void loadReport()
  }, [])

  async function deploy(fid: string) {
    setDeploying(fid)
    try {
      await onDeployFormula(fid)
    } finally {
      setDeploying(null)
    }
  }

  const verdictClass = (v: string) => {
    const k = String(v ?? "").toLowerCase()
    if (k.includes("accept") || k.includes("pass")) return "valid"
    if (k.includes("hold")) return "na"
    if (k.includes("reject") || k.includes("invalid")) return "invalid"
    if (k.includes("defer")) return "na"
    return "susp"
  }

  // 部署日志按来源分组:批量会话(note 以 batch:{batch_id} 开头)归到其批量组,其余为单会话。
  const batchGroups = new Map<string, DeployLogEntry[]>()
  const singles: DeployLogEntry[] = []
  for (const d of deployLog) {
    const m = /^batch:(\S+)/.exec(d.note ?? "")
    if (m) {
      const arr = batchGroups.get(m[1]) ?? []
      arr.push(d)
      batchGroups.set(m[1], arr)
    } else {
      singles.push(d)
    }
  }

  return (
    <section className="wb-section" data-testid="governance-pane">
      <div className="govn-two">
        <div className="govn-block">
          <h4 className="bt-title">{tr("部署历史")}</h4>
          {deployLog.length === 0 ? (
            <p className="bt-empty">{tr("尚无部署动作 · 在回测/验证 tab 发起部署后在此留痕")}</p>
          ) : (
            <div className="dep-history" data-testid="dep-history">
              {[...batchGroups.entries()].map(([bid, rows]) => (
                <div key={bid} data-testid={`dep-batch-${bid}`} className="mb-2">
                  <div className="text-xs font-medium text-muted-foreground mb-1">
                    {tr("批量")} {bid.slice(-8)} · {rows.length} {tr("条部署 ·")} {rows.filter((r) => r.ok).length} {tr("成功")}
                  </div>
                  {rows.map((d, i) => (
                    <div key={i} className={`dep-row ${d.ok ? "ok" : "blocked"}`} data-testid={`dep-row-${bid}-${i}`}>
                      <span className="dep-status">{d.ok ? tr("已部署") : tr("被拒")}</span>
                      <span className="dep-target">{d.target}</span>
                      <span className="dep-t tnum">{new Date(d.at).toLocaleTimeString()}</span>
                    </div>
                  ))}
                </div>
              ))}
              {singles.map((d, i) => (
                <div key={`s-${i}`} className={`dep-row ${d.ok ? "ok" : "blocked"}`} data-testid={`dep-row-single-${i}`}>
                  <span className="dep-status">{d.ok ? tr("已部署") : tr("被拒")}</span>
                  <span className="dep-target">{d.target}</span>
                  <span className="dep-t">{d.note ? tr(d.note) : ""}</span>
                  <span className="dep-at tnum">{new Date(d.at).toLocaleTimeString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="govn-block">
          <h4 className="bt-title">{tr("T4 裁决")}</h4>
          {phase === "loading" && <p className="bt-empty">{tr("加载验证报告…")}</p>}
          {phase === "missing" && (
            <div className="govn-empty" data-testid="govn-missing">
              <p>{tr("验证报告未生成(先跑验证管线)。")}</p>
              <p className="bs-hint">
                {tr("管线确认后,这里会消费 US-002 数据源展示各公式 T4 verdict 与 deploy 门控。 Meta 评审:hold/reject/defer 一律禁用部署。")}
              </p>
              <button type="button" className="btc wb-btn" onClick={onGoValidation}>
                {tr("去验证 tab")}
              </button>
            </div>
          )}
          {phase === "ready" && verdicts && (
            <div className="govn-tbl" data-testid="govn-verdicts">
              {verdicts.map((fv, i) => {
                const info = matchFormulaByTokens(fv, formulas)
                const blocked = deployBlockReason(fv.decision, fv.reason)
                const deployable = info !== null
                const deployTitle = blocked
                  ? blocked
                  : deployable
                    ? tr("T4 accept → 可直接部署")
                    : tr("本地无匹配产物,先训练/导入该公式")
                return (
                  <div key={i} className="govn-row" data-testid={`govn-v-${i}`}>
                    <span className="fname">{fv.readable}</span>
                    <span className={`verdict ${verdictClass(fv.decision)}`} data-testid={`verdict-${i}`}>
                      {fv.decision}
                    </span>
                    <span className="fscore" title={fv.reason ?? ""}>
                      {(fv.mean_ic ?? 0).toFixed(3)} IC
                    </span>
                    <button
                      type="button"
                      className={`btc wb-btn ${blocked || !deployable ? "disabled" : ""}`}
                      disabled={!!blocked || !deployable || deploying === (info?.formula_id ?? "")}
                      title={deployTitle}
                      onClick={() => info && deploy(info.formula_id)}
                      data-testid={`govn-deploy-${i}`}
                    >
                      {deploying === (info?.formula_id ?? "") ? "…" : tr("部署")}
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
