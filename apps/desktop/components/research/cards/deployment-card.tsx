"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { DeploymentSummary, DeploymentFlags } from "@/lib/api/strategies";
import {
  pauseDeployment,
  resumeDeployment,
  stopDeployment,
  cloneStrategy,
  exportStrategy,
  updateDeploymentFlags,
} from "@/lib/api/strategies";
import { useUiText } from "@/components/i18n/use-ui-text"

interface Props {
  d: DeploymentSummary;
  onChanged?: () => void;
}

type Action = "pause" | "resume" | "stop" | "clone" | "export" | "flag_ai" | "flag_bias";

// US-3 状态圆点 pill(对齐 QD:6px 圆点+状态字)。后端实际只产 running/paused/stopped;
// starting/stopping 是历史设计残留,留作防御性默认(走灰)。
// P2 颜色归一:平台语义 token pos/warn/neg(不随 locale 翻转的状态色用 warn;
// destructive 语义见 globals.css --color-destructive,涨跌翻转见 pos/neg 注记)。
const STATUS_STYLE: Record<string, { dot: string; text: string; label: string }> = {
  running: { dot: "bg-pos", text: "text-pos", label: "运行中" },
  paused: { dot: "bg-warn", text: "text-warn", label: "暂停" },
  stopped: { dot: "bg-destructive", text: "text-destructive", label: "已停止" },
  starting: { dot: "bg-ink-3", text: "text-ink-3", label: "启动中" },
  stopping: { dot: "bg-ink-3", text: "text-ink-3", label: "停止中" },
};

