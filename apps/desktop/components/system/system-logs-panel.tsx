"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card } from "@/components/shared/page-filter-bar";
import { getSystemLogs, type SystemLogRow } from "@/lib/api/system-logs";
import { useUiText } from "@/components/i18n/use-ui-text"

const LEVEL_FILTERS = ["", "debug", "info", "warning", "error"];

function levelColor(level?: string): string {
  if (!level) return "text-ink-3";
  const l = level.toLowerCase();
  if (l === "error") return "text-destructive";
  if (l === "warning" || l === "warn") return "text-gold";
  if (l === "info") return "text-sky-400";
  if (l === "debug") return "text-ink-4";
  return "text-ink-3";
}

function fmtTime(ts: number | string | undefined): string {
  if (ts == null) return "—";
  const n = typeof ts === "string" ? parseFloat(ts) : ts;
  if (!Number.isFinite(n)) return "—";
  return new Date(n * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

export function SystemLogsPanel() {
  const tr = useUiText()

  const [level, setLevel] = useState("");
  const key = `/api/v1/system/logs?limit=200${level ? `&level=${level}` : ""}`;
  const { data, isLoading } = useSWR(key, () => getSystemLogs(200, level || undefined), {
    refreshInterval: 15000,
  });
  const rows: SystemLogRow[] = data ?? [];

  return (
    <V17Card title={tr("系统日志 ({n})", { n: rows.length })}>
      <div className="flex gap-1 mb-2 flex-wrap">
        {LEVEL_FILTERS.map((l) => (
          <button
            key={l || "all"}
            onClick={() => setLevel(l)}
            className={`rounded px-1.5 py-0.5 text-[9px] ${
              level === l
                ? "bg-sky-500/20 text-sky-400"
                : "bg-surface-3 text-ink-3 hover:bg-surface-4"
            }`}
          >
            {l || tr("全部")}
          </button>
        ))}
      </div>
      <div className="max-h-72 overflow-auto">
        {isLoading && rows.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-3 animate-pulse">{tr("加载中…")}</div>
        )}
        {!isLoading && rows.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无日志")}</div>
        )}
        {rows.map((r, i) => (
          <div
            key={r.id ?? i}
            className="flex items-start gap-2 text-[10px] py-1 border-b border-border-soft"
          >
            <span className="text-ink-4 font-mono w-28 flex-shrink-0">{fmtTime(r.timestamp)}</span>
            <span className={`w-14 flex-shrink-0 font-mono ${levelColor(r.level)}`}>
              {r.level ?? "—"}
            </span>
            <span className="w-32 flex-shrink-0 text-ink-3 font-mono truncate" title={r.logger}>
              {r.logger ?? "—"}
            </span>
            <span className="flex-1 text-ink break-all">{r.message ?? "—"}</span>
            {r.module && (
              <span className="text-ink-4 font-mono text-[9px] flex-shrink-0">
                {r.module}:{r.line ?? ""}
              </span>
            )}
          </div>
        ))}
      </div>
    </V17Card>
  );
}
