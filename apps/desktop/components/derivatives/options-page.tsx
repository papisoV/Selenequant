"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";

// 旧代码 kingfisher/options-panel.tsx (307 行) 吸收
// 数据层: /api/v1/options/{summary,tickers,instruments}
// 视觉骨架: V17Card + StatBlock (新前端)
// 对接逻辑: base_currency 切换 (BTC/ETH) + force refresh + expiry 选择 + greeks 表
// 1 文件管 4 路由: options-summary / options-tickers / options-instruments / options-tickers-instrument-name

interface OptionsTicker {
  instrument_name: string;
  mark_price: number | null;
  mark_price_usd: number | null;
  underlying_price: number | null;
  implied_volatility: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  rho: number | null;
  open_interest: number | null;
  volume_24h: number | null;
  bid_price: number | null;
  ask_price: number | null;
  last_price: number | null;
  timestamp: number | null;
}

interface OptionsSummary {
  base_currency: string;
  expiration_timestamp: number | null;
  days_to_expiry: number | null;
  total_call_oi: number | null;
  total_put_oi: number | null;
  put_call_ratio: number | null;
  max_pain: number | null;
  total_call_volume: number | null;
  total_put_volume: number | null;
  call_iv_atm: number | null;
  put_iv_atm: number | null;
  timestamp: number | null;
}

interface OptionsInstrument {
  instrument_name: string;
  base_currency: string;
  expiration_timestamp: number | null;
  strike: number | null;
  option_type: "call" | "put";
  is_active: boolean;
  creation_timestamp: number | null;
}

interface TickersResponse { tickers: OptionsTicker[]; }
interface SummaryResponse { summaries: OptionsSummary[]; }
interface InstrumentsResponse { instruments: OptionsInstrument[]; }

const CURRENCY_OPTIONS = ["BTC", "ETH"] as const;

function baseSym(sym: string): string {
  return sym.split("-")[0] || "BTC";
}

function formatIV(val: number | null): string {
  if (val == null) return "—";
  return `${(val * 100).toFixed(1)}%`;
}

