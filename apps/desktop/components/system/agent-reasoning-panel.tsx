"use client";

import useSWR from "swr";
import { V17Card } from "@/components/shared/page-filter-bar";
import { getAgentReasoningRecent, type AgentReasoningRow } from "@/lib/api/agent-reasoning";

function fmtTime(ts: number | string | undefined): string {
  if (ts == null) return "—";
  const n = typeof ts === "string" ? parseFloat(ts) : ts;
  if (!Number.isFinite(n)) return "—";
  return new Date(n * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function roleColor(role?: string): string {
  if (!role) return "text-ink-3";
  const r = role.toLowerCase();
  if (r === "user") return "text-sky-400";
  if (r === "assistant") return "text-emerald-400";
  if (r === "system") return "text-gold";
  return "text-ink-3";
}

export function AgentReasoningPanel() {
  const { data, isLoading } = useSWR(
    "/api/v1/agent/reasoning/recent?limit=100",
    () => getAgentReasoningRecent(100),
    { refreshInterval: 15000 },
  );
  const rows: AgentReasoningRow[] = data ?? [];

  return (
    <V17Card title={`Agent 推理记录 (${rows.length})`}>
      <div className="max-h-72 overflow-auto">
        {isLoading && rows.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-3 animate-pulse">加载中…</div>
        )}
        {!isLoading && rows.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-3">暂无推理记录</div>
        )}
        {rows.map((r, i) => (
          <div
            key={r.id ?? i}
            className="flex items-start gap-2 text-[10px] py-1 border-b border-border-soft"
          >
            <span className="text-ink-4 font-mono w-28 flex-shrink-0">{fmtTime(r.timestamp)}</span>
            <span className={`w-16 flex-shrink-0 font-mono ${roleColor(r.role)}`}>
              {r.role ?? "—"}
            </span>
            <span className="w-16 flex-shrink-0 text-gold font-mono">{r.symbol ?? "—"}</span>
            <span className="flex-1 text-ink break-all line-clamp-2">{r.content ?? "—"}</span>
          </div>
        ))}
      </div>
    </V17Card>
  );
}
