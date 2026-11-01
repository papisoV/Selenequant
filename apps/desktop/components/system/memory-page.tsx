"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 后端 /api/v1/memory/market-patterns 返回裸 list
// (memory.py:83 — 每项: id / name / symbol / conditions / action / enabled /
//  created_at / hit_count / last_hit_at)
interface MarketPattern {
  id?: string;
  name?: string;
  symbol?: string;
  action?: string;
  enabled?: boolean;
  created_at?: number;
  hit_count?: number;
}

function fmtTs(ms: number | undefined): string {
  if (ms == null) return "—";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

export function MemoryPage() {
  const tr = useUiText()

  const { data } = useApi<MarketPattern[] | null>(
    "/api/v1/memory/market-patterns"
  );
  const patterns = Array.isArray(data) ? data : [];
  const totalHits = patterns.reduce((s, p) => s + (p.hit_count ?? 0), 0);
  const enabledCount = patterns.filter((p) => p.enabled !== false).length;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("市场记忆概览")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("已存储模式")} value={String(patterns.length)} cls="text-gold" />
          <StatBlock label={tr("启用中")} value={String(enabledCount)} cls="text-pos" />
          <StatBlock label={tr("累计命中")} value={String(totalHits)} />
        </div>
      </V17Card>
      <V17Card title={tr("模式列表")}>
        <div className="flex flex-col">
          {patterns.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          )}
          {patterns.map((p, i) => (
            <div key={p.id ?? i} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
              <span className="flex-1 text-ink">{p.name ?? "—"}</span>
              <span className="w-16 text-right text-gold font-mono">{p.symbol ?? "—"}</span>
              <span className="w-14 text-right text-ink-3 font-mono num">{p.hit_count ?? 0} {tr("次")}</span>
              <span className="w-14 text-right text-ink-4 font-mono">{fmtTs(p.created_at)}</span>
              <span className={`w-12 text-right ${p.enabled !== false ? "text-pos" : "text-ink-4"}`}>
                {p.enabled !== false ? tr("启用") : tr("停用")}
              </span>
            </div>
          ))}
        </div>
      </V17Card>
    </div>
  );
}
