"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import {
  INDICATORS,
  INDICATOR_CATEGORY_LABELS,
  type IndicatorCategory,
  type IndicatorMeta,
} from "@/lib/indicators";

export type CategoryFilter = IndicatorCategory | "all";

const CATEGORY_OPTIONS: CategoryFilter[] = [
  "all",
  ...(Object.keys(INDICATOR_CATEGORY_LABELS) as IndicatorCategory[]),
];

const PARAM_COUNT_OPTIONS = [
  { value: "any", label: "任意参数" },
  { value: "1", label: "1 个参数" },
  { value: "2", label: "2+ 参数" },
];

export function IndicatorLibrary({
  onSelect,
  selectedId,
}: {
  onSelect?: (meta: IndicatorMeta) => void;
  selectedId?: string;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [paramCount, setParamCount] = useState("any");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return INDICATORS.filter((m) => {
      if (category !== "all" && m.category !== category) return false;
      if (paramCount === "1" && m.params.length !== 1) return false;
      if (paramCount === "2" && m.params.length < 2) return false;
      if (!q) return true;
      const hay = `${m.name} ${m.nameZh} ${m.description} ${m.id}`.toLowerCase();
      return hay.includes(q);
    });
  }, [query, category, paramCount]);

  return (
    <div className="indicator-library flex flex-col gap-2">
      <input
        aria-label="搜索指标"
        type="text"
        placeholder="搜索模板名/描述…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
      />
      <div className="flex flex-wrap gap-1">
        {CATEGORY_OPTIONS.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => setCategory(c)}
            data-category={c}
            className={cn(
              "rounded px-1.5 py-0.5 text-[9px] transition-colors",
              category === c ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2",
            )}
          >
            {c === "all" ? "全部" : INDICATOR_CATEGORY_LABELS[c]}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1 text-[9px] text-ink-4">
        参数筛选
        <select
          aria-label="参数筛选"
          value={paramCount}
          onChange={(e) => setParamCount(e.target.value)}
          className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
        >
          {PARAM_COUNT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <span className="ml-auto">
          {filtered.length}/{INDICATORS.length}
        </span>
      </div>
      <div className="indicator-grid grid max-h-72 grid-cols-1 gap-1 overflow-auto pr-1">
        {filtered.map((m) => (
          <button
            key={m.id}
            type="button"
            data-indicator-id={m.id}
            data-testid={`indicator-card-${m.id}`}
            onClick={() => onSelect?.(m)}
            className={cn(
              "indicator-card flex flex-col gap-0.5 rounded border border-border-soft px-2 py-1 text-left text-[10px] transition-colors hover:bg-surface-2",
              selectedId === m.id && "border-gold/50 bg-gold/10",
            )}
          >
            <span className="flex items-center gap-1">
              <span className="font-medium text-ink">{m.name}</span>
              <span className="text-ink-4">{m.nameZh}</span>
            </span>
            <span className="flex items-center gap-1 text-[9px] text-ink-4">
              <span className="rounded bg-surface-3 px-1 text-ink-3">{INDICATOR_CATEGORY_LABELS[m.category]}</span>
              <span>{m.interval}</span>
              <span>{m.params.length} 参数</span>
            </span>
          </button>
        ))}
        {filtered.length === 0 && <div className="py-4 text-center text-[10px] text-ink-4">无匹配模板</div>}
      </div>
    </div>
  );
}
