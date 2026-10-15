"use client";

import { useMemo } from "react";
import { useApi } from "@/lib/hooks/use-api";
import type { PageMeta } from "@/lib/page-manifest";
import { formatCellValue, humanizeKey, inferDomain, isDynamicPage, unwrapRows } from "@/lib/renderer-config";
import { DynamicPlaceholder } from "./dynamic-placeholder";
import { SkeletonRows } from "./skeleton";

// Chat 型页面(system 场景):REST → 对话/状态卡片渲染
// 适用:dash / notifications / memory / marketplace / agent
// 增强:status 字段 → 彩色 pill 徽章;含 timestamp+message 字段时渲染时间线布局。

function statusBadge(v: unknown): { text: string; cls: string } | null {
  if (typeof v === "boolean") return { text: v ? "on" : "off", cls: v ? "bg-pos/10 text-pos" : "bg-neg/10 text-neg" };
  if (typeof v !== "string") return null;
  const good = /^(on|ok|success|running|active|done|ready|up|open|completed|healthy)$/i.test(v);
  const bad = /^(off|error|failed|down|closed|stopped|unhealthy)$/i.test(v);
  return { text: v, cls: good ? "bg-pos/10 text-pos" : bad ? "bg-neg/10 text-neg" : "bg-surface-3 text-ink-3" };
}

function formatTimestamp(v: unknown): string {
  const s = String(v ?? "");
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 16).replace("T", " ");
  return s.slice(0, 24);
}

// 判定是否有时间线字段(时间戳 + 内容字段)
function hasTimelineFields(o: Record<string, unknown>): boolean {
  const timeKey = ["timestamp", "time", "created_at", "createdAt", "date"].find((k) => k in o);
  const msgKey = ["message", "text", "content", "body", "summary"].find((k) => k in o);
  return Boolean(timeKey && msgKey);
}

function TimelineRow({ o, timeKey, msgKey }: { o: Record<string, unknown>; timeKey: string; msgKey: string }) {
  const badge = statusBadge(o.status);
  return (
    <div className="relative flex gap-3 pb-3">
      {/* 时间轴竖线 */}
      <div className="flex flex-col items-center">
        <div className="mt-1 h-2 w-2 rounded-full bg-surface-3 ring-1 ring-border-soft" />
        <div className="w-px flex-1 bg-border-soft" />
      </div>
      <div className="flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[9px] text-ink-4">{formatTimestamp(o[timeKey])}</span>
          {badge && (
            <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${badge.cls}`}>{badge.text}</span>
          )}
        </div>
        <div className="mt-0.5 text-[10px] text-ink break-all">
          {formatCellValue(o[msgKey])}
        </div>
        {/* 其余键折叠到一行摘要 */}
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] text-ink-3">
          {Object.entries(o)
            .filter(([k]) => k !== timeKey && k !== msgKey && k !== "status")
            .slice(0, 6)
            .map(([k, v]) => (
              <span key={k} className="truncate">
                {humanizeKey(k)}:{formatCellValue(v)}
              </span>
            ))}
        </div>
      </div>
    </div>
  );
}

function CardList({ entries }: { entries: Array<[string, unknown]> }) {
  return (
    <div className="space-y-2">
      {entries.map(([k, v]) => {
        const badge = statusBadge(v);
        return (
          <div key={k} className="border border-border-soft bg-surface rounded p-2">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-medium text-ink-2">{humanizeKey(k)}</span>
              {badge && (
                <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${badge.cls}`}>{badge.text}</span>
              )}
            </div>
            {!badge && (
              <div className="mt-1 text-[10px] text-ink break-all">
                {typeof v === "object" ? JSON.stringify(v, null, 2).slice(0, 200) : String(v ?? "")}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// 无时间线字段的表行 → 紧凑卡片(标题 + 状态 pill + 前 8 键摘要)
function CompactRow({ r, i }: { r: Record<string, unknown>; i: number }) {
  const badge = statusBadge(r.status);
  return (
    <div className="mb-2 border border-border-soft bg-surface rounded p-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-medium text-ink-2">
          {String(r.id ?? r.name ?? `item ${i + 1}`)}
        </span>
        {badge && (
          <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${badge.cls}`}>{badge.text}</span>
        )}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] text-ink-3">
        {Object.entries(r)
          .filter(([k]) => k !== "id" && k !== "name" && k !== "status")
          .slice(0, 8)
          .map(([k, v]) => (
            <span key={k} className="truncate">
              {humanizeKey(k)}:{formatCellValue(v)}
            </span>
          ))}
      </div>
    </div>
  );
}

export function ChatPanelPage({ meta }: { meta: PageMeta }) {
  const rest = meta.rest ?? `/api/v1/${meta.slug}`;
  const { data, isLoading } = useApi<Record<string, unknown> | null>(rest);

  const dynamic = isDynamicPage(meta);
  const domain = useMemo(() => inferDomain(meta), [meta]);
  const unwrapped = useMemo(() => unwrapRows(data, domain), [data, domain]);

  const rows = unwrapped.kind === "table" ? unwrapped.rows : [];

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border-soft bg-surface px-4 py-2">
        <div className="flex items-center gap-3">
          <h1 className="text-[13px] font-medium text-ink">{meta.title}</h1>
          <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">
            {meta.origin === "design" ? "design" : "backend"}
          </span>
          {meta.rest && <code className="text-[9px] text-ink-4">{meta.rest}</code>}
        </div>
      </header>
      <div className="flex-1 overflow-auto bg-bg p-3">
        {dynamic ? (
          <DynamicPlaceholder rest={rest} />
        ) : isLoading ? (
          <SkeletonRows rows={4} />
        ) : unwrapped.kind === "table" && rows.length > 0 ? (
          <div className="max-w-2xl">
            {rows.slice(0, 50).map((r, i) =>
              hasTimelineFields(r) ? (
                <TimelineRow
                  key={i}
                  o={r}
                  timeKey={["timestamp", "time", "created_at", "createdAt", "date"].find((k) => k in r) ?? "timestamp"}
                  msgKey={["message", "text", "content", "body", "summary"].find((k) => k in r) ?? "message"}
                />
              ) : (
                <CompactRow key={i} r={r} i={i} />
              ),
            )}
          </div>
        ) : unwrapped.kind === "kv" && unwrapped.entries.length > 0 ? (
          <CardList entries={unwrapped.entries} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <p className="text-[11px] text-ink-3">暂无状态数据</p>
            <p className="text-[9px] text-ink-4">{rest}</p>
          </div>
        )}
      </div>
    </div>
  );
}
