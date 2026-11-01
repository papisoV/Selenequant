"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 业务域插入页 — Q-Evolve 进化
interface QevolveData {
  active_evolution?: string;
  generation?: number;
  best_fitness?: number;
  population_size?: number;
}

export function QevolvePage() {
  const tr = useUiText()

  const { data } = useApi<QevolveData | null>(
    "/api/v1/qevolve/current"
  );
  const d = data ?? null;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("Q-Evolve 概览")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={tr("进化 ID")} value={d?.active_evolution ?? "—"} cls="text-gold" />
          <StatBlock label={tr("代数")} value={String(d?.generation ?? 0)} />
          <StatBlock label={tr("最佳适应度")} value={d?.best_fitness != null ? d.best_fitness.toFixed(2) : "—"} cls="text-pos" />
          <StatBlock label={tr("种群大小")} value={String(d?.population_size ?? 0)} />
        </div>
      </V17Card>
      <V17Card title={tr("进化进度")}>
        <div className="flex items-center gap-2 text-[10px]">
          <span className="text-ink-3">{tr("进度")}</span>
          <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
            <div
              className="h-full bg-gold"
              style={{ width: `${Math.min(((d?.generation ?? 0) / 50) * 100, 100)}%` }}
            />
          </div>
          <span className="text-ink-2 font-mono">
            {Math.min(((d?.generation ?? 0) / 50) * 100, 100).toFixed(0)}%
          </span>
        </div>
      </V17Card>
    </div>
  );
}
