"use client";

import Link from "next/link";
import { useEffect, useState, useCallback, useRef } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, ArrowRight, Terminal, Database, Activity, Cpu, Wifi } from "lucide-react";

const HEALTH_URL = "http://localhost:8088/api/v1/health";
const MAX_RETRIES = 60;
const POLL_INTERVAL = 1000;

const BOOT_SEQUENCE = [
  { at: 1, module: "core", msg: "Quant engine loaded", cls: "ok" as const },
  { at: 3, module: "data", msg: "Market data layer initialized", cls: "ok" as const },
  { at: 6, module: "strategy", msg: "18 strategy templates registered", cls: "ok" as const },
  { at: 10, module: "ws", msg: "WebSocket channels pending", cls: "warn" as const },
  { at: 15, module: "api", msg: "REST endpoints standing by", cls: "ok" as const },
  { at: 20, module: "cache", msg: "SQLite connection pool ready", cls: "ok" as const },
  { at: 25, module: "auth", msg: "Local auth context initialized", cls: "ok" as const },
  { at: 30, module: "runtime", msg: "Still waiting for health check...", cls: "warn" as const },
  { at: 40, module: "runtime", msg: "Extended wait — still polling", cls: "warn" as const },
  { at: 50, module: "runtime", msg: "Final attempt window", cls: "warn" as const },
];

const ACTIVITY_MESSAGES = [
  "Connecting to runtime",
  "Awaiting health response",
  "Polling local service",
  "Awaiting runtime",
];

type LogEntry = { timestamp: string; module: string; msg: string; cls: "ok" | "warn" | "err" };

function formatTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return m > 0 ? `${m}:${String(sec).padStart(2, "0")}` : `${sec}s`;
}

