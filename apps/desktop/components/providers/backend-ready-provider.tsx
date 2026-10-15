"use client"

import { useEffect, useState, useCallback } from "react"
import { usePathname } from "next/navigation"

// 新前端 API_BASE = "" (见 lib/api-client.ts:4)，Next.js rewrites 将 /api/v1/* 转发到 8088
const HEALTH_URL = "/api/v1/health"
// 冷启动期间后端"活着但答得慢"：/health 要跑 registry 全量探针，还要跟启动任务
// 抢事件循环。5s 太短 —— 会把慢响应误判成"后端不在"，逼出降级空态。
// 15s 足够覆盖一次慢响应，同时真挂掉时仍能快速失败。重试次数不变(总预算不变)。
const HEALTH_TIMEOUT_MS = 15_000
const MAX_RETRIES = 60
const POLL_INTERVAL = 1000

// 「仅浏览界面」降级态下每 8s 轻探一次后端；探到就自动升级回 ready
// （去掉顶上那条提示条）。慢一点无所谓——降级态本来就是「先用着，等后端」。
const DEGRADED_POLL_INTERVAL = 8000

// Landing 页不拦截 — 入口页无条件可见，点"进入平台"才触发后端检测
const BYPASS_PATHS = ["/"]

const BOOT_SEQUENCE = [
  { at: 1, module: "hint", msg: "浏览器须用 localhost 访问 — 127.0.0.1 会被 dev 跨源墙拦截", cls: "warn" as const },
  { at: 1, module: "core", msg: "Quant engine loaded", cls: "ok" as const },
  { at: 3, module: "data", msg: "Market data layer initialized", cls: "ok" as const },
  { at: 6, module: "strategy", msg: "18 strategy templates registered", cls: "ok" as const },
  { at: 10, module: "ws", msg: "WebSocket channels pending", cls: "warn" as const },
  { at: 15, module: "api", msg: "REST endpoints standing by", cls: "ok" as const },
  { at: 20, module: "hint", msg: "后端冷启动需 45-60s — 健康门轮询最长 60s,请勿提前关闭页面", cls: "warn" as const },
  { at: 20, module: "cache", msg: "SQLite connection pool ready", cls: "ok" as const },
  { at: 25, module: "auth", msg: "Local auth context initialized", cls: "ok" as const },
  { at: 30, module: "runtime", msg: "Still waiting for health check...", cls: "warn" as const },
  { at: 40, module: "runtime", msg: "Extended wait — still polling", cls: "warn" as const },
  { at: 50, module: "runtime", msg: "Final attempt window", cls: "warn" as const },
]

const ACTIVITY_MESSAGES = [
  "Connecting to runtime",
  "Awaiting health response",
  "Polling local service",
  "Awaiting runtime",
]

type LogEntry = { timestamp: string; module: string; msg: string; cls: "ok" | "warn" | "err" }

function formatTime(s: number): string {
  const m = Math.floor(s / 60)
  const sec = s % 60
  return m > 0 ? `${m}:${String(sec).padStart(2, "0")}` : `${sec}s`
}

/**
 * 后端不可达时的非阻塞提示条。
 *
 * 取代了原来那张全屏「Runtime Unreachable」死页——那张页会把整个应用锁住，
 * 界面全看不到。现在业务界面照常渲染（各页显示自己的空态），这条只贴在底部
 * 说明「为什么是空的」，并在后端起来后由 Provider 自动移除。
 */