function formatDate(ts: number | null): string {
  if (ts == null) return "—";
  return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatUSD(val: number | null): string {
  if (val == null) return "—";
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

function parseStrike(name: string): string {
  const parts = name.split("-");
  if (parts.length >= 3) return parts[2];
  return name;
}

function parseType(name: string): "C" | "P" {
  return name.endsWith("-C") ? "C" : "P";
}

function useOptionsData(baseCurrency: string, forceToken: number) {
  const tickersPath = `/api/v1/options/tickers?base_currency=${baseCurrency}${forceToken > 0 ? "&force=true" : ""}`;
  const { data: tickersData } = useApi<TickersResponse>(tickersPath);

  const summaryPath = `/api/v1/options/summary?base_currency=${baseCurrency}${forceToken > 0 ? "&force=true" : ""}`;
  const { data: summaryData } = useApi<SummaryResponse>(summaryPath);

  const instPath = `/api/v1/options/instruments?base_currency=${baseCurrency}${forceToken > 0 ? "&force=true" : ""}`;
  const { data: instrumentsData } = useApi<InstrumentsResponse>(instPath);

  return {
    tickers: tickersData?.tickers ?? [],
    summaries: summaryData?.summaries ?? [],
    instruments: instrumentsData?.instruments ?? [],
  };
}

interface CommonProps {
  baseCurrency: string;
  setBaseCurrency: (c: string) => void;
  forceToken: number;
  setForceToken: (fn: (t: number) => number) => void;
  refreshing: boolean;
  setRefreshing: (b: boolean) => void;
}

function CurrencyAndRefresh({ baseCurrency, setBaseCurrency, refreshing, onRefresh }: CommonProps & { onRefresh: () => void }) {
  return (
    <div className="flex items-center gap-1">
      <div className="flex gap-1">
        {CURRENCY_OPTIONS.map((c) => (
          <button
            key={c}
            onClick={() => setBaseCurrency(c)}
            className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
              baseCurrency === c
                ? "bg-gold/20 text-gold"
                : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
            }`}
          >
            {c}
          </button>
        ))}
      </div>
      <button
        onClick={onRefresh}
        disabled={refreshing}
        className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
        aria-label="force refresh"
      >
        <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
      </button>
    </div>
  );
}

function handleRefreshCommon(setRefreshing: (b: boolean) => void, setForceToken: (fn: (t: number) => number) => void) {
  setRefreshing(true);
  setForceToken((t) => t + 1);
  setTimeout(() => setRefreshing(false), 800);
}

// === options-summary ===
export function OptionsSummaryPage() {
  const [baseCurrency, setBaseCurrency] = useState<string>(baseSym("BTC-USDT-SWAP"));
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const { summaries } = useOptionsData(baseCurrency, forceToken);

  const handleRefresh = () => handleRefreshCommon(setRefreshing, setForceToken);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="期权总览"
        right={
          <CurrencyAndRefresh
            baseCurrency={baseCurrency}
            setBaseCurrency={setBaseCurrency}
            forceToken={forceToken}
            setForceToken={setForceToken}
            refreshing={refreshing}
            setRefreshing={setRefreshing}
            onRefresh={handleRefresh}
          />
        }
      >
        {summaries.length === 0 ? (
          <div className="py-4 text-center text-[10px] text-ink-4">无期权数据</div>
        ) : (
          <div className="space-y-2">
            {summaries.map((s) => (
              <div key={s.expiration_timestamp} className="border border-border-soft rounded p-2 bg-surface-2">
                <div className="text-[10px] text-ink-2 mb-1.5">
                  {s.base_currency} · {formatDate(s.expiration_timestamp)} · DTE {s.days_to_expiry?.toFixed(0) ?? "—"}
                </div>
                <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
                  <StatBlock label="Call IV" value={formatIV(s.call_iv_atm)} cls="text-pos" />
                  <StatBlock label="Put IV" value={formatIV(s.put_iv_atm)} cls="text-neg" />
                  <StatBlock label="P/C Ratio" value={s.put_call_ratio?.toFixed(2) ?? "—"} />
                  <StatBlock label="Max Pain" value={formatUSD(s.max_pain)} cls="text-gold" />
                </div>
                <div className="grid gap-2 mt-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
                  <StatBlock label="Call OI" value={s.total_call_oi?.toFixed(0) ?? "—"} />
                  <StatBlock label="Put OI" value={s.total_put_oi?.toFixed(0) ?? "—"} />
                  <StatBlock label="Call Vol" value={s.total_call_volume?.toFixed(0) ?? "—"} />
                  <StatBlock label="Put Vol" value={s.total_put_volume?.toFixed(0) ?? "—"} />
                </div>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}

// === options-tickers ===
export function OptionsTickersPage() {
  const [baseCurrency, setBaseCurrency] = useState<string>(baseSym("BTC-USDT-SWAP"));
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedExpiry, setSelectedExpiry] = useState<number | null>(null);

  const { tickers, summaries } = useOptionsData(baseCurrency, forceToken);

  const handleRefresh = () => handleRefreshCommon(setRefreshing, setForceToken);

  const activeExpiry = selectedExpiry ?? summaries[0]?.expiration_timestamp ?? null;
  const filteredTickers = activeExpiry
    ? tickers.filter((t) => t.instrument_name.includes(formatDate(activeExpiry).replace(" ", "")))
    : tickers.slice(0, 20);

  const sortedTickers = [...filteredTickers].sort((a, b) => {
    const sa = parseFloat(parseStrike(a.instrument_name));
    const sb = parseFloat(parseStrike(b.instrument_name));
    const ta = parseType(a.instrument_name) === "C" ? 0 : 1;
    const tb = parseType(b.instrument_name) === "C" ? 0 : 1;
    return sa - sb || ta - tb;
  });

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="期权 Tickers (Greeks)"
        right={
          <CurrencyAndRefresh
            baseCurrency={baseCurrency}
            setBaseCurrency={setBaseCurrency}
            forceToken={forceToken}
            setForceToken={setForceToken}
            refreshing={refreshing}
            setRefreshing={setRefreshing}
            onRefresh={handleRefresh}
          />
        }
      >
        {summaries.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-2">
            {summaries
              .sort((a, b) => (a.expiration_timestamp ?? 0) - (b.expiration_timestamp ?? 0))
              .map((s) => (
                <button
                  key={s.expiration_timestamp}
                  onClick={() => setSelectedExpiry(s.expiration_timestamp)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    activeExpiry === s.expiration_timestamp
                      ? "bg-purple-400/20 text-purple-400 border border-purple-400/30"
                      : "bg-surface-2 text-ink-3 hover:bg-surface-3"
                  }`}
                >
                  {formatDate(s.expiration_timestamp)}
                  <span className="ml-1 text-[9px] opacity-60">{s.days_to_expiry?.toFixed(0) ?? "—"}d</span>
                </button>
              ))}
          </div>
        )}
        {sortedTickers.length === 0 ? (
          <div className="py-4 text-center text-[10px] text-ink-4">无 tickers 数据</div>
        ) : (
          <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="border-b border-border-soft text-ink-4">
                  <th className="text-left py-1 px-1.5">Strike</th>
                  <th className="text-center py-1 px-1">Type</th>
                  <th className="text-right py-1 px-1">IV</th>
                  <th className="text-right py-1 px-1">Delta</th>
                  <th className="text-right py-1 px-1">Gamma</th>
                  <th className="text-right py-1 px-1">Theta</th>
                  <th className="text-right py-1 px-1">Vega</th>
                  <th className="text-right py-1 px-1">OI</th>
                  <th className="text-right py-1 px-1">Vol</th>
                </tr>
              </thead>
              <tbody>
                {sortedTickers.map((tick) => {
                  const optType = parseType(tick.instrument_name);
                  return (
                    <tr key={tick.instrument_name} className="border-b border-border-soft/60 hover:bg-surface-2">
                      <td className="py-0.5 px-1.5 font-mono text-ink">{parseStrike(tick.instrument_name)}</td>
                      <td className="py-0.5 px-1 text-center">
                        <span className={`px-1 rounded text-[9px] font-bold ${optType === "C" ? "bg-pos/20 text-pos" : "bg-neg/20 text-neg"}`}>
                          {optType}
                        </span>
                      </td>
                      <td className="py-0.5 px-1 text-right font-mono text-ink-2">{formatIV(tick.implied_volatility)}</td>
                      <td className="py-0.5 px-1 text-right font-mono text-ink-3">{tick.delta?.toFixed(3) ?? "—"}</td>
                      <td className="py-0.5 px-1 text-right font-mono text-ink-3">{tick.gamma?.toFixed(4) ?? "—"}</td>
                      <td className="py-0.5 px-1 text-right font-mono text-ink-3">{tick.theta?.toFixed(4) ?? "—"}</td>
                      <td className="py-0.5 px-1 text-right font-mono text-ink-3">{tick.vega?.toFixed(4) ?? "—"}</td>
                      <td className="py-0.5 px-1 text-right font-mono text-ink-2">{tick.open_interest?.toFixed(0) ?? "—"}</td>
                      <td className="py-0.5 px-1 text-right font-mono text-ink-3">{tick.volume_24h?.toFixed(0) ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>
    </div>
  );
}

// === options-instruments ===
export function OptionsInstrumentsPage() {
  const [baseCurrency, setBaseCurrency] = useState<string>(baseSym("BTC-USDT-SWAP"));
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const { instruments } = useOptionsData(baseCurrency, forceToken);

  const handleRefresh = () => handleRefreshCommon(setRefreshing, setForceToken);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="期权 Instruments"
        right={
          <CurrencyAndRefresh
            baseCurrency={baseCurrency}
            setBaseCurrency={setBaseCurrency}
            forceToken={forceToken}
            setForceToken={setForceToken}
            refreshing={refreshing}
            setRefreshing={setRefreshing}
            onRefresh={handleRefresh}
          />
        }
      >
        {instruments.length === 0 ? (
          <div className="py-4 text-center text-[10px] text-ink-4">无 instruments 数据</div>
        ) : (
          <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="border-b border-border-soft text-ink-4">
                  <th className="text-left py-1 px-1.5">Instrument</th>
                  <th className="text-center py-1 px-1">Type</th>
                  <th className="text-right py-1 px-1">Strike</th>
                  <th className="text-right py-1 px-1">Expiry</th>
                  <th className="text-center py-1 px-1">Active</th>
                </tr>
              </thead>
              <tbody>
                {instruments.map((inst) => (
                  <tr key={inst.instrument_name} className="border-b border-border-soft/60 hover:bg-surface-2">
                    <td className="py-0.5 px-1.5 font-mono text-ink">{inst.instrument_name}</td>
                    <td className="py-0.5 px-1 text-center">
                      <span className={`px-1 rounded text-[9px] font-bold ${inst.option_type === "call" ? "bg-pos/20 text-pos" : "bg-neg/20 text-neg"}`}>
                        {inst.option_type === "call" ? "C" : "P"}
                      </span>
                    </td>
                    <td className="py-0.5 px-1 text-right font-mono text-ink-2">{inst.strike ?? "—"}</td>
                    <td className="py-0.5 px-1 text-right font-mono text-ink-3">{formatDate(inst.expiration_timestamp)}</td>
                    <td className="py-0.5 px-1 text-center">
                      <span className={inst.is_active ? "text-pos" : "text-ink-4"}>
                        {inst.is_active ? "●" : "○"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>
    </div>
  );
}

// === options-tickers-instrument-name ===
// 旧代码没单独做这个 tab, 用 ticker 详情视图
export function OptionsTickerDetailPage() {
  const [baseCurrency, setBaseCurrency] = useState<string>(baseSym("BTC-USDT-SWAP"));
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedInst, setSelectedInst] = useState<string | null>(null);

  const { tickers } = useOptionsData(baseCurrency, forceToken);

  const handleRefresh = () => handleRefreshCommon(setRefreshing, setForceToken);

  const detail = tickers.find((t) => t.instrument_name === selectedInst) ?? tickers[0] ?? null;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="期权 Ticker 详情"
        right={
          <CurrencyAndRefresh
            baseCurrency={baseCurrency}
            setBaseCurrency={setBaseCurrency}
            forceToken={forceToken}
            setForceToken={setForceToken}
            refreshing={refreshing}
            setRefreshing={setRefreshing}
            onRefresh={handleRefresh}
          />
        }
      >
        <div className="mb-2">
          <div className="text-[9px] text-ink-4 mb-1">选择 instrument</div>
          <select
            value={selectedInst ?? ""}
            onChange={(e) => setSelectedInst(e.target.value)}
            className="h-6 w-full rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink-2"
          >
            {tickers.map((t) => (
              <option key={t.instrument_name} value={t.instrument_name}>{t.instrument_name}</option>
            ))}
          </select>
        </div>
        {detail ? (
          <div className="space-y-2">
            <div className="text-[10px] text-ink-2">{detail.instrument_name}</div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
              <StatBlock label="Mark" value={detail.mark_price != null ? `$${detail.mark_price.toFixed(2)}` : "—"} cls="text-gold" />
              <StatBlock label="Bid" value={detail.bid_price != null ? `$${detail.bid_price.toFixed(2)}` : "—"} cls="text-pos" />
              <StatBlock label="Ask" value={detail.ask_price != null ? `$${detail.ask_price.toFixed(2)}` : "—"} cls="text-neg" />
              <StatBlock label="Last" value={detail.last_price != null ? `$${detail.last_price.toFixed(2)}` : "—"} />
            </div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
              <StatBlock label="IV" value={formatIV(detail.implied_volatility)} />
              <StatBlock label="Delta" value={detail.delta?.toFixed(3) ?? "—"} />
              <StatBlock label="Gamma" value={detail.gamma?.toFixed(4) ?? "—"} />
              <StatBlock label="Theta" value={detail.theta?.toFixed(4) ?? "—"} />
              <StatBlock label="Vega" value={detail.vega?.toFixed(4) ?? "—"} />
            </div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              <StatBlock label="OI" value={detail.open_interest?.toFixed(0) ?? "—"} />
              <StatBlock label="Vol 24h" value={detail.volume_24h?.toFixed(0) ?? "—"} />
              <StatBlock label="Underlying" value={detail.underlying_price != null ? `$${detail.underlying_price.toFixed(2)}` : "—"} />
            </div>
          </div>
        ) : (
          <div className="py-4 text-center text-[10px] text-ink-4">无 ticker 数据</div>
        )}
      </V17Card>
    </div>
  );
}
