"use client";

import { useEffect, useRef, useState } from "react";
import { apiDirect } from "@/lib/api-client";
import { FeatureGate } from "@/components/shared/feature-gate";
import {
  fundingRateSpread,
  basisSpread,
  DEMO_FUNDING_RATES,
  type FundingRateRow,
  type ArbOpportunity,
} from "@/lib/quant/arb";
import { useUiText } from "@/components/i18n/use-ui-text"

export const DEMO_FALLBACK_MS = 8000;

function normalizeRates(data: unknown): FundingRateRow[] {
  if (Array.isArray(data)) {
    return (data as FundingRateRow[]).filter((r) => r && typeof r.rate === "number");
  }
  if (data && typeof data === "object") {
    const o = data as Record<string, unknown>;
    const arr = (o.data ?? o.rates ?? o.rows) as unknown;
    if (Array.isArray(arr)) {
      return (arr as FundingRateRow[]).filter((r) => r && typeof r.rate === "number");
    }
  }
  return [];
}

export function ArbPanel() {
  return (
    <FeatureGate feature="arb_pro" lockMode="replace">
      <ArbPanelContent />
    </FeatureGate>
  );
}

function ArbPanelContent() {
  const tr = useUiText()

  const [tab, setTab] = useState<"funding" | "basis">("funding");
  return (
    <div className="arb-panel flex flex-col gap-2">
      <div className="flex gap-1 border-b border-border-soft pb-1">
        <TabBtn active={tab === "funding"} onClick={() => setTab("funding")}>{tr("资金费率")}</TabBtn>
        <TabBtn active={tab === "basis"} onClick={() => setTab("basis")}>{tr("期现基差")}</TabBtn>
      </div>
      {tab === "funding" ? <FundingTab /> : <BasisTab />}
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-tab-active={active}
      className={`rounded px-2 py-1 text-[10px] transition-colors ${active ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
    >
      {children}
    </button>
  );
}

function FundingTab() {
  const tr = useUiText()

  const [rates, setRates] = useState<FundingRateRow[]>([]);
  const [source, setSource] = useState<"live" | "demo">("live");
  const liveRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      if (!cancelled && !liveRef.current) {
        setRates(DEMO_FUNDING_RATES);
        setSource("demo");
      }
    }, DEMO_FALLBACK_MS);

    apiDirect<unknown>("/api/v1/derivatives/funding-rates/all-exchanges")
      .then((data) => {
        if (cancelled) return;
        const rows = normalizeRates(data);
        if (rows.length > 0) {
          liveRef.current = true;
          setRates(rows);
          setSource("live");
        }
      })
      .catch(() => {
        // 后端断 → demo 兜底(timer 注入)
      });

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  const opportunities: ArbOpportunity[] = fundingRateSpread(rates);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-[9px] text-ink-4">
        {tr("资金费率(8h)套利扫描 · 年化 ≥ 10% 视为可开仓")}
        {source === "demo" && <span className="feed-demo-badge">{tr("演示")}</span>}
      </div>
      {opportunities.length === 0 && (
        <div className="py-4 text-center text-[11px] text-ink-4">{tr("加载费率…")}</div>
      )}
      {opportunities.map((o) => (
        <div key={o.symbol} className="flex items-center gap-2 rounded border border-border-soft px-2 py-1 text-[11px]">
          <span className="w-36 truncate font-medium text-ink">{o.symbol}</span>
          <span className={`w-16 shrink-0 font-mono ${o.rate >= 0 ? "text-pos" : "text-neg"}`}>
            {(o.rate * 100).toFixed(4)}%
          </span>
          <span className={`w-20 shrink-0 font-mono ${o.openable ? "text-gold" : "text-ink-4"}`}>
            {o.annualizedPct}%
          </span>
          <span className="w-14 shrink-0 text-[9px] text-ink-3">{o.side === "short" ? tr("做空吃费") : tr("做多吃费")}</span>
          <span className={`ml-auto rounded px-1.5 py-0.5 text-[9px] ${o.openable ? "bg-pos/20 text-pos" : "bg-surface-2 text-ink-4"}`}>
            {o.openable ? tr("可开仓") : tr("观望")}
          </span>
        </div>
      ))}
    </div>
  );
}

function BasisTab() {
  const tr = useUiText()

  const [spot, setSpot] = useState("65000");
  const [futures, setFutures] = useState("65500");
  const spotN = Number(spot);
  const futN = Number(futures);
  const basis = basisSpread(spotN, futN);

  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("现货价")}
          <input aria-label={tr("现货价")} type="number" value={spot} onChange={(e) => setSpot(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("期货价")}
          <input aria-label={tr("期货价")} type="number" value={futures} onChange={(e) => setFutures(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
      </div>
      <div className="flex items-center gap-2 text-[11px]">
        <span className="text-ink-3">{tr("期现基差")}</span>
        <span className={`font-mono font-medium ${basis >= 0 ? "text-pos" : "text-neg"}`}>
          {basis.toFixed(2)}%
        </span>
        <span className="text-[9px] text-ink-4">{tr("(正=期货升水,可买现货卖期货)")}</span>
      </div>
    </div>
  );
}
