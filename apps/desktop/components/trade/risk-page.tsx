"use client";

import React, { useMemo } from "react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { BalanceCard } from "@/components/trading/balance-card";
import { PNLCurvePanel } from "@/components/trading/pnl-curve-panel";
import { PortfolioBuilderPanel } from "@/components/research/portfolio-builder-panel";
import { SerenityPanel } from "@/components/research/serenity-panel";
import { PositionTableEmbedded, adaptPosition } from "@/components/trading/position-table-embedded";
import type { PositionTab, Position } from "@/components/trading/position-table-embedded";
import { useRiskOverviewStore } from "@/lib/store/risk-overview";
import { usePaperStore } from "@/lib/store/paper";
import { useApi } from "@/lib/hooks/use-api";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { useShallow } from "zustand/react/shallow";
import type { PaperPositionData } from "@/lib/types/paper";
import { useUiText } from "@/components/i18n/use-ui-text"

// ---- dash snapshot types (for RiskSummaryBar) ----
// Aligned with dash-page.tsx + backend phase4.py DashboardSnapshot.
interface DashboardMarketSummary {
  symbol: string;
  last_price: number | null;
  change_24h_pct: number | null;
  volume_24h: number | null;
}
interface DashboardPaperSummary {
  account_id: string;
  equity: number;
  unrealized_pnl: number;
  open_positions: number;
}
interface DashboardTradingSummary {
  mode: string;
  open_orders: number;
  open_positions: number;
  kill_switch: boolean;
}
interface DashboardSignalsSummary {
  total: number;
  high_severity: number;
  medium_severity: number;
  low_severity: number;
}
interface DashboardSnapshot {
  ts: number;
  markets: DashboardMarketSummary[];
  paper: DashboardPaperSummary | null;
  trading: DashboardTradingSummary | null;
  signals: DashboardSignalsSummary | null;
  slow_subcalls: string[];
}

const fmt_pct = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

// ---- RiskSummaryBar (extracted from dash) ----
function RiskSummaryBar() {
  const tr = useUiText()

  const { data } = useApi<DashboardSnapshot>("/api/v1/dashboard");
  const snap = data ?? null;
  const trading = snap?.trading ?? null;
  const paper = snap?.paper ?? null;

  return (
    <V17Card title={tr("风险概览")}>
      <div className="grid grid-cols-3 gap-2">
        <StatBlock label={tr("运行模式")} value={trading?.mode ?? "—"} />
        <StatBlock label="Kill Switch" value={trading?.kill_switch ? "ON" : "OFF"} cls={trading?.kill_switch ? "text-neg" : "text-pos"} />
        <StatBlock label={tr("挂单")} value={trading?.open_orders ?? 0} cls="text-gold" />
        <StatBlock label={tr("账户净值")} value={paper ? `${paper.equity?.toFixed(2) ?? "—"}` : "—"} cls="text-pos" />
        <StatBlock label={tr("未实现盈亏")} value={paper ? `${paper.unrealized_pnl?.toFixed(2) ?? "—"}` : "—"} cls={paper && paper.unrealized_pnl != null && paper.unrealized_pnl >= 0 ? "text-pos" : "text-neg"} />
        <StatBlock label={tr("当前持仓")} value={trading?.open_positions ?? 0} cls="text-pos" />
      </div>
    </V17Card>
  );
}

