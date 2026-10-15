"use client";

import { useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { useApi } from "@/lib/hooks/use-api";
import type { PageMeta } from "@/lib/page-manifest";
import { formatCellValue, humanizeKey, inferDomain, isDynamicPage, unwrapRows } from "@/lib/renderer-config";
import { SkeletonRows } from "./skeleton";

// 通用详情页:fate:"detail" 且 rest 带 {param} 占位符的页到此渲染。
// 参数来源 = URL query:优先占位符同名键(task_id=xxx),回退 id/symbol/exchange 常用别名。
// 数据 = rest 填参 → useApi → unwrapRows 复用列表页解包(dict → 键值,数组 → 表格)。
// 缺参数时显示"从列表进入"提示(母页列表行后续接入跳转)。

const PARAM_ALIASES = ["id"] as const;

function resolveParams(meta: PageMeta, query: URLSearchParams): Record<string, string> {
  const placeholders = [...new Set((meta.rest ?? "").match(/{([^}]+)}/g)?.map((s) => s.slice(1, -1)) ?? [])];
  const resolved: Record<string, string> = {};
  for (const name of placeholders) {
    const value = query.get(name) ?? (PARAM_ALIASES.includes(name as "id") ? query.get("id") : null);
    if (value) resolved[name] = value;
  }
  return resolved;
}

function buildUrl(rest: string, params: Record<string, string>): string {
  return rest.replace(/{([^}]+)}/g, (_, name: string) => encodeURIComponent(params[name] ?? ""));
}

function KVRows({ data, domain }: { data: Record<string, unknown>; domain: ReturnType<typeof inferDomain> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return <p className="p-3 text-[10px] text-ink-4">空记录</p>;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 p-3 text-[10px] sm:grid-cols-3">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-baseline gap-2">
          <dt className="shrink-0 text-ink-3">{humanizeKey(k)}</dt>
          <dd className="break-all text-ink">{formatCellValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ArrayRows({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const r of rows.slice(0, 50)) {
      for (const k of Object.keys(r)) {
        if (!seen.has(k)) {
          seen.add(k);
          out.push(k);
        }
      }
    }
    return out.slice(0, 12);
  }, [rows]);
  return (
    <table className="w-full text-[10px]">
      <thead className="bg-surface-2 text-ink-3">
        <tr>
          {columns.map((k) => (
            <th key={k} className="px-2 py-1 text-left">
              {humanizeKey(k)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-border-soft">
            {columns.map((k) => (
              <td key={k} className="px-2 py-1 text-ink-2">
                {formatCellValue(r[k])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function DetailPageView({ meta }: { meta: PageMeta }) {
  const params = useSearchParams();
  const rest = meta.rest ?? `/api/v1/${meta.slug}`;
  const resolved = useMemo(() => resolveParams(meta, params), [meta, params]);
  const missing = useMemo(
    () =>
      [...new Set((rest.match(/{([^}]+)}/g) ?? []).map((s) => s.slice(1, -1)))].filter(
        (name) => !(name in resolved),
      ),
    [rest, resolved],
  );
  const url = Object.keys(resolved).length > 0 ? buildUrl(rest, resolved) : rest;
  const ready = isDynamicPage(meta) ? missing.length === 0 : true;
  const { data, isLoading } = useApi<unknown>(ready ? url : null);
  const domain = inferDomain(meta);
  const unwrapped = useMemo(() => unwrapRows(data, domain), [data, domain]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border-soft bg-surface px-4 py-2">
        <div className="flex items-center gap-3">
          <h1 className="text-[13px] font-medium text-ink">{meta.title}</h1>
          {Object.keys(resolved).length > 0 && (
            <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">
              {Object.entries(resolved)
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ")}
            </span>
          )}
          {meta.rest && <code className="text-[9px] text-ink-4">{rest}</code>}
        </div>
      </header>
      <div className="flex-1 overflow-auto bg-bg">
        {missing.length > 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <p className="text-[11px] text-ink-3">请从列表进入,或在 URL 带上 ?{missing[0]}=</p>
            <p className="text-[9px] text-ink-4">{rest}</p>
          </div>
        ) : isLoading ? (
          <SkeletonRows rows={6} />
        ) : unwrapped.kind === "kv" ? (
          unwrapped.entries.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2">
              <p className="text-[11px] text-ink-3">暂无数据</p>
              <p className="text-[9px] text-ink-4">{url}</p>
            </div>
          ) : (
            <KVRows data={Object.fromEntries(unwrapped.entries)} domain={domain} />
          )
        ) : unwrapped.rows.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <p className="text-[11px] text-ink-3">暂无数据</p>
            <p className="text-[9px] text-ink-4">{url}</p>
          </div>
        ) : (
          <ArrayRows rows={unwrapped.rows} />
        )}
      </div>
    </div>
  );
}