export function DeploymentCard({ d, onChanged }: Props) {
  const tr = useUiText()

  const router = useRouter();
  const [busy, setBusy] = useState<Action | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 从 d.flags 回填真实值(list 端点注入);硬编码 false 会让"后端开着 UI 显示没开"。
  const [aiGate, setAiGate] = useState(() => d.flags?.ai_gate_enabled ?? false);
  const [biasGuard, setBiasGuard] = useState(() => d.flags?.bias_guard_enabled ?? false);
  // 列表刷新(SWR mutate)带来新 d 时同步,保持 UI 与后端一致。
  useEffect(() => {
    setAiGate(d.flags?.ai_gate_enabled ?? false);
    setBiasGuard(d.flags?.bias_guard_enabled ?? false);
  }, [d.id, d.flags?.ai_gate_enabled, d.flags?.bias_guard_enabled]);

  async function run(action: Action, fn: () => Promise<void>) {
    setBusy(action);
    setErr(null);
    try {
      await fn();
      onChanged?.();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function toggleFlag(flag: "ai_gate_enabled" | "bias_guard_enabled", value: boolean) {
    const action: Action = flag === "ai_gate_enabled" ? "flag_ai" : "flag_bias";
    setBusy(action);
    setErr(null);
    try {
      const body: DeploymentFlags = { [flag]: value };
      const r = await updateDeploymentFlags(d.strategy_id, body);
      if (flag === "ai_gate_enabled") setAiGate(r.flags?.ai_gate_enabled ?? value);
      else setBiasGuard(r.flags?.bias_guard_enabled ?? value);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function runExport() {
    setBusy("export");
    setErr(null);
    try {
      const text = await exportStrategy(d.strategy_id);
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${d.strategy_name ?? d.strategy_id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  // 停止部署两步确认(vhero-kill 同款词汇):单击 Live/实例停止是不可逆高危动作,
  // 首击进 confirming,3s 内二击执行,超时或 ESC 归位。单击直接杀 = P1 事故温床。
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearStopTimer = () => {
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
  };
  const [confirmingStop, setConfirmingStop] = useState(false);
  // ESC 取消确认态。注意 cleanup 只摘监听不清 timer——此 effect 随 confirmingStop
  // 重跑,cleanup 若 clearStopTimer 会把 handleStopClick 刚设的 3s 归位 timer 杀掉
  // (首击后 confirmingStop 变化 → 重渲染 → cleanup 先于新 timer 触发执行)。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && confirmingStop) {
        setConfirmingStop(false);
        clearStopTimer();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirmingStop]);
  // timer/unmount 清理:组件卸载时兜底。
  useEffect(() => clearStopTimer, []);
  // 部署项变化(d.id)或停止完成后归位,防止 confirming 态串卡。
  useEffect(() => {
    setConfirmingStop(false);
    clearStopTimer();
  }, [d.id]);

  function handleStopClick() {
    if (!confirmingStop) {
      setConfirmingStop(true);
      clearStopTimer();
      stopTimerRef.current = setTimeout(() => setConfirmingStop(false), 3000);
      return;
    }
    clearStopTimer();
    setConfirmingStop(false);
    void run("stop", () => stopDeployment(d.strategy_id));
  }

  const status = d.status ?? "stopped";
  const style = STATUS_STYLE[status] ?? { dot: "bg-surface-3", text: "text-ink-3", label: status };
  const isRunning = status === "running";
  const isPaused = status === "paused";
  const flagDisabled = busy !== null;

  return (
    <div data-testid="strat-card" className="rounded border border-border-soft bg-surface-2 p-2 transition-colors duration-150">
      <div className="flex justify-between items-start">
        <div className="min-w-0">
          <div className="text-[10px] font-medium text-ink truncate">
            {tr(d.strategy_name ?? d.strategy_id)}
          </div>
          <div className="text-[9px] text-gold mt-0.5 font-mono truncate">
            {d.strategy_id}
          </div>
        </div>
        <span
          className={`flex shrink-0 items-center gap-1 text-[9px] ${style.text}`}
          title={status}
        >
          <span
            data-testid="deploy-status-dot"
            className={`h-1.5 w-1.5 rounded-full ${style.dot}`}
          />
          {tr(style.label)}
        </span>
      </div>

      <div className="text-[9px] text-ink-4 mt-1">
        {d.mode ?? "—"} | v{d.strategy_version ?? "?"} | {d.account_id ?? "—"}
      </div>

      {/* US-102 运行时健康:runner slot 有数才显示。null=等 runner 起来(灰);
          errors>0 用 warn 色提醒。bar_count=0 但 status=running 通常是 warmup 中。 */}
      {d.runtime ? (
        <div
          className={`text-[9px] mt-1 font-mono ${d.runtime.errors > 0 ? "text-warn" : "text-ink-3"}`}
          title={`bars=${d.runtime.bar_count} signals=${d.runtime.signals_emitted} errors=${d.runtime.errors}`}
        >
          {d.runtime.bar_count} bars · {d.runtime.signals_emitted} {tr("信号")}
          {d.runtime.errors > 0 ? tr(" · {n} 错误", { n: d.runtime.errors }) : ""}
        </div>
      ) : isRunning ? (
        <div className="text-[9px] text-ink-4 mt-1" title={tr("runner 没有这个部署的 slot(可能还在启动)")}>
          {tr("等待运行时数据…")}
        </div>
      ) : null}

      {/* P0-2a:flags 存于后端模块级内存 dict,与进程同寿命。此前 UI 只给两个
          裸 checkbox,观感是"勾了就永久生效",实际重启即丢 —— 必须明示。 */}
      <div className="text-[9px] text-ink-4 mt-1.5">
        {tr("运行期开关 · 仅对本次运行有效,服务重启后需重设")}
      </div>
      <div className="flex flex-wrap gap-2 mt-1 text-[9px]">
        <label
          className="flex items-center gap-1 text-ink-3 cursor-pointer"
          title={tr("AI Gate:启用后由 AI 对信号做二次放行判断。仅本次运行有效,服务重启后需重设。")}
        >
          <input
            type="checkbox"
            checked={aiGate}
            disabled={flagDisabled}
            onChange={(e) => toggleFlag("ai_gate_enabled", e.target.checked)}
            className="h-3 w-3 accent-[--color-gold]"
          />
          AI Gate
        </label>
        <label
          className="flex items-center gap-1 text-ink-3 cursor-pointer"
          title={tr("Bias Guard:启用后拦截偏离开仓。仅本次运行有效,服务重启后需重设。")}
        >
          <input
            type="checkbox"
            checked={biasGuard}
            disabled={flagDisabled}
            onChange={(e) => toggleFlag("bias_guard_enabled", e.target.checked)}
            className="h-3 w-3 accent-[--color-gold]"
          />
          Bias Guard
        </label>
      </div>

      <div className="flex flex-wrap gap-1 mt-2">
        <button
          onClick={() => router.push(`/research/strategies-strategy-id?id=${encodeURIComponent(d.strategy_id)}`)}
          className="text-[9px] rounded bg-accent/15 text-accent px-1.5 py-1 transition-colors duration-150 hover:bg-accent/25"
        >
          {tr("工作台")}
        </button>
        {isRunning && (
          <button
            disabled={busy !== null}
            onClick={() => run("pause", () => pauseDeployment(d.strategy_id))}
            className="text-[9px] rounded bg-warn/15 text-warn px-1.5 py-1 transition-colors duration-150 hover:bg-warn/25 disabled:opacity-50"
          >
            {busy === "pause" ? "..." : tr("暂停")}
          </button>
        )}
        {isPaused && (
          <button
            disabled={busy !== null}
            onClick={() => run("resume", () => resumeDeployment(d.strategy_id))}
            className="text-[9px] rounded bg-pos/15 text-pos px-1.5 py-1 transition-colors duration-150 hover:bg-pos/25 disabled:opacity-50"
          >
            {busy === "resume" ? "..." : tr("恢复")}
          </button>
        )}
        {(isRunning || isPaused) && (
          <button
            disabled={busy !== null}
            onClick={handleStopClick}
            className={`text-[9px] rounded px-1.5 py-1 transition-colors duration-150 disabled:opacity-50 ${
              confirmingStop
                ? "bg-destructive/40 text-ink animate-pulse"
                : "bg-destructive/15 text-destructive hover:bg-destructive/25"
            }`}
          >
            {busy === "stop" ? "..." : confirmingStop ? tr("确认停止?") : tr("停止")}
          </button>
        )}
        <button
          disabled={busy !== null}
          onClick={() => run("clone", () => cloneStrategy(d.strategy_id))}
          className="text-[9px] rounded bg-surface-3 text-ink-3 px-1.5 py-1 transition-colors duration-150 hover:bg-surface-4 disabled:opacity-50"
        >
          {busy === "clone" ? "..." : tr("克隆")}
        </button>
        <button
          disabled={busy !== null}
          onClick={runExport}
          className="text-[9px] rounded bg-surface-3 text-ink-3 px-1.5 py-1 transition-colors duration-150 hover:bg-surface-4 disabled:opacity-50"
        >
          {busy === "export" ? "..." : tr("导出")}
        </button>
      </div>

      {err && (
        <div className="text-[9px] text-destructive mt-1 truncate" title={err}>
          {err}
        </div>
      )}
    </div>
  );
}
