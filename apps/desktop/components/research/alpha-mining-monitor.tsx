"use client"

import { useEffect, useState } from "react"

import type { MonitorQuad, MonitorQuadPayload, MonitorReading } from "@/lib/api/alpha"
import { monitorList, monitorRead, monitorReadAsync, monitorRegister, monitorTaskStatus, monitorUnregister } from "@/lib/api/alpha"
import { useUiText } from "@/components/i18n/use-ui-text"

type Phase = "ready" | "loading" | "err"

interface QuadRow extends MonitorQuad {
  reading: MonitorReading | null
  readingPhase: Phase
  err: string | null
}

const INTERVALS = ["15m", "1h", "4h", "1d"]

/** 监控 tab:B4 四元组(klines.db × symbol × interval × factor)对象列表 + 读数。
 *  六端点族:register/unregister/list/read/read-async/task。全真数据,无 mock。 */
export function MonitorPane() {
  const tr = useUiText()

  const [quads, setQuads] = useState<QuadRow[]>([])
  const [phase, setPhase] = useState<Phase>("loading")
  const [err, setErr] = useState<string | null>(null)
  const [symbol, setSymbol] = useState("BTC/USDT")
  const [interval, setInterval] = useState("1h")
  const [factor, setFactor] = useState("")
  const [submitting, setSubmitting] = useState(false)

  async function load() {
    setPhase("loading")
    setErr(null)
    try {
      const list = await monitorList()
      setQuads(list.map((q) => ({ ...q, reading: null, readingPhase: "ready", err: null })))
      setPhase("ready")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setPhase("err")
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function register() {
    if (!symbol.trim() || !factor.trim()) return
    const quad: MonitorQuadPayload = { symbol: symbol.trim(), interval, factor: factor.trim() }
    setSubmitting(true)
    try {
      await monitorRegister(quad)
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  async function remove(i: number) {
    const q = quads[i]
    if (!q) return
    try {
      await monitorUnregister({ symbol: q.symbol, interval: q.interval, factor: q.factor })
      setQuads((prev) => prev.filter((_, j) => j !== i))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  async function read(i: number) {
    const q = quads[i]
    if (!q) return
    setQuads((prev) => prev.map((r, j) => (j === i ? { ...r, readingPhase: "loading", err: null } : r)))
    try {
      const reading = await monitorRead({ symbol: q.symbol, interval: q.interval, factor: q.factor })
      setQuads((prev) =>
        prev.map((r, j) =>
          j === i
            ? { ...r, reading, readingPhase: "ready", err: null }
            : r,
        ),
      )
    } catch (e) {
      setQuads((prev) => prev.map((r, j) => (j === i ? { ...r, readingPhase: "err", err: e instanceof Error ? e.message : String(e) } : r)))
    }
  }

  /** 冷缓存异步重算:先拿 task_id,再轮询 task 状态。 */
  async function recompute(i: number) {
    const q = quads[i]
    if (!q) return
    try {
      const task = await monitorReadAsync({ symbol: q.symbol, interval: q.interval, factor: q.factor })
      let t = task
      while (t.status === "pending" || t.status === "running") {
        await new Promise((r) => setTimeout(r, 1200))
        t = await monitorTaskStatus(t.task_id)
      }
      await read(i)
    } catch (e) {
      setQuads((prev) => prev.map((r, j) => (j === i ? { ...r, readingPhase: "err", err: e instanceof Error ? e.message : String(e) } : r)))
    }
  }

  return (
    <section className="wb-section" data-testid="monitor-pane">
      <div className="mon-form">
        <input
          className="wb-input"
          placeholder="symbol · BTC/USDT"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          data-testid="mon-symbol"
        />
        <select className="wb-select" value={interval} onChange={(e) => setInterval(e.target.value)} data-testid="mon-interval">
          {INTERVALS.map((iv) => (
            <option key={iv} value={iv}>
              {iv}
            </option>
          ))}
        </select>
        <input
          className="wb-input"
          placeholder={tr("factor · 因子名")}
          value={factor}
          onChange={(e) => setFactor(e.target.value)}
          data-testid="mon-factor"
        />
        <Button label={tr("注册对象")} onClick={register} disabled={submitting} busy={submitting} testId="mon-register" />
      </div>

      {err && phase === "err" && <p className="bt-err" data-testid="mon-err">{err}</p>}

      <div className="mon-list" data-testid="mon-list">
        {phase === "loading" && <p className="bt-empty">{tr("加载监控对象…")}</p>}
        {phase === "ready" && quads.length === 0 && <p className="bt-empty">{tr("暂无监控对象 · 上方注册 B4 四元组")}</p>}
        {quads.map((q, i) => (
          <div key={`${q.source ?? "quad"}-${i}`} className="mon-row" data-testid={`mon-row-${i}`}>
            <span className="mon-q">
              {q.symbol} · {q.interval}
            </span>
            <span className="fscore">{q.factor}</span>
            <div className="mon-actions">
              <Button label={tr("读数")} onClick={() => read(i)} busy={q.readingPhase === "loading"} testId={`mon-read-${i}`} />
              <button type="button" className="btc" onClick={() => recompute(i)} title={tr("冷缓存异步重算")}>
                {tr("重算")}
              </button>
              <button type="button" className="bt-remove" onClick={() => remove(i)} title={tr("移除")}>
                {tr("移除")}
              </button>
            </div>
            {q.reading && (
              <div className="mon-read" data-testid={`mon-reading-${i}`}>
                <span className="mon-value tnum">{q.reading.value === null ? "—" : q.reading.value.toFixed(4)}</span>
                <span className="mon-fresh">
                  {tr("于")} {q.reading.computed_at ? new Date(q.reading.computed_at).toLocaleTimeString() : "—"}
                  {q.reading.last_bar_ts ? ` · bar ${new Date(q.reading.last_bar_ts).toLocaleString()}` : ""}
                </span>
                {q.reading.stale ? <span className="mon-stale-badge">{tr("缓存过期 · 后台刷新")}</span> : <span className="mon-ready">{tr("新鲜")}</span>}
                {q.reading.recomputed && <span className="mon-ready">{tr("同步重算")}</span>}
              </div>
            )}
            {q.readingPhase === "err" && q.err && <p className="bt-err">{q.err}</p>}
          </div>
        ))}
      </div>
    </section>
  )
}

function Button({
  label,
  onClick,
  disabled,
  busy,
  testId,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  busy?: boolean
  testId?: string
}) {
  return (
    <button type="button" className="btc wb-btn" onClick={onClick} disabled={disabled || busy} data-testid={testId}>
      {busy ? "…" : label}
    </button>
  )
}
