"use client";

import { StatBlock } from "@/components/shared/page-filter-bar";
import { fmtMoney } from "@/lib/assets/aggregate";

export function TotalCard({ totalUsd, count }: { totalUsd: number; count: number }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <StatBlock label="总估值 (USD)" value={fmtMoney(totalUsd)} cls="text-gold" />
      <StatBlock label="资产行数" value={count} />
    </div>
  );
}
