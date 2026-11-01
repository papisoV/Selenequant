"use client";

import { useEffect } from "react";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useAlertsStore } from "@/lib/store/alerts";
import { useUiText } from "@/components/i18n/use-ui-text"

export function IntelligenceAlertsPanel() {
  const tr = useUiText()

  const rules = useAlertsStore((s) => s.rules);
  const hydrate = useAlertsStore((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  return (
    <V17Card title={tr("情报告警规则 ({n})", { n: rules.length })}>
      {rules.length === 0 && (
        <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无规则")}</div>
      )}
      <div className="flex flex-col">
        {rules.map((r) => (
          <div
            key={r.id}
            className="flex items-start gap-2 text-[10px] py-1 border-b border-border-soft"
          >
            <span className={`w-10 flex-shrink-0 font-mono ${r.enabled ? "text-pos" : "text-ink-4"}`}>
              {r.enabled ? "ON" : "OFF"}
            </span>
            <span className="w-32 flex-shrink-0 text-ink font-medium">{r.name}</span>
            <span className="w-16 flex-shrink-0 text-ink-3 font-mono">{r.type}</span>
            <span className="flex-1 text-ink-3 break-all">{r.description ?? "—"}</span>
          </div>
        ))}
      </div>
    </V17Card>
  );
}