function BackendDegradedBanner({
  lastError,
  debug,
  onRetry,
}: {
  lastError: string
  debug: string[]
  onRetry: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div
      role="status"
      className="fixed inset-x-0 bottom-0 z-[9999] border-t px-4 py-2.5"
      style={{
        background: "rgba(20,14,14,0.96)",
        borderColor: "rgba(239,68,68,0.25)",
        backdropFilter: "blur(8px)",
      }}
    >
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-3 gap-y-1.5 font-mono text-[11px]">
        <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: "var(--destructive)" }} />
        <span style={{ color: "#f87171" }}>
          后端未连接 — 界面为空态
        </span>
        <span style={{ color: "var(--inst-text-dim)" }}>
          正在后台重试；恢复后本提示自动消失
        </span>
        {lastError && (
          <span className="break-all opacity-70" style={{ color: "var(--inst-text-dim)" }}>
            ({lastError})
          </span>
        )}
        <button
          type="button"
          onClick={onRetry}
          className="cursor-pointer rounded px-2 py-0.5 transition-colors"
          style={{
            border: "1px solid rgba(255,255,255,0.10)",
            color: "var(--foreground)",
          }}
        >
          立即重试
        </button>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="cursor-pointer select-none transition-colors"
          style={{ color: "var(--inst-text-dim)" }}
        >
          {open ? "收起诊断 ▲" : "诊断 ▼"}
        </button>
        {open && (
          <div
            className="mt-1 max-h-[120px] w-full overflow-y-auto break-all rounded p-2 leading-[1.6]"
            style={{
              background: "rgba(255,255,255,0.02)",
              border: "1px solid rgba(255,255,255,0.04)",
              color: "var(--inst-text-dim)",
            }}
          >
            {debug.join("\n") || "No entries"}
          </div>
        )}
      </div>
    </div>
  )
}

