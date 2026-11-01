"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw, Search } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 孤儿页 hyperliquid-addresses-address-details → /api/v1/hyperliquid/addresses/{address}/details
// slug 是静态目录拿不到地址参数,页面用输入框让用户填地址。
// 返回 AddressDetails: address, window_days, score{...}, equity_curve[],
//   current_positions[], open_orders[], closed_trades[], portfolio

interface DetailsScore {
  address?: string;
  classification?: string;
  score?: number;
  metrics?: {
    win_rate?: number;
    profit_factor?: number;
    realized_pnl?: number;
    max_drawdown?: number;
    volume?: number;
    closed_trades?: number;
  };
  reasons?: string[];
  risk_flags?: string[];
}
interface PositionData {
  coin?: string;
  szi?: string;
  entry_px?: string | null;
  position_value?: string;
  unrealized_pnl?: string;
}
interface AddressDetailsData {
  address?: string;
  window_days?: number;
  score?: DetailsScore;
  equity_curve?: { time_ms?: number; equity?: number; pnl?: number }[];
  current_positions?: PositionData[];
  open_orders?: unknown[];
  closed_trades?: unknown[];
}

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

export function HyperliquidAddressDetailsPage() {
  const tr = useUiText()

  const [addrInput, setAddrInput] = useState("");
  const [committed, setCommitted] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const key = committed ? `hl-details-${committed}` : null;
  const path = committed
    ? `/api/v1/hyperliquid/addresses/${encodeURIComponent(committed)}/details`
    : null;
  const { data, mutate } = useSWR<AddressDetailsData | null>(
    key,
    () => (path ? fetcher<AddressDetailsData>(path) : null),
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );

  const d = data ?? null;
  const score = d?.score;

  const handleRefresh = () => {
    setRefreshing(true);
    mutate();
    setTimeout(() => setRefreshing(false), 600);
  };

  const submit = () => {
    const v = addrInput.trim();
    if (v) setCommitted(v);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("HL 地址详情")}
        right={
          <div className="flex items-center gap-1">
            <input
              type="text"
              placeholder={tr("输入 HL 地址…")}
              value={addrInput}
              onChange={(e) => setAddrInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
              className="h-6 w-44 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink-2 placeholder:text-ink-4"
            />
            <button
              onClick={submit}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
              aria-label="search"
            >
              <Search className="h-3 w-3" />
            </button>
            {committed && (
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
                aria-label="refresh"
              >
                <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
              </button>
            )}
          </div>
        }
      >
        {!committed ? (
          <div className="py-4 text-center text-[10px] text-ink-4">{tr("输入 HL 地址查看画像")}</div>
        ) : d == null ? (
          <div className="py-4 text-center text-[10px] text-ink-4">{tr("加载中 / 未找到该地址")}</div>
        ) : (
          <div className="space-y-2">
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              <StatBlock label={tr("分类")} value={score?.classification ?? "—"} cls="text-gold" />
              <StatBlock label={tr("评分")} value={score?.score != null ? String(score.score) : "—"} />
              <StatBlock label={tr("窗口")} value={d.window_days != null ? `${d.window_days}d` : "—"} />
            </div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              <StatBlock
                label={tr("胜率")}
                value={(score?.metrics?.win_rate ?? 0) > 0 ? `${((score?.metrics?.win_rate ?? 0) * 100).toFixed(0)}%` : "—"}
                cls="text-pos"
              />
              <StatBlock
                label={tr("盈亏比")}
                value={(score?.metrics?.profit_factor ?? 0) > 0 ? (score?.metrics?.profit_factor ?? 0).toFixed(2) : "—"}
              />
              <StatBlock label={tr("已实现 PnL")} value={fmtNum(score?.metrics?.realized_pnl)} />
            </div>
            {(score?.risk_flags?.length ?? 0) > 0 && (
              <div className="text-[10px] text-neg">
                ⚠ {score?.risk_flags?.join(" · ")}
              </div>
            )}
          </div>
        )}
      </V17Card>
      {committed && d != null && (
        <>
          <V17Card title={tr("当前持仓 ({p0})", { p0: d.current_positions?.length ?? 0 })}>
            {!d.current_positions?.length ? (
              <div className="text-center text-ink-4 text-[11px] py-3">{tr("无持仓")}</div>
            ) : (
              <div className="flex flex-col">
                {d.current_positions.map((p, i) => {
                  const pnl = parseFloat(p.unrealized_pnl ?? "0");
                  return (
                    <div
                      key={`${p.coin}-${i}`}
                      className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft"
                    >
                      <span className="w-12 font-mono">{p.coin}</span>
                      <span className="w-20 text-ink-3 font-mono truncate">{p.szi}</span>
                      <span className="flex-1 text-ink-3 font-mono">{p.entry_px ?? "—"}</span>
                      <span className="w-16 text-right font-mono num">{fmtNum(p.position_value ? parseFloat(p.position_value) : undefined)}</span>
                      <span className={`w-14 text-right font-mono num ${pnl >= 0 ? "text-pos" : "text-neg"}`}>
                        {fmtNum(pnl)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </V17Card>
          <V17Card title={tr("权益曲线 ({p0} 点)", { p0: d.equity_curve?.length ?? 0 })}>
            {!d.equity_curve?.length ? (
              <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无曲线")}</div>
            ) : (
              <div className="flex flex-col">
                {d.equity_curve.slice(-20).reverse().map((pt, i) => (
                  <div
                    key={`${pt.time_ms}-${i}`}
                    className="flex items-center gap-2 text-[10px] py-0.5 border-b border-border-soft"
                  >
                    <span className="flex-1 text-ink-4 font-mono">
                      {pt.time_ms ? new Date(pt.time_ms).toLocaleDateString("en-US") : "—"}
                    </span>
                    <span className="w-20 text-right font-mono num">{fmtNum(pt.equity)}</span>
                    <span className={`w-14 text-right font-mono num ${(pt.pnl ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                      {fmtNum(pt.pnl)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </V17Card>
        </>
      )}
    </div>
  );
}
