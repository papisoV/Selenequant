"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { V17Card } from "@/components/shared/page-filter-bar";
import { getRecentEvents, type EventRow } from "@/lib/api/events";

// P0-3:ai_action = AI 工具写操作痕迹(save/deploy/stop),由 main.py 回调写入。
const KIND_FILTERS = [
  "",
  "order",
  "fill",
  "error",
  "kill_switch",
  "risk",
  "system",
  "ai_action",
];

// P1-3:ai_action payload 结构化字段 → 对象跳转 chip(与 deployment-card 同一深链契约)。
// 解析失败/字段缺失 → 返回空数组,fail-open 渲染纯文本行(与今天一致)。
export function parseAiActionChips(payloadJson?: string): { label: string; strategyId?: string }[] {
  if (!payloadJson) return [];
  try {
    const p = JSON.parse(payloadJson) as Record<string, unknown>;
    if (!p || typeof p !== "object") return [];
    const chips: { label: string; strategyId?: string }[] = [];
    if (typeof p.strategy_id === "string") chips.push({ label: `策略:${p.strategy_id}`, strategyId: p.strategy_id });
    if (typeof p.account_id === "string") chips.push({ label: `账户:${p.account_id}` });
    if (typeof p.deployment_id === "string") chips.push({ label: `部署:${p.deployment_id}` });
    return chips;
  } catch {
    return [];
  }
}

function severityColor(sev?: string): string {
  if (!sev) return "text-ink-3";
  const s = sev.toLowerCase();
  if (s === "error" || s === "critical") return "text-destructive";
  if (s === "warn" || s === "warning") return "text-gold";
  if (s === "info") return "text-sky-400";
  return "text-ink-3";
}

function fmtTime(ts: number | string | undefined): string {
  if (ts == null) return "—";
  const n = typeof ts === "string" ? parseFloat(ts) : ts;
  if (!Number.isFinite(n)) return "—";
  // backend timestamps are seconds
  return new Date(n * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

export function EventsPanel() {
  const [kind, setKind] = useState("");
  const router = useRouter();
  const key = `/api/v1/events/recent?limit=100${kind ? `&kind=${kind}` : ""}`;
  const { data, isLoading } = useSWR(key, () => getRecentEvents(100, kind || undefined), {
    refreshInterval: 10000,
  });
  const rows = data ?? [];

  return (
    <V17Card title={`事件流 (${rows.length})`}>
      <div className="flex gap-1 mb-2 flex-wrap">
        {KIND_FILTERS.map((k) => (
          <button
            key={k || "all"}
            onClick={() => setKind(k)}
            className={`rounded px-1.5 py-0.5 text-[9px] ${
              kind === k
                ? "bg-sky-500/20 text-sky-400"
                : "bg-surface-3 text-ink-3 hover:bg-surface-4"
            }`}
          >
            {k || "全部"}
          </button>
        ))}
      </div>
      <div className="max-h-72 overflow-auto">
        {isLoading && rows.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-3 animate-pulse">加载中…</div>
        )}
        {!isLoading && rows.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-3">暂无事件</div>
        )}
        {rows.map((r, i) => {
          const chips = r.kind === "ai_action" ? parseAiActionChips(r.payload_json) : [];
          return (
            <div
              key={r.id ?? i}
              className="flex items-start gap-2 text-[10px] py-1 border-b border-border-soft"
            >
              <span className="text-ink-4 font-mono w-28 flex-shrink-0">{fmtTime(r.timestamp)}</span>
              <span className={`w-14 flex-shrink-0 font-mono ${severityColor(r.severity)}`}>
                {r.severity ?? "—"}
              </span>
              <span className="w-16 flex-shrink-0 text-ink-3 font-mono">{r.kind ?? "—"}</span>
              <span className="w-16 flex-shrink-0 text-gold font-mono">{r.symbol ?? "—"}</span>
              <span className="flex-1 text-ink">
                {r.title ?? r.body ?? "—"}
                {chips.length > 0 && (
                  <span className="ml-2 inline-flex flex-wrap gap-1 align-baseline">
                    {chips.map((c) =>
                      c.strategyId ? (
                        <button
                          key={c.label}
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            router.push(
                              `/research/strategies-strategy-id?id=${encodeURIComponent(c.strategyId as string)}`,
                            );
                          }}
                          className="font-mono text-[9px] text-sky-400 hover:text-sky-300 hover:underline underline-offset-2"
                        >
                          {c.label}
                        </button>
                      ) : (
                        <span key={c.label} className="font-mono text-[9px] text-ink-3">
                          {c.label}
                        </span>
                      ),
                    )}
                  </span>
                )}
              </span>
              {r.source && (
                <span className="text-ink-4 font-mono text-[9px] flex-shrink-0">{r.source}</span>
              )}
            </div>
          );
        })}
      </div>
    </V17Card>
  );
}
