"use client";

import { useMemo, useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import type { PageMeta } from "@/lib/page-manifest";
import { formatCellValue, humanizeKey, inferDomain, isDynamicPage, unwrapRows } from "@/lib/renderer-config";
import { DynamicPlaceholder } from "./dynamic-placeholder";
import { SkeletonRows } from "./skeleton";
import { useUiText } from "@/components/i18n/use-ui-text";

// 列表型页面(trade 场景 / system 部分):REST → 表格渲染
// 适用:pos / paper / trd / grid / tof / live_log / backtests / dex-trading / optimizations / notifications / memory / marketplace / chat-sessions / skill / mcp / set
// 增强:自动解包 dict 数组字段(按内容域配键名)、列排序、文本过滤、分页、CSV 导出、单元格格式化。
// 纯 dict 页 fallback 键值视图;动态页(task_id 等)显示"需从列表进入"。

const PAGE_SIZE = 10;

function KVGrid({ entries }: { entries: Array<[string, unknown]> }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 p-3 text-[10px] sm:grid-cols-3">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-baseline gap-2">
          <dt className="shrink-0 text-ink-3">{humanizeKey(k)}</dt>
          <dd className="truncate text-ink">{formatCellValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function DownloadCsv({ rows }: { rows: Record<string, unknown>[] }) {
  const download = () => {
    const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    const esc = (s: string) => `"${s.replaceAll('"', '""')}"`;
    const lines = [keys.map(esc).join(",")];
    for (const r of rows) lines.push(keys.map((k) => esc(formatCellValue(r[k]))).join(","));
    const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `export-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button
      type="button"
      onClick={download}
      className="rounded bg-surface-3 px-2 py-0.5 text-[9px] text-ink-2 hover:bg-surface-2"
    >
      CSV
    </button>
  );
}

export function DataTablePage({ meta }: { meta: PageMeta }) {
  const tr = useUiText();
  const rest = meta.rest ?? `/api/v1/${meta.slug}`;
  const { data, isLoading } = useApi<unknown>(rest);

  const dynamic = isDynamicPage(meta);

  const domain = useMemo(() => inferDomain(meta), [meta]);
  const unwrapped = useMemo(() => unwrapRows(data, domain), [data, domain]);

  // ---- 表格状态 ----
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);

  const rows = unwrapped.kind === "table" ? unwrapped.rows : [];

  // 过滤 + 排序
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let out = rows;
    if (q) {
      out = rows.filter((r) =>
        Object.values(r).some((v) => formatCellValue(v).toLowerCase().includes(q)),
      );
    }
    if (sortKey) {
      const k = sortKey;
      out = [...out].sort((a, b) => {
        const av = a[k];
        const bv = b[k];
        if (typeof av === "number" && typeof bv === "number") return av - bv;
        return String(av ?? "").localeCompare(String(bv ?? ""), "zh-CN");
      });
      if (sortDir === "desc") out.reverse();
    }
    return out;
  }, [rows, filter, sortKey, sortDir]);

  // 列 = 所有行键的并集(保留首现顺序,截断防爆)
  const columns = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const r of filtered.slice(0, 50)) {
      for (const k of Object.keys(r)) {
        if (!seen.has(k)) {
          seen.add(k);
          out.push(k);
        }
      }
    }
    return out.slice(0, 12);
  }, [filtered]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  const toggleSort = (k: string) => {
    if (sortKey === k) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(k);
      setSortDir("asc");
    }
    setPage(0);
  };

  const renderBody = () => {
    if (dynamic) {
      return (
        <DynamicPlaceholder
          rest={rest}
          params={tr("{title} 是详情页,含动态参数({params})。请先从对应列表页选择记录打开。", {
            title: tr(meta.title),
            params: (rest.match(/{[^}]+}/g) ?? []).join(", "),
          })}
        />
      );
    }
    if (isLoading) return <SkeletonRows rows={6} />;
    if (unwrapped.kind === "kv") {
      return unwrapped.entries.length === 0 ? (
        <div className="flex h-full flex-col items-center justify-center gap-2">
          <p className="text-[11px] text-ink-3">{tr("暂无数据")}</p>
          <p className="text-[9px] text-ink-4">{rest}</p>
        </div>
      ) : (
        <KVGrid entries={unwrapped.entries} />
      );
    }
    // table
    if (rows.length === 0) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-2">
          <p className="text-[11px] text-ink-3">{tr("暂无数据")}</p>
          <p className="text-[9px] text-ink-4">{rest}</p>
        </div>
      );
    }
    return (
      <table className="w-full text-[10px]">
        <thead className="bg-surface-2 text-ink-3">
          <tr>
            {columns.map((k) => (
              <th key={k} className="px-2 py-1 text-left">
                <button
                  type="button"
                  onClick={() => toggleSort(k)}
                  className="flex items-center gap-1 hover:text-ink"
                >
                  {humanizeKey(k)}
                  {sortKey === k && <span className="text-[8px] text-ink-2">{sortDir === "asc" ? "▲" : "▼"}</span>}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {pageRows.map((r, i) => (
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
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border-soft bg-surface px-4 py-2">
        <div className="flex items-center gap-3">
          <h1 className="text-[13px] font-medium text-ink">{tr(meta.title)}</h1>
          <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">
            {meta.origin === "design" ? "design" : "backend"}
          </span>
          {meta.rest && (
            <code className="text-[9px] text-ink-4">{meta.rest}</code>
          )}
        </div>
        {!dynamic && unwrapped.kind === "table" && rows.length > 0 && (
          <div className="flex items-center gap-2">
            {filtered.length > 0 && (
              <span className="text-[9px] text-ink-3">{filtered.length} {tr("条")}</span>
            )}
            <DownloadCsv rows={filtered} />
          </div>
        )}
      </header>
      <div className="flex-1 overflow-auto bg-bg">
        {/* 工具条:过滤 + 分页 */}
        {!dynamic && unwrapped.kind === "table" && rows.length > 0 && (
          <div className="flex items-center justify-between border-b border-border-soft px-3 py-1.5">
            <input
              type="text"
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value);
                setPage(0);
              }}
              placeholder={tr("过滤…")}
              className="w-40 rounded border border-border-soft bg-surface px-2 py-0.5 text-[10px] text-ink outline-none placeholder:text-ink-4"
            />
            <div className="flex items-center gap-2 text-[9px] text-ink-3">
              <button
                type="button"
                disabled={safePage === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                className="rounded bg-surface-3 px-2 py-0.5 hover:bg-surface-2 disabled:opacity-40"
              >
                ←
              </button>
              <span>
                {safePage + 1}/{pageCount}
              </span>
              <button
                type="button"
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                className="rounded bg-surface-3 px-2 py-0.5 hover:bg-surface-2 disabled:opacity-40"
              >
                →
              </button>
            </div>
          </div>
        )}
        {renderBody()}
      </div>
    </div>
  );
}
