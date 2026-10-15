"use client";

import { useMemo } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { useContextStore } from "@/lib/store/context";
import type { PageMeta } from "@/lib/page-manifest";
import { abbrevNumber, humanizeKey, isDynamicPage } from "@/lib/renderer-config";
import { DynamicPlaceholder } from "./dynamic-placeholder";
import { SkeletonRows } from "./skeleton";

// symbol 型页面(watch 大部分):用 pageFilter.symbol 拼 REST path
// 适用:kfi / mk / liqmap / liqratio / gex / agg / tac / sig / vchart / poly / dex / beta / vol_surface / smart_money / onchain / us-radar / regime / cvd / heatmap / liquidations / hyperliquid
// 增强:键值分组(数值/状态/嵌套/其他)、数值着色(正绿负红)、大数字缩写、嵌套对象可读摘要。
// 动态页({task_id} 等非 symbol)显示"需从列表进入"。

type Entry = [string, unknown];

function isNumeric(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function isStatusValue(v: unknown): boolean {
  if (typeof v === "boolean") return true;
  if (typeof v === "string") return /^(on|off|ok|error|failed|success|running|active|done|ready|pending|up|down|open|closed)$/i.test(v);
  return false;
}

// 分组:数值组 / 状态组 / 嵌套对象组 / 其他
function groupEntries(entries: Entry[]): { label: string; entries: Entry[] }[] {
  const numeric: Entry[] = [];
  const status: Entry[] = [];
  const nested: Entry[] = [];
  const misc: Entry[] = [];
  for (const [k, v] of entries) {
    if (isNumeric(v)) numeric.push([k, v]);
    else if (isStatusValue(v)) status.push([k, v]);
    else if (typeof v === "object" && v !== null) nested.push([k, v]);
    else misc.push([k, v]);
  }
  return [
    { label: "数值", entries: numeric },
    { label: "状态", entries: status },
    { label: "数据", entries: nested },
    { label: "其他", entries: misc },
  ].filter((g) => g.entries.length > 0);
}

function statusPill(v: unknown): { text: string; cls: string } | null {
  if (typeof v === "boolean") return { text: v ? "on" : "off", cls: v ? "bg-pos/10 text-pos" : "bg-neg/10 text-neg" };
  if (typeof v === "string") {
    const good = /^(on|ok|success|running|active|done|ready|up|open)$/i.test(v);
    const bad = /^(off|error|failed|down|closed|stop)$/i.test(v);
    return { text: v, cls: good ? "bg-pos/10 text-pos" : bad ? "bg-neg/10 text-neg" : "bg-surface-3 text-ink-3" };
  }
  return null;
}

function numericCls(v: number): string {
  // 只对明显"方向"数值着色:百分比/美元/含 delta/change/pnl 的键
  if (v === 0) return "text-ink";
  return v > 0 ? "text-pos" : "text-neg";
}

// 数值着色:含 percent/rate/ratio 的键显示 % 后缀
const isPctKey = (k: string) => /percent|pct|rate|ratio|_yield|_apy/i.test(k);

function NestedSummary({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-ink-4">[]</span>;
    // 对象数组:摘要长度
    if (typeof value[0] === "object") {
      return <span className="text-ink-2">{value.length} 项</span>;
    }
    return <span className="truncate text-ink-2">{value.slice(0, 4).map(String).join(", ")}</span>;
  }
  if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    const keys = Object.keys(o).slice(0, 3);
    const summary = keys.map((k) => `${k}:${o[k]}`).join(" · ");
    return <span className="truncate text-ink-2">{summary || "{…}"}</span>;
  }
  return <span className="truncate text-ink-2">{String(value)}</span>;
}

// 键值单元格:数值着色缩写 / 状态 pill / 嵌套摘要。pill 只算一次。
function ValueCell({ k, v }: { k: string; v: unknown }) {
  if (isNumeric(v)) {
    return (
      <span className={numericCls(v)}>
        {abbrevNumber(v)}
        {isPctKey(k) ? "%" : ""}
      </span>
    );
  }
  const pill = statusPill(v);
  if (pill) return <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${pill.cls}`}>{pill.text}</span>;
  return <NestedSummary value={v} />;
}

export function SymbolPanelPage({ meta }: { meta: PageMeta }) {
  const { pageFilter } = useContextStore();
  const { symbol } = pageFilter;

  const tpl = meta.rest ?? `/api/v1/${meta.slug}`;
  const path = tpl.replace("{symbol}", symbol).replace("{sym}", symbol);
  const { data, isLoading } = useApi<Record<string, unknown> | null>(path);

  const dynamic = isDynamicPage(meta);

  const obj = data ?? null;
  const entries = useMemo(
    () =>
      obj && typeof obj === "object" && !Array.isArray(obj)
        ? Object.entries(obj).slice(0, 40)
        : [],
    [obj],
  );
  const groups = useMemo(() => groupEntries(entries), [entries]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border-soft bg-surface px-4 py-2">
        <div className="flex items-center gap-3">
          <h1 className="text-[13px] font-medium text-ink">{meta.title}</h1>
          <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">
            {symbol}
          </span>
          <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">
            {meta.origin === "design" ? "design" : "backend"}
          </span>
        </div>
      </header>
      <div className="flex-1 overflow-auto bg-bg p-3">
        {dynamic ? (
          <DynamicPlaceholder rest={path} />
        ) : Array.isArray(obj) ? (
          <div className="space-y-2">
            {obj.map((r, i) => (
              <div key={i} className="border border-border-soft bg-surface rounded p-2">
                <div className="text-[10px] font-medium text-ink">
                  {String(r?.id ?? r?.name ?? r?.symbol ?? r?.address ?? `item ${i + 1}`)}
                </div>
                <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-[9px] sm:grid-cols-3">
                  {Object.entries(r ?? {})
                    .filter(([k]) => k !== "id" && k !== "name" && k !== "symbol" && k !== "address")
                    .slice(0, 10)
                    .map(([k, v]) => (
                      <div key={k} className="flex items-baseline gap-2">
                        <dt className="shrink-0 text-ink-3">{humanizeKey(k)}</dt>
                        <dd className="truncate">
                          <ValueCell k={k} v={v} />
                        </dd>
                      </div>
                    ))}
                </dl>
              </div>
            ))}
          </div>
        ) : entries.length === 0 ? (
          isLoading ? (
            <SkeletonRows rows={4} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2">
              <p className="text-[11px] text-ink-3">{symbol} 暂无数据</p>
              <p className="text-[9px] text-ink-4">{path}</p>
            </div>
          )
        ) : (
          <div className="space-y-3">
            {groups.map((g) => (
              <div key={g.label}>
                <div className="mb-1 text-[9px] font-medium uppercase tracking-wide text-ink-4">
                  {g.label}
                </div>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] sm:grid-cols-3">
                  {g.entries.map(([k, v]) => (
                    <div key={k} className="flex items-baseline gap-2">
                      <dt className="shrink-0 text-ink-3">{humanizeKey(k)}</dt>
                      <dd className="truncate">
                        <ValueCell k={k} v={v} />
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
