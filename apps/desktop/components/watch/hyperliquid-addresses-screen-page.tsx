"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw, Filter } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 孤儿页 hyperliquid-addresses-screen → /api/v1/hyperliquid/addresses/screen
// 分类筛选 + 窗口 + 结果表。返回 ScreenedAddress[]。
// 字段: address, classification(elite/inverse/neutral), score(0-100),
//   metrics.{win_rate, profit_factor, realized_pnl, max_drawdown, volume, closed_trades},
//   risk_flags[], copy_signal

interface ScreenedMetrics {
  win_rate?: number;
  profit_factor?: number;
  realized_pnl?: number;
  max_drawdown?: number;
  volume?: number;
  closed_trades?: number;
}
interface ScreenedAddressData {
  address: string;
  classification?: string;
  score?: number;
  metrics?: ScreenedMetrics;
  risk_flags?: string[];
  reasons?: string[];
}
interface HlScreenData {
  results?: ScreenedAddressData[];
  count?: number;
  classification?: string;
  window_days?: number;
  has_more?: boolean;
}

const CLASS_LABEL: Record<string, string> = {
  elite: "精英",
  inverse: "反向",
  neutral: "中性",
};
const CLASS_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "elite", label: "精英" },
  { value: "inverse", label: "反向" },
  { value: "neutral", label: "中性" },
] as const;
const WINDOW_OPTIONS = [
  { value: 7, label: "7d" },
  { value: 30, label: "30d" },
  { value: 90, label: "90d" },
] as const;

async function fetcher<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    if (!res.ok) return null;
    const body = await res.json();
    if (body && typeof body === "object" && "data" in body && "meta" in body) {
      if (body.error) return null;
      return (body.data ?? null) as T;
    }
    return body as T;
  } catch {
    return null;
  }
}

function fmtNum(n: number | undefined): string {
  if (n === undefined || n === null) return "—";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toFixed(0);
}

export function HyperliquidAddressesScreenPage() {
  const tr = useUiText()

  const [classification, setClassification] = useState<string>("all");
  const [windowDays, setWindowDays] = useState<number>(30);
  const [refreshing, setRefreshing] = useState(false);

  const key = `hl-screen-${classification}-${windowDays}`;
  const path = `/api/v1/hyperliquid/addresses/screen?classification=${classification}&window_days=${windowDays}&limit=200`;
  const { data, mutate } = useSWR<HlScreenData | null>(key, () => fetcher<HlScreenData>(path), {
    revalidateOnFocus: false,
    dedupingInterval: 5000,
  });

  const d = data ?? null;
  const results = d?.results ?? [];

  const handleRefresh = () => {
    setRefreshing(true);
    mutate();
    setTimeout(() => setRefreshing(false), 600);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("HL 地址筛选")}
        right={
          <div className="flex items-center gap-1">
            <Filter className="h-3 w-3 text-ink-4" />
            <select
              value={classification}
              onChange={(e) => setClassification(e.target.value)}
              className="h-6 rounded border border-border-soft bg-surface-2 px-1 text-[10px] text-ink-2"
            >
              {CLASS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{tr(opt.label)}</option>
              ))}
            </select>
            <select
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
              className="h-6 rounded border border-border-soft bg-surface-2 px-1 text-[10px] text-ink-2"
            >
              {WINDOW_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{tr(opt.label)}</option>
              ))}
            </select>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
              aria-label="refresh"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("地址总数")} value={String(d?.count ?? results.length)} />
          <StatBlock
            label={tr("精英")}
            value={String(results.filter((r) => r.classification === "elite").length)}
            cls="text-gold"
          />
          <StatBlock
            label={tr("窗口")}
            value={d?.window_days != null ? `${d.window_days}d` : "—"}
          />
        </div>
      </V17Card>
      <V17Card title={tr("筛选结果")}>
        <div className="flex flex-col">
          {results.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          )}
          {results.map((r) => {
            const cls = r.classification ?? "neutral";
            const roi = (r.metrics?.realized_pnl ?? 0) > 0;
            return (
              <div
                key={r.address}
                className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft"
              >
                <span className="w-12 font-medium">{tr(CLASS_LABEL[cls] ?? cls)}</span>
                <span className="flex-1 text-ink-3 font-mono truncate">{r.address.slice(0, 16)}</span>
                <span className="w-8 text-right text-ink-2 font-mono">{r.score ?? 0}</span>
                <span className="w-12 text-right font-mono num text-pos">
                  {(r.metrics?.win_rate ?? 0) > 0
                    ? `${((r.metrics?.win_rate ?? 0) * 100).toFixed(0)}%`
                    : "—"}
                </span>
                <span className="w-12 text-right font-mono num text-gold">
                  {(r.metrics?.profit_factor ?? 0) > 0
                    ? (r.metrics?.profit_factor ?? 0).toFixed(2)
                    : "—"}
                </span>
                <span className="w-14 text-right font-mono num">{fmtNum(r.metrics?.realized_pnl)}</span>
                {roi && <span className="w-4 text-pos">▲</span>}
                {r.risk_flags?.length ? <span className="text-[9px] text-neg">⚠{r.risk_flags.length}</span> : null}
              </div>
            );
          })}
        </div>
        {results.length > 0 && (
          <div className="pt-1.5 border-t border-border-soft text-[9px] text-ink-4 text-center">
            {tr("显示")} {results.length}/{d?.count ?? results.length} {tr("条")}
          </div>
        )}
      </V17Card>
    </div>
  );
}
