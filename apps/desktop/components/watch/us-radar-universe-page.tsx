"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useMarketStore } from "@/lib/store/market";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// US 宇宙 — /api/v1/us-radar/universe
// shape: { universe[], by_kind{}, total, available_kinds[], timestamp }
// item: { symbol, name, kind, sector, price, change_pct, volume }

interface UniverseItem {
  symbol?: string;
  name?: string;
  kind?: string;
  sector?: string;
  price?: number;
  change_pct?: number;
  volume?: number;
}
interface UniverseData {
  universe?: UniverseItem[];
  by_kind?: Record<string, UniverseItem[]>;
  total?: number;
  available_kinds?: string[];
  timestamp?: number;
}

const KIND_LABEL: Record<string, string> = {
  index_etf: "指数 ETF",
  sector_etf: "行业 ETF",
  single_stock: "个股",
};

const bare = (s: string): string => s.replace(/[-/:].*$/, "");

function fmtNum(n?: number): string {
  if (n === undefined || n === null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toFixed(2);
}

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function UniversePage() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, isLoading, mutate } = useApi<UniverseData | null>("/api/v1/us-radar/universe");
  const [refreshing, setRefreshing] = useState(false);
  const { setSymBase } = useMarketStore();
  const d = data ?? null;

  const items = d?.universe ?? [];
  const byKind = Object.entries(d?.by_kind ?? {});

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const openSymbol = (sym: string) => {
    setSymBase(bare(sym));
    window.location.href = "/watch/us-radar-symbols-symbol";
  };

  const renderRows = (list: UniverseItem[]) =>
    list.length === 0 ? (
      <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
    ) : (
      list.map((u) => {
        const pct = u.change_pct ?? 0;
        return (
          <button
            key={u.symbol}
            type="button"
            onClick={() => openSymbol(u.symbol ?? "")}
            className="flex items-center gap-2 border-b border-border-soft py-1 text-left text-[10px] hover:bg-surface-2"
          >
            <span className="w-14 truncate font-mono font-medium text-gold">{u.symbol}</span>
            <span className="flex-1 truncate text-ink-3">{u.name}</span>
            <span className="hidden w-16 truncate text-[9px] text-ink-4 md:inline" title={u.sector}>{u.sector}</span>
            <span className="w-14 text-right font-mono num text-ink">{u.price?.toFixed(2) ?? "—"}</span>
            <span className={`w-12 text-right font-mono num ${pct >= 0 ? "text-pos" : "text-neg"}`}>
              {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
            </span>
            <span className="hidden w-14 text-right font-mono text-[9px] text-ink-4 md:inline">{fmtNum(u.volume)}</span>
          </button>
        );
      })
    );

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("US 研究宇宙")}
        right={
          <div className="flex items-center gap-2">
            <span className="text-[9px] text-ink-4 font-mono">{fmtTime(d?.timestamp)}</span>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || isLoading}
              aria-label={tr("刷新")}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={tr("标的")} value={String(d?.total ?? items.length)} />
          {byKind.map(([kind, list]) => (
            <StatBlock key={kind} label={tr(KIND_LABEL[kind] ?? kind)} value={String(list.length)} cls="text-gold" />
          ))}
        </div>
      </V17Card>

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
        {byKind.length === 0 ? (
          <div className="col-span-3">
            <V17Card title={tr("全部标的")}>{renderRows(items)}</V17Card>
          </div>
        ) : (
          byKind.map(([kind, list]) => (
            <div key={kind}>
              <V17Card title={`${tr(KIND_LABEL[kind] ?? kind)} (${list.length})`}>{renderRows(list)}</V17Card>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
