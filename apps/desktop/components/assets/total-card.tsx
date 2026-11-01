"use client";

import { StatBlock } from "@/components/shared/page-filter-bar";
import { fmtMoney } from "@/lib/assets/aggregate";
import { useUiText } from "@/components/i18n/use-ui-text";

export function TotalCard({ totalUsd, count }: { totalUsd: number; count: number }) {
  const tr = useUiText();
  return (
    <div className="grid grid-cols-2 gap-2">
      <StatBlock label={tr("总估值 (USD)")} value={fmtMoney(totalUsd)} cls="text-gold" />
      <StatBlock label={tr("资产行数")} value={count} />
    </div>
  );
}