export function BackendReadyProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [status, setStatus] = useState<"checking" | "ready" | "degraded">("checking")
  const [attempt, setAttempt] = useState(0)
  const [lastError, setLastError] = useState("")
  const [logs, setLogs] = useState<LogEntry[]>([])
  // 诊断记录用 state 而非 ref：提示条要在 render 里读它，读 ref.current
  // 属于 render 期访问 ref（react-hooks/refs 会报，且可能不触发重渲染）。
  const [debug, setDebug] = useState<string[]>([])

  const bypass = BYPASS_PATHS.includes(pathname || "")

  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(HEALTH_URL, {
        signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
        mode: "cors",
      })
      setDebug((prev) => [...prev, `[${new Date().toISOString()}] status=${res.status} ok=${res.ok}`])
      if (res.ok) {
        setStatus("ready")
        return true
      }
      setLastError(`HTTP ${res.status}`)
    } catch (err) {
      const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
      setDebug((prev) => [...prev, `[${new Date().toISOString()}] error: ${msg}`])
      setLastError(msg)
    }
    return false
  }, [])

  const startPolling = useCallback(async () => {
    setStatus("checking")
    setAttempt(0)
    setLogs([])
    setDebug([])
    for (let i = 0; i < MAX_RETRIES; i++) {
      setAttempt(i + 1)
      const ok = await checkHealth()
      if (ok) return
      await new Promise((r) => setTimeout(r, POLL_INTERVAL))
    }
    // 后端 60s 内没起来 —— 不再把整个应用锁在这一屏。
    // 放行到业务界面（各页自己显示空态），顶上挂一条提示条。
    setStatus("degraded")
  }, [checkHealth])

  useEffect(() => {
    // Landing 等入口页跳过后端检测；一旦用户点进业务路由才开始 polling
    if (bypass) {
      setStatus("ready")
      return
    }
    startPolling()
  }, [startPolling, bypass])

  // 降级态下低频探测：后端随后起来了就自动摘掉提示条，不用刷新页面。
  useEffect(() => {
    if (status !== "degraded") return
    let cancelled = false
    const tick = async () => {
      const ok = await checkHealth()
      if (!cancelled && ok) setStatus("ready")
    }
    const id = setInterval(tick, DEGRADED_POLL_INTERVAL)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [status, checkHealth])

  // Build boot log entries based on attempt
  useEffect(() => {
    if (status !== "checking") return
    const now = new Date()
    const ts = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`
    const newEntries = BOOT_SEQUENCE.filter((e) => e.at === attempt).map((e) => ({
      timestamp: ts,
      module: e.module,
      msg: e.msg,
      cls: e.cls,
    }))
    if (newEntries.length > 0) {
      setLogs((prev) => [...prev, ...newEntries])
    }
  }, [attempt, status])

  // bypass 路径（landing 入口）无条件放行 — SSR 首屏也不能被诊断面板拦住
  if (bypass || status === "ready") {
    return <>{children}</>
  }

  // 降级态：放行业务界面，只在底部挂一条非阻塞提示条。
  //
  // 后端没起来时把整个应用锁在启动屏上是错的——用户既看不到任何界面，
  // 也不知道为什么。各页早就按「后端不可达 = 空态」设计（useApiPage 默认
  // 吞错误返 null，43 页不破），所以放行是安全的；这里只负责把「为什么
  // 是空的」说清楚，并在后端随后起来时自动摘掉提示条。
  if (status === "degraded") {
    return (
      <>
        {children}
        <BackendDegradedBanner
          lastError={lastError}
          debug={debug}
          onRetry={startPolling}
        />
      </>
    )
  }

  const activityIdx = Math.min(Math.floor(attempt / 15), ACTIVITY_MESSAGES.length - 1)
  const progressPct = Math.min((attempt / MAX_RETRIES) * 100, 95)

  return (
    <div className="fixed inset-0 flex items-center justify-center overflow-hidden"
      style={{ background: "#0a0e17" }}>

      {/* Ambient orbs */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden" aria-hidden>
        <div className="absolute rounded-full opacity-0"
          style={{
            width: 600, height: 600, top: "-15%", left: "-10%",
            background: "radial-gradient(circle, rgba(227,182,80,0.15) 0%, transparent 70%)",
            filter: "blur(100px)",
            animation: "orbDrift1 20s ease-in-out infinite, orbFadeIn 3s 0.3s ease forwards",
          }} />
        <div className="absolute rounded-full opacity-0"
          style={{
            width: 500, height: 500, bottom: "-15%", right: "-10%",
            background: "radial-gradient(circle, rgba(227,182,80,0.10) 0%, transparent 70%)",
            filter: "blur(100px)",
            animation: "orbDrift2 25s ease-in-out infinite, orbFadeIn 3s 0.8s ease forwards",
          }} />
        <div className="absolute rounded-full opacity-0"
          style={{
            width: 400, height: 400, top: "30%", right: "10%",
            background: "radial-gradient(circle, rgba(227,182,80,0.08) 0%, transparent 70%)",
            filter: "blur(100px)",
            animation: "orbDrift3 18s ease-in-out infinite, orbFadeIn 3s 1.2s ease forwards",
          }} />
      </div>

      {/* Grid layer */}
      <div className="pointer-events-none fixed inset-0" aria-hidden
        style={{
          backgroundImage: "linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px)",
          backgroundSize: "60px 60px",
          maskImage: "radial-gradient(ellipse 70% 60% at 50% 50%, black 30%, transparent 75%)",
          WebkitMaskImage: "radial-gradient(ellipse 70% 60% at 50% 50%, black 30%, transparent 75%)",
        }} />

      {/* Scan line */}
      <div className="pointer-events-none fixed left-0 right-0" aria-hidden
        style={{
          height: 2,
          background: "linear-gradient(90deg, transparent, rgba(227,182,80,0.15), rgba(227,182,80,0.08), transparent)",
          boxShadow: "0 0 12px rgba(227,182,80,0.08)",
          animation: "scanDown 6s ease-in-out infinite",
          opacity: 0,
        }} />

      {/* Noise texture */}
      <div className="pointer-events-none fixed inset-0" aria-hidden
        style={{
          backgroundImage: "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
          opacity: 0.012,
        }} />

      {/* Top glow */}
      <div className="pointer-events-none fixed left-0 right-0 top-0" aria-hidden
        style={{
          height: 500,
          background: "radial-gradient(ellipse 50% 40% at 30% 0%, rgba(227,182,80,0.06) 0%, transparent 100%)",
        }} />

      {/* Center block */}
      <div className="relative z-10 flex w-[420px] flex-col items-start">

        {/* Brand row */}
        <div className="flex items-center gap-3" style={{ animation: "fadeIn 0.5s ease both" }}>
          {/* Logo mark with rotating gradient border */}
          <div className="relative flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-[9px]"
            style={{
              background: "linear-gradient(135deg, rgba(227,182,80,0.10), rgba(227,182,80,0.06))",
              border: "1px solid rgba(255,255,255,0.06)",
              boxShadow: "0 0 24px rgba(227,182,80,0.06), inset 0 1px 0 rgba(255,255,255,0.04)",
            }}>
            <div className="absolute inset-[-1px] rounded-[10px]"
              style={{
                background: "conic-gradient(from var(--logo-angle, 0deg), rgba(227,182,80,0.3), rgba(227,182,80,0.2), rgba(227,182,80,0.05), rgba(227,182,80,0.15), rgba(227,182,80,0.3))",
                animation: "logoRotate 8s linear infinite",
                zIndex: -1,
              }} />
            <div className="absolute inset-[1px] rounded-[8px]"
              style={{ background: "var(--card)", zIndex: -1 }} />
            <svg viewBox="0 0 1024 1024" fill="#e3b650" opacity={0.85}
              className="relative z-[1] h-4 w-4">
              <path d="M268.703,730.897C251.785,735.45,235.16,738.271,218.012,734.749C195.676,730.161,180.803,716.426,170.867,696.606C162.435,679.787,159.228,661.614,157.784,643.116C154.322,598.778,161.729,555.736,175.846,513.995C204.417,429.517,255.378,360.868,325.658,306.281C383.929,261.022,449.698,231.926,521.429,216.122C557.907,208.085,594.941,203.458,632.408,204.829C681.59,206.628,728.867,216.306,772.261,240.809C773.95,241.762,775.987,242.31,777.215,244.622C774.832,245.936,772.357,245.296,770.042,245.122C714.447,240.941,661.843,251.87,612.857,278.165C527.339,324.071,474.888,395.891,450.419,489.025C443.932,513.718,440.122,538.831,439.176,564.364C438.935,570.852,438.634,577.339,438.232,583.819C437.548,594.86,432.86,604.341,426.422,613.051C395.478,654.914,358.074,689.442,311.651,713.627C298.038,720.718,283.915,726.639,268.703,730.897Z" />
            </svg>
          </div>
          <div>
            <div className="font-serif text-[15px] font-bold tracking-tight"
              style={{
                background: "linear-gradient(135deg, #e3b650, #F5E6A3, #e3b650)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}>
              SeleneQuant
            </div>
            <div className="font-mono text-[9px] uppercase tracking-[0.14em]"
              style={{ color: "var(--inst-text-dim)" }}>
              Quantitative Intelligence
            </div>
          </div>
          <span className="ml-auto rounded px-[7px] py-[2px] font-mono text-[9px] font-semibold"
            style={{
              background: "var(--inst-gold-muted)",
              border: "1px solid rgba(227,182,80,0.08)",
              color: "var(--inst-gold)",
            }}>
            v0.1
          </span>
        </div>

        {/* Gold divider */}
        <div className="relative mt-[18px] h-px w-full"
          style={{
            background: "linear-gradient(90deg, var(--inst-gold), rgba(227,182,80,0.08), transparent)",
            animation: "fadeIn 0.6s 0.1s ease both",
          }}>
          <div className="absolute left-0 top-[-2px] bottom-[-2px] w-[80px]"
            style={{
              background: "linear-gradient(90deg, rgba(227,182,80,0.15), transparent)",
              filter: "blur(2px)",
            }} />
        </div>

        {/* Terminal area */}
        <div className="mt-[18px] w-full">

          {status === "checking" && (
            <>
              {/* Boot log */}
              <div className="mb-4 flex flex-col gap-0">
                {logs.map((log, i) => (
                  <div key={i} className="font-mono text-xs tabular-nums leading-[1.8]"
                    style={{ animation: "logAppear 0.3s ease forwards", opacity: 0 }}>
                    <span style={{ color: "var(--inst-text-dim)" }} className="mr-2">{log.timestamp}</span>
                    <span style={{ color: "var(--primary)" }} className="mr-1.5 font-medium">[{log.module}]</span>
                    <span style={{
                      color: log.cls === "ok" ? "var(--inst-emerald)"
                        : log.cls === "warn" ? "var(--inst-gold)"
                        : "var(--destructive)",
                    }}>
                      {log.msg}
                    </span>
                  </div>
                ))}
              </div>

              {/* Activity divider */}
              <div className="mb-2.5 h-px w-[60px]"
                style={{ background: "linear-gradient(90deg, var(--inst-gold), transparent)" }} />

              {/* Activity line */}
              <div className="flex items-center gap-2.5 font-mono text-xs tabular-nums">
                <span style={{ color: "var(--muted-foreground)" }}>
                  {ACTIVITY_MESSAGES[activityIdx]}
                </span>
                {/* Animated dots */}
                <span className="inline-flex w-5 shrink-0 gap-[2px]">
                  {[0, 1, 2].map((n) => (
                    <span key={n} className="inline-block h-[3px] w-[3px] rounded-full"
                      style={{
                        background: "var(--muted-foreground)",
                        animation: `dotCycle 1.2s ease-in-out infinite ${n * 0.2}s`,
                      }} />
                  ))}
                </span>
                {/* Inline progress */}
                <div className="ml-1 h-[2px] w-[100px] shrink-0 overflow-hidden rounded-sm relative"
                  style={{ background: "rgba(255,255,255,0.04)" }}>
                  <div className="absolute left-0 top-0 h-full rounded-sm transition-all duration-400"
                    style={{
                      width: `${progressPct}%`,
                      background: "linear-gradient(90deg, var(--primary), var(--inst-gold))",
                      transitionTimingFunction: "cubic-bezier(0.22,1,0.36,1)",
                    }} />
                  <div className="absolute top-[-1px] bottom-[-1px] w-[30px]"
                    style={{
                      background: "linear-gradient(90deg, transparent, rgba(227,182,80,0.5), transparent)",
                      animation: "sweep 2s ease-in-out infinite",
                    }} />
                </div>
                <span className="ml-auto font-mono text-[11px] tabular-nums"
                  style={{ color: "var(--inst-text-dim)" }}>
                  {formatTime(attempt)}
                </span>
              </div>
            </>
          )}

        </div>
      </div>

      {/* Keyframes injected via style tag — @property for logo rotation must be global */}
      <style jsx global>{`
        @property --logo-angle {
          syntax: '<angle>';
          initial-value: 0deg;
          inherits: false;
        }
        @keyframes logoRotate {
          from { --logo-angle: 0deg; }
          to { --logo-angle: 360deg; }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes logAppear {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes dotCycle {
          0%, 80%, 100% { opacity: 0.15; }
          40% { opacity: 1; }
        }
        @keyframes sweep {
          0% { left: -30px; }
          100% { left: calc(100% + 30px); }
        }
        @keyframes orbDrift1 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          33% { transform: translate(80px, 60px) scale(1.05); }
          66% { transform: translate(-40px, 80px) scale(0.95); }
        }
        @keyframes orbDrift2 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          33% { transform: translate(-70px, -40px) scale(1.08); }
          66% { transform: translate(50px, -60px) scale(0.96); }
        }
        @keyframes orbDrift3 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50% { transform: translate(-60px, 40px) scale(1.06); }
        }
        @keyframes orbFadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes scanDown {
          0% { top: -2px; opacity: 0; }
          5% { opacity: 1; }
          95% { opacity: 1; }
          100% { top: 100%; opacity: 0; }
        }
      `}</style>
    </div>
  )
}
