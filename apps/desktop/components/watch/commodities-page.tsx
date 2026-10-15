"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card } from "@/components/shared/page-filter-bar";
import { KlineChart } from "@/components/chart/kline-chart";

// 大宗/外汇研究页 — 数据源 AKShare(经 /api/v1/commodities-fx/*)
// 大宗: CN 期货 K 线(分钟/日线),喂 KlineChart 真实 bars
// 外汇: 即期报价卡片(非历史 K 线)
// 数据来源与用途边界: 研究终端 quote/kline,非交易信号(与红线5 同类决策记录)

interface Bar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  hold: number;
  symbol: string;
}

interface FxQuote {
  pair: string;
  bid: number;
  ask: number;
}

interface Candle {
  open: number;
  high: number;
  low: number;
  close: number;
  up: boolean;
  volume?: number;
}

const PERIODS = ["1", "5", "15", "30", "60", "1d"] as const;
const COMMON_SYMBOLS = ["AU0", "CU0", "SC0", "RB0", "IF0"] as const;

const fmtVal = (v: number | null | undefined): string =>
  v == null || Number.isNaN(v) ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 3 });

export function CommoditiesPage() {
  const [symbol, setSymbol] = useState("AU0");
  const [active, setActive] = useState("AU0");
  const [period, setPeriod] = useState<string>("1");

  const bars = useApi<Bar[] | null>(`/api/v1/commodities-fx/${active}/kline?period=${period}`);
  const quotes = useApi<FxQuote[] | null>(`/api/v1/commodities-fx/fx/quotes`);

  const candles: Candle[] = (bars.data ?? []).map((b) => ({
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    up: b.close >= b.open,
    volume: b.volume,
  }));

  const last = candles[candles.length - 1];

  const submit = (s?: string) => {
    const code = (s ?? symbol).trim();
    if (code) setActive(code);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="大宗/外汇研究 (AKShare)">
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="bg-surface-2 border border-border-soft rounded px-2 py-1 text-[12px] text-ink w-24"
            placeholder="期货代码"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <button
            className="bg-surface-2 border border-border-soft rounded px-2 py-1 text-[11px] text-ink hover:bg-surface"
            onClick={() => submit()}
          >
            查询
          </button>
          {COMMON_SYMBOLS.map((s) => (
            <button
              key={s}
              className={`border border-border-soft rounded px-2 py-0.5 text-[11px] font-mono ${active === s ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
              onClick={() => { setActive(s); setSymbol(s); }}
            >
              {s}
            </button>
          ))}
          <span className="text-[11px] text-ink-3 ml-1">
            当前: <span className="font-mono text-gold">{active}</span>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-2">
          {PERIODS.map((p) => (
            <button
              key={p}
              className={`border border-border-soft rounded px-2 py-0.5 text-[11px] ${period === p ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
              onClick={() => setPeriod(p)}
            >
              {p === "1d" ? "日线" : `${p}m`}
            </button>
          ))}
          <span className="text-[11px] text-ink-4">AKShare 期货分钟/日线 · 研究数据,非交易信号</span>
        </div>
      </V17Card>

      <V17Card title={`K 线 ${active}`} right={last ? `${last.close} @ ${bars.data?.at(-1)?.timestamp ?? ""}` : "—"}>
        {bars.data && bars.data.length > 0 ? (
          <KlineChart sym={active} tf={period} bars={candles} />
        ) : (
          <p className="py-6 text-center text-[11px] text-ink-4">暂无 K 线数据(可能未装 akshare 或数据源无此品种)</p>
        )}
      </V17Card>

      <V17Card title="外汇即期报价">
        {quotes.data && quotes.data.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-ink-4 text-left">
                  <th className="py-1 pr-3">货币对</th>
                  <th className="py-1 pr-3">买入</th>
                  <th className="py-1 pr-3">卖出</th>
                  <th className="py-1 pr-3">点差</th>
                </tr>
              </thead>
              <tbody>
                {quotes.data.slice(0, 25).map((q) => (
                  <tr key={q.pair} className="border-t border-border-soft text-ink">
                    <td className="py-1 pr-3 text-gold">{q.pair}</td>
                    <td className="py-1 pr-3">{fmtVal(q.bid)}</td>
                    <td className="py-1 pr-3">{fmtVal(q.ask)}</td>
                    <td className="py-1 pr-3 text-ink-4">{q.bid != null && q.ask != null ? (q.ask - q.bid).toFixed(5) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-6 text-center text-[11px] text-ink-4">暂无外汇报价数据</p>
        )}
      </V17Card>
    </div>
  );
}
