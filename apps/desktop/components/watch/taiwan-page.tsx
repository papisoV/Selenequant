"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 台股研究页 — 数据源 FinMind(经 /api/v1/taiwan/*)
// 端点: quote / valuation / financials / revenue / dividend / search
interface QuoteData {
  stock_id?: string;
  name?: string;
  board?: string;
  currency?: string;
  date?: string;
  open?: number | null;
  max?: number | null;
  min?: number | null;
  close?: number | null;
  change?: number | null;
  change_pct?: number | null;
  trading_volume?: number | null;
  trading_money?: number | null;
  per?: number | null;
  pbr?: number | null;
  dividend_yield?: number | null;
  shares_issued?: number | null;
  market_cap?: number | null;
}

interface ValuationData {
  close?: number | null;
  per?: number | null;
  per_year_range?: number[] | null;
  pbr?: number | null;
  dividend_yield?: number | null;
  high_52w?: number | null;
  low_52w?: number | null;
  market_cap?: number | null;
}

interface RevenueMonth {
  year: number;
  month: number;
  revenue: number | null;
  yoy_pct: number | null;
}

interface RevenueData {
  months?: RevenueMonth[];
}

interface DividendItem {
  year: string;
  cash: number;
  stock: number;
  cash_ex_date?: string | null;
  cash_payment_date?: string | null;
}

interface DividendData {
  dividends?: DividendItem[];
}

interface FinancialYear {
  year: string;
  full_year: boolean;
  quarters_count: number;
  revenue: number | null;
  gross_profit: number | null;
  gross_margin_pct: number | null;
  operating_income: number | null;
  operating_margin_pct: number | null;
  net_income: number | null;
  net_margin_pct: number | null;
  eps: number | null;
  roe_pct: number | null;
}

interface FinancialsData {
  years?: FinancialYear[];
}

interface SearchHit {
  stock_id: string;
  name: string;
  board?: string;
  industries?: string[];
}

