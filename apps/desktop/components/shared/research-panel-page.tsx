"use client";

import { useMemo, useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import type { PageMeta } from "@/lib/page-manifest";
import { formatCellValue, humanizeKey, inferDomain, isDynamicPage, unwrapRows } from "@/lib/renderer-config";
import { DynamicPlaceholder } from "./dynamic-placeholder";
import { SkeletonRows } from "./skeleton";
import { useUiText } from "@/components/i18n/use-ui-text";

// 研究型页面(research 场景):REST → 卡片网格渲染
// 适用:rsch / pa / strat / wid / alpha_mine / portfolio / serenity / qevolve / factors
// 增强:卡片搜索(文本跨卡片过滤)、字段选择(勾选哪些键上卡片)、卡片标题优先 name/id/title。
// 动态页(task_id 等)显示"需从列表进入"。

function titleInfo(o: Record<string, unknown>, i: number): { title: string; key: string | null } {
  for (const k of ["name", "id", "title", "symbol", "formula"]) {
    const v = o[k];
    if (v !== undefined && v !== null && v !== "") return { title: String(v), key: k };
  }
  return { title: `item ${i + 1}`, key: null };
}

export function ResearchPanelPage({ meta }: { meta: PageMeta }) {
  const tr = useUiText();
  const rest = meta.rest ?? `/api/v1/${meta.slug}`;
  const { data, isLoading } = useApi<unknown>(rest);

  const dynamic = isDynamicPage(meta);
  const domain = useMemo(() => inferDomain(meta), [meta]);
  const unwrapped = useMemo(() => unwrapRows(data, domain), [data, domain]);

  const [query, setQuery] = useState("");
  const [selectedFields, setSelectedFields] = useState<Set<string> | null>(null);

  const items = unwrapped.kind === "table" ? unwrapped.rows : [];

  // 所有可显示的键(取前 12 个)
  const allFields = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const r of items.slice(0, 30)) {
      for (const k of Object.keys(r)) {
        if (!seen.has(k)) {
          seen.add(k);
          out.push(k);
        }
      }
    }
    return out.slice(0, 12);
  }, [items]);

  // 默认显示前 5 个键;selectedFields 非空则用用户勾选(Set → 数组)
  const visibleFields = selectedFields ? [...selectedFields] : allFields.slice(0, 5);

  // 搜索过滤
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((r) =>
      Object.entries(r).some(([k, v]) =>
        (k.toLowerCase().includes(q) || formatCellValue(v).toLowerCase().includes(q)),
      ),
    );
  }, [items, query]);

  const toggleField = (k: string) => {
    setSelectedFields((prev) => {
      const base = prev ?? new Set(allFields.slice(0, 5));
      const next = new Set(base);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border-soft bg-surface px-4 py-2">
        <div className="flex items-center gap-3">
          <h1 className="text-[13px] font-medium text-ink">{tr(meta.title)}</h1>
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
          <SkeletonRows rows={5} />
        ) : items.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <p className="text-[11px] text-ink-3">{tr("暂无研究数据")}</p>
            <p className="text-[9px] text-ink-4">{rest}</p>
          </div>
        ) : (
          <>
            {/* 搜索 + 字段选择 */}
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={tr("搜索卡片…")}
                className="w-40 rounded border border-border-soft bg-surface px-2 py-0.5 text-[10px] text-ink outline-none placeholder:text-ink-4"
              />
              <div className="flex flex-wrap items-center gap-1">
                {allFields.map((k) => (
                  <label key={k} className="flex cursor-pointer items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-2">
                    <input
                      type="checkbox"
                      checked={visibleFields.includes(k)}
                      onChange={() => toggleField(k)}
                      className="h-2.5 w-2.5"
                    />
                    {humanizeKey(k)}
                  </label>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((o, i) => {
                const { title, key: titleKey } = titleInfo(o, i);
                return (
                  <div
                    key={i}
                    className="border border-border-soft bg-surface rounded p-2"
                  >
                    <div className="text-[10px] font-medium text-ink">{tr(title)}</div>
                    <dl className="mt-1 space-y-0.5 text-[9px]">
                      {visibleFields
                        .filter((k) => k !== titleKey)
                        .map((k) => (
                          <div key={k} className="flex justify-between gap-2">
                            <dt className="shrink-0 text-ink-3">{humanizeKey(k)}</dt>
                            <dd className="truncate text-ink-2">{formatCellValue(o[k], tr)}</dd>
                          </div>
                        ))}
                    </dl>
                  </div>
                );
              })}
              {filtered.length === 0 && (
                <div className="col-span-full text-center text-[10px] text-ink-4 py-6">
                  {tr("无匹配卡片")}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
