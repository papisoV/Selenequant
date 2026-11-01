"use client";

import { useCallback, useRef, useState } from "react";
import { useContextStore } from "@/lib/store/context";
import { PageFilterBar, V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useMarketsWs, type MarketMessage } from "@/lib/ws/use-markets-ws";
import { DEFAULT_SYMBOLS as SYMBOLS } from "@/lib/monitor-feed";
import { useUiText } from "@/components/i18n/use-ui-text"

interface TickerData {
  symbol?: string;
  last_price?: number;
  change_24h_pct?: number;
  volume_24h?: number;
  quote_volume_24h?: number;
  high_24h?: number;
  low_24h?: number;
  bid?: number;
  ask?: number;
}

interface MkRow {
  s: string;
  price: number | null;
  chgPct: number | null;
  vol: number | null;
}

// 旧代码走 WS /api/v1/ws/markets 订阅 ticker 推送（旧代码 use-market-ws.ts:180）
// REST /api/v1/markets/{sym}/ticker 现拉 Binance，1008 限流时拉不到
// WS 后端有缓存 + 广播，1008 时仍可能推最后缓存值
export function MkPage() {
  const tr = useUiText()

  const { pageFilter } = useContextStore();
  const [tickers, setTickers] = useState<Record<string, TickerData>>({});
  const tickersRef = useRef<Record<string, TickerData>>({});

  const onMessageBatch = useCallback((msgs: MarketMessage[]) => {
    let changed = false;
    const next = { ...tickersRef.current };
    for (const msg of msgs) {
      if (msg.type !== "ticker") continue;
      const d = msg.data as unknown as TickerData;
      const sym = (d.symbol ?? "").split("/")[0].split("-")[0].split(":")[0].toUpperCase();
      if (!sym) continue;
      next[sym] = d;
      changed = true;
    }
    if (!changed) return;
    tickersRef.current = next;
    setTickers(next);
  }, []);

  // 订阅 12 个 symbol 的 ticker（WS 走 /api/v1/ws/markets）
  // onMessageBatch:RAF 批处理,每帧合并一次 setState,避免 100ms 级推送逐条重渲染
  useMarketsWs({
    symbols: SYMBOLS.map((s) => `${s}/USDT`),
    exchange: "binance",
    marketType: "futures",
    interval: "1h",
    onMessageBatch,
  });

  const rows: MkRow[] = SYMBOLS.map((s) => {
    const t = tickers[s];
    if (!t) return { s, price: null, chgPct: null, vol: null };
    return {
      s,
      price: t.last_price ?? null,
      chgPct: t.change_24h_pct != null ? t.change_24h_pct : null,
      vol: t.volume_24h ?? null,
    };
  });

  const validRows = rows.filter((r) => r.price != null);
  const gainers = validRows.filter((r) => (r.chgPct ?? 0) > 0).length;
  const losers = validRows.length - gainers;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("行情总览")}>
        <PageFilterBar tfs={["1h", "4h", "24h"]} showSym={false} />
        <div className="flex flex-col mt-2">
          <div className="grid grid-cols-[80px_1fr_1fr_1fr_1fr] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>SYMBOL</span>
            <span className="text-right">PRICE</span>
            <span className="text-right">24H CHG</span>
            <span className="text-right">VOL (M)</span>
            <span className="text-right">TREND</span>
          </div>
          {validRows.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">
              {tr("等待 WebSocket 推送行情数据…")}
            </div>
          )}
          {rows.map((r) => {
            const chg = r.chgPct;
            const chgColor = chg == null ? "text-ink-4" : chg >= 0 ? "text-pos" : "text-neg";
            const arrow = chg == null ? "—" : chg > 1 ? "↑↑" : chg > 0 ? "↑" : chg < -1 ? "↓↓" : "↓";
            return (
              <div
                key={r.s}
                className="grid grid-cols-[80px_1fr_1fr_1fr_1fr] gap-2 text-[10px] py-1 border-b border-border-soft items-center"
              >
                <span className="text-gold font-mono">{r.s}</span>
                <span className="num text-right text-ink font-mono">
                  {r.price == null ? "—" : r.price < 1 ? r.price.toFixed(4) : r.price.toLocaleString()}
                </span>
                <span className={`num text-right font-mono ${chgColor}`}>
                  {chg == null ? "—" : `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`}
                </span>
                <span className="num text-right text-ink-3 font-mono">
                  {r.vol == null ? "—" : r.vol.toFixed(0)}
                </span>
                <span className={`text-right ${chgColor}`}>{arrow}</span>
              </div>
            );
          })}
        </div>
      </V17Card>
      <V17Card title={tr("市场统计")}>
        <div className="grid grid-cols-4 gap-2">
          <StatBlock label={tr("上涨")} value={gainers} cls="text-pos" />
          <StatBlock label={tr("下跌")} value={losers} cls="text-neg" />
          <StatBlock label={tr("总交易量")} value={validRows.length > 0 ? `$${validRows.reduce((s, r) => s + (r.vol ?? 0), 0).toFixed(0)}M` : "—"} />
          <StatBlock label={tr("最大涨幅")} value={validRows.length > 0 ? `+${Math.max(...validRows.map((r) => r.chgPct ?? 0)).toFixed(2)}%` : "—"} cls="text-pos" />
        </div>
      </V17Card>
    </div>
  );
}