const fmtNum = (v: number | null | undefined, digits = 2) =>
  v == null || Number.isNaN(v) ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: digits });
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(2)}%`);
const fmtCap = (v: number | null | undefined) =>
  v == null || Number.isNaN(v) ? "—" : v >= 1e12 ? `${(v / 1e12).toFixed(2)}T` : v >= 1e8 ? `${(v / 1e8).toFixed(2)}亿` : v.toLocaleString();

export function TaiwanPage() {
  const [symbol, setSymbol] = useState("2330");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState("2330");

  const quote = useApi<QuoteData | null>(`/api/v1/taiwan/${active}/quote`);
  const valuation = useApi<ValuationData | null>(`/api/v1/taiwan/${active}/valuation`);
  const revenue = useApi<RevenueData | null>(`/api/v1/taiwan/${active}/revenue`);
  const dividend = useApi<DividendData | null>(`/api/v1/taiwan/${active}/dividend`);
  const financials = useApi<FinancialsData | null>(`/api/v1/taiwan/${active}/financials`);
  const search = useApi<SearchHit[] | null>(query.trim() ? `/api/v1/taiwan/search?q=${encodeURIComponent(query.trim())}` : null);

  const q = quote.data;
  const v = valuation.data;
  const months = revenue.data?.months ?? [];
  const dividends = dividend.data?.dividends ?? [];
  const years = financials.data?.years ?? [];

  const submit = (sid: string) => {
    setActive(sid);
    setSymbol(sid);
    setQuery("");
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="台股个股研究 (FinMind)">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <input
              className="bg-surface-2 border border-border-soft rounded px-2 py-1 text-[12px] text-ink w-32"
              placeholder="代码或名称"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && query.trim() && submit(query.trim())}
            />
            {search.data && search.data.length > 0 && (
              <div className="absolute z-10 mt-1 w-56 max-h-48 overflow-auto bg-surface border border-border-soft rounded shadow-lg">
                {search.data.map((h) => (
                  <button
                    key={h.stock_id}
                    className="block w-full text-left px-2 py-1 text-[11px] text-ink hover:bg-surface-2"
                    onClick={() => submit(h.stock_id)}
                  >
                    <span className="font-mono text-gold">{h.stock_id}</span> {h.name}
                    {h.industries && h.industries.length > 0 && <span className="text-ink-4 ml-1">{h.industries[0]}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
          <span className="text-[11px] text-ink-3">当前: <span className="font-mono text-gold">{active}</span> {q?.name ?? ""}</span>
        </div>
      </V17Card>

      <V17Card title="行情快照" right={q?.date ?? "—"}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="收盘价" value={fmtNum(q?.close)} cls="text-gold" />
          <StatBlock label="涨跌" value={q?.change == null ? "—" : `${q.change >= 0 ? "+" : ""}${fmtNum(q.change)} (${fmtPct(q.change_pct)})`} cls={q?.change != null && q.change >= 0 ? "text-red-400" : "text-emerald-400"} />
          <StatBlock label="最高/最低" value={`${fmtNum(q?.max)} / ${fmtNum(q?.min)}`} />
          <StatBlock label="成交量(张)" value={fmtNum(q?.trading_volume, 0)} />
          <StatBlock label="成交额" value={q?.trading_money != null ? `${(q.trading_money / 1e8).toFixed(2)}亿` : "—"} />
          <StatBlock label="总市值" value={fmtCap(q?.market_cap)} />
          <StatBlock label="PE" value={fmtNum(q?.per)} />
          <StatBlock label="股息率" value={fmtPct(q?.dividend_yield)} />
        </div>
      </V17Card>

      <V17Card title="估值与区间">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="52周最高" value={fmtNum(v?.high_52w)} />
          <StatBlock label="52周最低" value={fmtNum(v?.low_52w)} />
          <StatBlock label="PE 年度区间" value={v?.per_year_range?.length ? `${fmtNum(v.per_year_range[0])} ~ ${fmtNum(v.per_year_range[1])}` : "—"} />
          <StatBlock label="PB" value={fmtNum(v?.pbr)} />
        </div>
      </V17Card>

      <V17Card title="财务摘要 (年)">
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-ink-4 text-left">
                <th className="py-1 pr-3">年份</th>
                <th className="py-1 pr-3">营收</th>
                <th className="py-1 pr-3">毛利率</th>
                <th className="py-1 pr-3">净利率</th>
                <th className="py-1 pr-3">EPS</th>
                <th className="py-1 pr-3">ROE</th>
              </tr>
            </thead>
            <tbody>
              {years.slice(0, 5).map((y) => (
                <tr key={y.year} className="border-t border-border-soft text-ink">
                  <td className="py-1 pr-3 text-gold">{y.year}{!y.full_year && y.quarters_count ? ` (${y.quarters_count}Q)` : ""}</td>
                  <td className="py-1 pr-3">{fmtNum(y.revenue)}</td>
                  <td className="py-1 pr-3">{fmtPct(y.gross_margin_pct)}</td>
                  <td className="py-1 pr-3">{fmtPct(y.net_margin_pct)}</td>
                  <td className="py-1 pr-3">{fmtNum(y.eps)}</td>
                  <td className="py-1 pr-3">{fmtPct(y.roe_pct)}</td>
                </tr>
              ))}
              {years.length === 0 && (
                <tr><td colSpan={6} className="py-2 text-ink-4 text-center">暂无财务数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </V17Card>

      <V17Card title="月营收 (近13个月)">
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-ink-4 text-left">
                <th className="py-1 pr-3">月份</th>
                <th className="py-1 pr-3">营收</th>
                <th className="py-1 pr-3">YoY</th>
              </tr>
            </thead>
            <tbody>
              {months.slice(-13).map((m) => (
                <tr key={`${m.year}-${m.month}`} className="border-t border-border-soft text-ink">
                  <td className="py-1 pr-3 text-gold">{m.year}-{String(m.month).padStart(2, "0")}</td>
                  <td className="py-1 pr-3">{fmtNum(m.revenue)}</td>
                  <td className={`py-1 pr-3 ${m.yoy_pct != null && m.yoy_pct >= 0 ? "text-red-400" : "text-emerald-400"}`}>{fmtPct(m.yoy_pct)}</td>
                </tr>
              ))}
              {months.length === 0 && (
                <tr><td colSpan={3} className="py-2 text-ink-4 text-center">暂无营收数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </V17Card>

      <V17Card title="股利政策 (近5年)">
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-ink-4 text-left">
                <th className="py-1 pr-3">年份</th>
                <th className="py-1 pr-3">现金股利</th>
                <th className="py-1 pr-3">股票股利</th>
                <th className="py-1 pr-3">除息日</th>
              </tr>
            </thead>
            <tbody>
              {dividends.slice(-5).map((d, i) => (
                <tr key={`${d.year}-${i}`} className="border-t border-border-soft text-ink">
                  <td className="py-1 pr-3 text-gold">{d.year}</td>
                  <td className="py-1 pr-3">{fmtNum(d.cash)}</td>
                  <td className="py-1 pr-3">{fmtNum(d.stock)}</td>
                  <td className="py-1 pr-3 text-ink-4">{d.cash_ex_date ?? "—"}</td>
                </tr>
              ))}
              {dividends.length === 0 && (
                <tr><td colSpan={4} className="py-2 text-ink-4 text-center">暂无股利数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </V17Card>
    </div>
  );
}