function nowTs(): string {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

/**
 * MOCK 预览加载页
 * 后端未连接时显示的 fallback 视图：
 * - 顶部 MOCK 红条
 * - Hero 骨架预览 + ambient orbs + 扫描线（对齐 BackendReadyProvider 风格）
 * - 启动指引 + boot log（BOOT_SEQUENCE 假启动日志 + ACTIVITY_MESSAGES 分阶段提示）
 * - 失败诊断面板（60s 后显示 Runtime Unreachable + Reconnect 按钮）
 */
export function MockPreviewLoading() {
  const [status, setStatus] = useState<"checking" | "failed">("checking");
  const [attempt, setAttempt] = useState(0);
  const [lastError, setLastError] = useState("");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [debugOpen, setDebugOpen] = useState(false);
  const debugRef = useRef<string[]>([]);

  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(5000) });
      debugRef.current.push(`[${new Date().toISOString()}] status=${res.status} ok=${res.ok}`);
      if (res.ok) {
        // 后端就绪，刷新页面让 BackendReadyProvider 接管
        window.location.reload();
        return true;
      }
      setLastError(`HTTP ${res.status}`);
    } catch (err) {
      const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
      debugRef.current.push(`[${new Date().toISOString()}] error: ${msg}`);
      setLastError(msg);
    }
    return false;
  }, []);

  const startPolling = useCallback(async () => {
    setStatus("checking");
    setAttempt(0);
    setLogs([]);
    for (let i = 0; i < MAX_RETRIES; i++) {
      setAttempt(i + 1);
      const ok = await checkHealth();
      if (ok) return;
      await new Promise((r) => setTimeout(r, POLL_INTERVAL));
    }
    setStatus("failed");
  }, [checkHealth]);

  useEffect(() => {
    startPolling();
  }, [startPolling]);

  // 按 attempt 追加 BOOT_SEQUENCE 日志
  useEffect(() => {
    if (status !== "checking") return;
    const newEntries = BOOT_SEQUENCE.filter((e) => e.at === attempt).map((e) => ({
      timestamp: nowTs(),
      module: e.module,
      msg: e.msg,
      cls: e.cls,
    }));
    if (newEntries.length > 0) {
      setLogs((prev) => [...prev, ...newEntries]);
    }
  }, [attempt, status]);
  return (
    <div className="relative min-h-screen bg-[#131722] text-white overflow-hidden">
      {/* Ambient orbs — 对齐 BackendReadyProvider 诊断面板 */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-40 -left-40 size-[500px] rounded-full bg-red-500/10 blur-[120px]" />
        <div className="absolute -bottom-40 -right-40 size-[500px] rounded-full bg-amber-500/8 blur-[120px]" />
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 size-[400px] rounded-full bg-cyan-500/6 blur-[100px]" />
      </div>

      {/* Grid 背景 */}
      <div className="pointer-events-none absolute inset-0 opacity-50"
        style={{
          backgroundImage: "linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px)",
          backgroundSize: "60px 60px",
        }}
      />

      {/* 顶部 MOCK 警示条 */}
      <motion.div
        initial={{ y: "-100%" }}
        animate={{ y: "0%" }}
        transition={{ duration: 0.3, ease: "easeOut" }}
        className="fixed top-0 left-0 right-0 z-50 h-10 flex items-center justify-center gap-3 bg-red-600 text-white text-sm font-semibold border-b border-red-400/30 shadow-[0_2px_12px_rgba(0,0,0,0.6)]"
      >
        <AlertTriangle className="size-4" />
        MOCK 模式预览 · 后端未连接 · 等待启动 uvicorn
        <motion.span
          className="size-2 rounded-full bg-white"
          animate={{ opacity: [1, 0.2, 1] }}
          transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
        />
      </motion.div>

      {/* 主内容区，顶部留 40px 给红条 */}
      <div className="relative pt-10">
        {/* Hero 区 */}
        <section className="px-6 py-20 text-center relative">
          {/* 扫描线 — 对齐 BackendReadyProvider */}
          <motion.div
            className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-red-500/40 to-transparent"
            animate={{ y: ["0vh", "100vh"] }}
            transition={{ duration: 8, repeat: Infinity, ease: "linear" }}
          />

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.2 }}
          >
            <div className="inline-flex items-center gap-2 rounded-full border border-amber-500/30 bg-amber-500/10 px-4 py-1.5 text-xs text-amber-300 mb-6 backdrop-blur-sm">
              <span className="size-1.5 rounded-full bg-amber-400 animate-pulse" />
              FALLBACK DATA PREVIEW
            </div>
            <h1 className="text-5xl md:text-6xl font-bold mb-4 tracking-tight">
              这是 <span className="text-amber-400">MOCK</span> 模式下的界面预览
            </h1>
            <p className="text-white/60 max-w-2xl mx-auto leading-relaxed">
              后端服务未启动，当前显示的是前端 fallback 数据。
              <br />
              启动后端即可看到真实市场数据。
            </p>

            {/* 状态指示器 */}
            <div className="mt-8 flex items-center justify-center gap-6 text-xs font-mono">
              <div className="flex items-center gap-2 text-red-300">
                <span className="size-1.5 rounded-full bg-red-400 animate-pulse" />
                API: disconnected
              </div>
              <div className="flex items-center gap-2 text-amber-300">
                <span className="size-1.5 rounded-full bg-amber-400 animate-pulse" />
                WS: fallback
              </div>
              <div className="flex items-center gap-2 text-cyan-300">
                <span className="size-1.5 rounded-full bg-cyan-400 animate-pulse" />
                UI: ready
              </div>
            </div>
          </motion.div>
        </section>

        {/* Dashboard 骨架预览 */}
        <section className="max-w-6xl mx-auto px-6 pb-16">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.3 }}
            className="rounded-xl border border-white/10 bg-black/40 overflow-hidden backdrop-blur-sm shadow-[0_0_60px_rgba(0,0,0,0.4)]"
          >
            {/* 骨架顶栏 */}
            <div className="flex items-center gap-2 px-4 py-2 border-b border-white/[0.06] bg-black/60">
              <div className="flex gap-1.5">
                <div className="size-2.5 rounded-full bg-red-500/50" />
                <div className="size-2.5 rounded-full bg-amber-500/50" />
                <div className="size-2.5 rounded-full bg-emerald-500/50" />
              </div>
              <div className="flex-1 text-center text-[10px] text-white/40 font-mono">
                selenespace.local/watch/kf — MOCK PREVIEW
              </div>
              <div className="flex items-center gap-1.5 text-[10px] text-amber-400/80 font-mono">
                <span className="size-1 rounded-full bg-amber-400 animate-pulse" />
                MOCK
              </div>
            </div>

            {/* 骨架内容：模拟 dashboard 布局 */}
            <div className="grid grid-cols-[180px_1fr_240px] gap-2 p-2 h-[480px]">
              {/* Sidebar 骨架 */}
              <div className="space-y-2">
                <div className="h-6 rounded bg-white/[0.04]" />
                <div className="h-8 rounded bg-white/[0.06]" />
                <div className="h-8 rounded bg-white/[0.04]" />
                <div className="h-8 rounded bg-white/[0.04]" />
                <div className="h-4 mt-4 bg-white/[0.03]" />
                <div className="h-8 rounded bg-white/[0.06]" />
                <div className="h-8 rounded bg-white/[0.04]" />
              </div>

              {/* 主区：K 线 + 订单簿 */}
              <div className="space-y-2">
                <div className="h-8 rounded bg-white/[0.04] flex items-center px-3 gap-2">
                  <div className="h-3 w-16 rounded bg-cyan-500/20" />
                  <div className="h-3 w-20 rounded bg-white/[0.08]" />
                  <div className="ml-auto h-3 w-12 rounded bg-emerald-500/20" />
                </div>
                <div className="h-[320px] rounded bg-white/[0.02] relative overflow-hidden">
                  {/* 假 K 线 — Catmull-Rom 平滑曲线，对齐 SplineSceneBasic 风格 */}
                  <svg className="absolute inset-0 w-full h-full" viewBox="0 0 400 200" preserveAspectRatio="none">
                    <defs>
                      <linearGradient id="klineGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#1ea85a" stopOpacity="0.3" />
                        <stop offset="100%" stopColor="#1ea85a" stopOpacity="0" />
                      </linearGradient>
                      <filter id="klineGlow">
                        <feGaussianBlur stdDeviation="1.5" result="blur" />
                        <feMerge>
                          <feMergeNode in="blur" />
                          <feMergeNode in="SourceGraphic" />
                        </feMerge>
                      </filter>
                    </defs>
                    <motion.path
                      d="M0,120 C20,100 30,80 40,80 C60,80 70,100 80,100 C100,100 110,70 120,60 C140,50 150,80 160,90 C180,100 190,50 200,40 C220,30 230,60 240,70 C260,80 270,30 280,30 C300,30 310,50 320,60 C340,70 350,20 360,20 C380,20 390,40 400,50"
                      stroke="#1ea85a"
                      strokeWidth="1.5"
                      fill="none"
                      filter="url(#klineGlow)"
                      initial={{ pathLength: 0 }}
                      animate={{ pathLength: 1 }}
                      transition={{ duration: 2.5, repeat: Infinity, repeatType: "loop", ease: "easeInOut" }}
                    />
                    {/* 扫描线 */}
                    <motion.line
                      x1="0" y1="0" x2="0" y2="200"
                      stroke="rgba(255,255,255,0.4)"
                      strokeWidth="0.5"
                      initial={{ x: 0 }}
                      animate={{ x: 400 }}
                      transition={{ duration: 3, repeat: Infinity, ease: "linear" }}
                    />
                  </svg>
                  <div className="absolute bottom-2 left-3 text-[10px] text-white/30 font-mono">
                    BTC-USDT-PERP · 1h · MOCK DATA
                  </div>
                  {/* 角标 */}
                  <div className="absolute top-2 right-2 text-[9px] text-amber-400/60 font-mono">
                    FALLBACK
                  </div>
                </div>
              </div>

              {/* Right panel 骨架 */}
              <div className="space-y-2">
                <div className="h-6 rounded bg-white/[0.04]" />
                <div className="h-12 rounded bg-white/[0.06]" />
                <div className="h-12 rounded bg-white/[0.04]" />
                <div className="h-12 rounded bg-white/[0.04]" />
                <div className="h-4 mt-4 bg-white/[0.03]" />
                <div className="h-16 rounded bg-white/[0.06]" />
              </div>
            </div>
          </motion.div>
        </section>

        {/* 启动指引 + Boot log */}
        <section className="max-w-3xl mx-auto px-6 py-12">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.4 }}
            className="rounded-xl border border-cyan-500/20 bg-cyan-500/[0.04] p-8 backdrop-blur-sm"
          >
            <div className="flex items-center gap-3 mb-6">
              <Terminal className="size-6 text-cyan-400" />
              <h2 className="text-2xl font-semibold">启动后端</h2>
            </div>
            <div className="rounded-lg bg-black/60 p-4 font-mono text-sm mb-6 border border-white/[0.06]">
              <div className="text-white/40 mb-2"># 启动 local-api 服务</div>
              <div className="text-emerald-400">
                <span className="text-white/60">$</span> cd services/local-api
              </div>
              <div className="text-emerald-400">
                <span className="text-white/60">$</span> python -m uvicorn app.main:app --port 8088 --reload
              </div>
            </div>

            {/* Boot log 风格 — 对齐 BackendReadyProvider，动态 BOOT_SEQUENCE + ACTIVITY_MESSAGES */}
            <div className="rounded-lg bg-black/40 p-4 font-mono text-xs border border-white/[0.04] mb-6">
              <div className="text-white/40 mb-2 flex items-center gap-2">
                <Cpu className="size-3" /> BOOT SEQUENCE
              </div>
              <div className="space-y-1">
                {logs.map((log, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3 }}
                    className="tabular-nums leading-[1.8]"
                  >
                    <span className="text-white/40 mr-2">{log.timestamp}</span>
                    <span className="text-cyan-400 mr-1.5 font-medium">[{log.module}]</span>
                    <span
                      className={
                        log.cls === "ok"
                          ? "text-emerald-400"
                          : log.cls === "warn"
                            ? "text-amber-400"
                            : "text-red-400"
                      }
                    >
                      {log.msg}
                    </span>
                  </motion.div>
                ))}

                {/* 失败诊断面板 */}
                {status === "failed" && (
                  <>
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="tabular-nums leading-[1.8]"
                    >
                      <span className="text-white/40 mr-2">{nowTs()}</span>
                      <span className="text-cyan-400 mr-1.5 font-medium">[runtime]</span>
                      <span className="text-red-400">
                        Connection failed after {attempt} attempts
                      </span>
                    </motion.div>

                    <div className="mt-3 rounded-md p-3 bg-red-500/5 border border-red-500/10">
                      <div className="mb-1 text-[11px] font-medium text-red-400">
                        Runtime Unreachable
                      </div>
                      {lastError && (
                        <div className="break-all text-[11px] leading-relaxed text-red-300/80">
                          {lastError}
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={() => startPolling()}
                        className="mt-3 cursor-pointer rounded-md px-4 py-1.5 font-mono text-[11px] font-medium transition-all duration-200 bg-gradient-to-br from-cyan-500/10 to-amber-500/5 border border-white/10 text-white hover:bg-white/5"
                      >
                        Reconnect
                      </button>
                      <div
                        className="mt-2 flex cursor-pointer select-none items-center gap-1 text-[10px] text-white/40"
                        onClick={() => setDebugOpen(!debugOpen)}
                      >
                        <span
                          className="inline-block transition-transform duration-200"
                          style={{ transform: debugOpen ? "rotate(90deg)" : "none", fontSize: 9 }}
                        >▸</span>
                        Diagnostic log
                      </div>
                      {debugOpen && (
                        <div className="mt-1.5 max-h-[100px] overflow-y-auto break-all rounded p-2 font-mono text-[10px] leading-[1.6] bg-white/[0.02] border border-white/[0.04] text-white/40">
                          {debugRef.current.join("\n") || "No entries"}
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>

              {/* ACTIVITY_MESSAGES 分阶段提示 */}
              {status === "checking" && (
                <div className="mt-4 flex items-center gap-2.5 tabular-nums">
                  <span className="text-white/50">
                    {ACTIVITY_MESSAGES[Math.min(Math.floor(attempt / 15), ACTIVITY_MESSAGES.length - 1)]}
                  </span>
                  <span className="inline-flex w-5 shrink-0 gap-[2px]">
                    {[0, 1, 2].map((n) => (
                      <motion.span
                        key={n}
                        className="inline-block h-[3px] w-[3px] rounded-full bg-white/50"
                        animate={{ opacity: [0.15, 1, 0.15] }}
                        transition={{ duration: 1.2, repeat: Infinity, delay: n * 0.2 }}
                      />
                    ))}
                  </span>
                  <div className="ml-1 h-[2px] w-[100px] shrink-0 overflow-hidden rounded-sm relative bg-white/[0.04]">
                    <div
                      className="absolute left-0 top-0 h-full rounded-sm transition-all duration-400 bg-gradient-to-r from-cyan-500 to-amber-500"
                      style={{ width: `${Math.min((attempt / MAX_RETRIES) * 100, 95)}%` }}
                    />
                    <motion.div
                      className="absolute top-[-1px] bottom-[-1px] w-[30px]"
                      style={{ background: "linear-gradient(90deg, transparent, rgba(227,182,80,0.5), transparent)" }}
                      animate={{ left: ["-30px", "calc(100% + 30px)"] }}
                      transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                    />
                  </div>
                  <span className="ml-auto font-mono text-[11px] text-white/40">
                    {formatTime(attempt)}
                  </span>
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
              <div className="flex items-start gap-2">
                <Database className="size-4 text-cyan-400 mt-0.5 shrink-0" />
                <span className="text-white/70">启动后端后，所有页面将显示真实市场数据</span>
              </div>
              <div className="flex items-start gap-2">
                <Activity className="size-4 text-emerald-400 mt-0.5 shrink-0" />
                <span className="text-white/70">WebSocket 实时推送订单簿/大单流/持仓</span>
              </div>
              <div className="flex items-start gap-2">
                <Wifi className="size-4 text-amber-400 mt-0.5 shrink-0" />
                <span className="text-white/70">MOCK 徽章自动消失</span>
              </div>
            </div>
          </motion.div>
        </section>

        {/* CTA */}
        <section className="max-w-3xl mx-auto px-6 py-12 text-center">
          <Link
            href="/system/dash"
            className="inline-flex items-center gap-2 rounded-full bg-white text-black px-8 py-3 font-semibold hover:bg-white/80 transition-colors shadow-[0_0_32px_rgba(255,255,255,0.15)]"
          >
            先进平台看看 MOCK 界面
            <ArrowRight className="size-4" />
          </Link>
          <p className="mt-4 text-xs text-white/40">
            平台内的橙色 MOCK 徽章会在后端连接后消失
          </p>
        </section>
      </div>
    </div>
  );
}