// ---- LeftSidebar: symbol selector + market snapshot ----
function LeftSidebar() {
  const tr = useUiText()

  const symbol = useRiskOverviewStore((s) => s.symbol);
  const setSymbol = useRiskOverviewStore((s) => s.setSymbol);
  const { data } = useApi<DashboardSnapshot>("/api/v1/dashboard");
  const markets = data?.markets ?? [];

  const TOP_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "UNI"];

  return (
    <div className="flex flex-col gap-2 overflow-auto">
      <V17Card title={tr("标的")}>
        <div className="flex flex-col gap-1">
          {TOP_SYMBOLS.map((s) => (
            <button
              key={s}
              onClick={() => setSymbol(s)}
              className={`text-left px-2 py-1 rounded text-[11px] font-mono transition-colors ${symbol === s ? "bg-gold/20 text-gold" : "text-ink-2 hover:bg-surface-2"}`}
            >
              {s}
            </button>
          ))}
        </div>
      </V17Card>
      <V17Card title={tr("行情快照")}>
        {markets.length === 0 ? (
          <div className="text-[10px] text-ink-3 py-1">{tr("暂无数据")}</div>
        ) : (
          <div className="flex flex-col">
            {markets.slice(0, 10).map((m) => {
              const chgCls = m.change_24h_pct == null ? "text-ink-3" : m.change_24h_pct >= 0 ? "text-pos" : "text-neg";
              return (
                <div key={m.symbol} className="flex items-center justify-between text-[9px] py-0.5 border-b border-border-soft">
                  <span className="text-gold font-mono">{m.symbol}</span>
                  <span className={`font-mono ${chgCls}`}>{fmt_pct(m.change_24h_pct)}</span>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>
    </div>
  );
}

// ---- EmbeddedSLTP (extracted from pos-page) ----
function EmbeddedSLTP() {
  const tr = useUiText()

  const positions = usePaperStore(useShallow((s) => s.positions));
  const realPositions = positions as PaperPositionData[];

  const OPEN = useMemo<Position[]>(() => {
    if (!realPositions || realPositions.length === 0) return [];
    return realPositions.map(adaptPosition);
  }, [realPositions]);

  return (
    <div className="grid grid-cols-2 gap-2 text-[10px]">
      {OPEN.filter((r) => r.pnl).map((r) => (
        <div key={r.sym} className="border-l-2 border-gold pl-2 py-1">
          <div className="flex justify-between">
            <span className="text-gold font-mono">{r.sym}</span>
            <span className={r.cls === "pos" ? "text-pos" : "text-neg"}>{r.pnl} USDT</span>
          </div>
          <div className="grid grid-cols-2 gap-1 mt-1 text-[9px]">
            <div className="bg-surface-2 rounded px-1.5 py-0.5">
              <span className="text-ink-4">SL</span>
              <span className="text-neg ml-1 font-mono">-{(parseFloat(r.avg || "0") * 0.02).toFixed(1)}</span>
            </div>
            <div className="bg-surface-2 rounded px-1.5 py-0.5">
              <span className="text-ink-4">TP</span>
              <span className="text-pos ml-1 font-mono">+{(parseFloat(r.avg || "0") * 0.04).toFixed(1)}</span>
            </div>
          </div>
        </div>
      ))}
      {OPEN.length === 0 && <div className="col-span-2 text-center text-ink-4 text-[11px] py-3">{tr("暂无持仓")}</div>}
    </div>
  );
}

// ---- RiskPage: 3-column layout ----
export function RiskPage() {
  const tr = useUiText()

  const riskTab = useRiskOverviewStore((s) => s.riskTab);
  const setRiskTab = useRiskOverviewStore((s) => s.setRiskTab);
  const positionTab = useRiskOverviewStore((s) => s.positionTab);
  const setPositionTab = useRiskOverviewStore((s) => s.setPositionTab);
  const accountId = useAccountId();

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="shrink-0 px-2 pt-2">
        <RiskSummaryBar />
      </div>
      <div className="flex flex-1 overflow-hidden gap-2 p-2">
        {/* Left: 240px */}
        <div className="w-[240px] shrink-0 overflow-auto">
          <LeftSidebar />
        </div>
        {/* Center: flex */}
        <div className="flex-1 overflow-auto flex flex-col gap-2">
          <div className="flex gap-1 shrink-0">
            <button
              onClick={() => setRiskTab("live")}
              className={`px-3 py-1 rounded text-[11px] font-medium transition-colors ${riskTab === "live" ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
            >
              {tr("实时风险")}
            </button>
            <button
              onClick={() => setRiskTab("portfolio")}
              className={`px-3 py-1 rounded text-[11px] font-medium transition-colors ${riskTab === "portfolio" ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
            >
              {tr("组合风险")}
            </button>
          </div>
          {riskTab === "live" ? (
            <div className="flex flex-col gap-2">
              <PositionTableEmbedded tab={positionTab} onTabChange={setPositionTab} />
              <V17Card title={tr("SL-TP 管理")}><EmbeddedSLTP /></V17Card>
              <V17Card title={tr("账户余额")}><BalanceCard /></V17Card>
              <PNLCurvePanel accountId={accountId} />
            </div>
          ) : (
            <PortfolioBuilderPanel />
          )}
        </div>
        {/* Right: 320px */}
        <div className="w-[320px] shrink-0 overflow-auto">
          <SerenityPanel />
        </div>
      </div>
    </div>
  );
}
