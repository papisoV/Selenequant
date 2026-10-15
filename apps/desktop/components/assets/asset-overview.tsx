"use client";

import { useMemo } from "react";
import { useAssetsStore } from "@/lib/store/assets";
import { DEMO_BALANCES, DEMO_PRICE_MAP } from "@/lib/assets/demo-data";
import { aggregate, totalUsd } from "@/lib/assets/aggregate";
import { ExchangeTabs } from "./exchange-tabs";
import { TotalCard } from "./total-card";
import { AssetTable } from "./asset-table";
import { AuthGate } from "./auth-gate";

export function AssetOverview() {
  const mode = useAssetsStore((s) => s.mode);
  const activeExchange = useAssetsStore((s) => s.activeExchange);
  const setMode = useAssetsStore((s) => s.setMode);

  const rows = useMemo(() => {
    if (mode !== "demo") return [];
    return aggregate(DEMO_BALANCES.filter((r) => r.exchange === activeExchange));
  }, [mode, activeExchange]);

  const total = useMemo(() => totalUsd(rows, DEMO_PRICE_MAP), [rows]);

  return (
    <div className="space-y-2">
      <ExchangeTabs />
      {mode === "empty" && <AuthGate onDemo={() => setMode("demo")} />}
      {mode === "demo" && (
        <div className="flex items-center justify-between">
          <span className="feed-demo-badge">演示</span>
          <button
            onClick={() => setMode("empty")}
            className="rounded border border-border-soft px-2 py-0.5 text-[9px] text-ink-3 hover:bg-surface-3"
          >
            返回
          </button>
        </div>
      )}
      {(mode === "demo" || mode === "live") && (
        <>
          <TotalCard totalUsd={total} count={rows.length} />
          <AssetTable rows={rows} />
        </>
      )}
    </div>
  );
}
