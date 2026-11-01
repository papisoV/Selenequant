"use client";

import { useCallback, useEffect, useState } from "react";
import { apiDirectRaw, ApiError } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// ashare router returns bare dicts on success (envelope only on error) —
// unwrap like lib/hooks/use-api.ts fetcher does
async function ashareGet<T>(path: string): Promise<T> {
  const body = await apiDirectRaw<unknown>(path);
  if (
    body &&
    typeof body === "object" &&
    "data" in body &&
    "meta" in body
  ) {
    const envelope = body as { data: T | null; error?: { message?: string } };
    if (envelope.error) {
      throw new ApiError(envelope.error.message ?? "request failed", 500);
    }
    return envelope.data as T;
  }
  return body as T;
}

interface BackfillResult {
  requested?: number;
  n_records?: number;
  n_symbols_with_data?: number;
}

interface FinRecord {
  symbol: string;
  year: number;
  quarter: number;
  statDate?: string | null;
  pubDate?: string | null;
  roeAvg?: number | null;
  gpMargin?: number | null;
  netProfit?: number | null;
  revenue?: number | null;
  epsQ?: number | null;
  epsTTM?: number | null;
  YOYNI?: number | null;
  BPS?: number | null;
  liabilityToAsset?: number | null;
}

interface TimelineResult {
  symbol: string;
  n_records: number;
  records: FinRecord[];
}

interface SnapshotTop {
  symbol: string;
  value: number;
}

interface SnapshotResult {
  field: string;
  year: number;
  quarter: number;
  n_universe: number;
  median: number;
  max: SnapshotTop;
  min: SnapshotTop;
  top: SnapshotTop[];
}

// percentage-style fields (stored as decimals after /100 normalization)
const PCT_FIELDS = new Set(["roeAvg", "gpMargin", "YOYNI", "liabilityToAsset"]);

function fmt(v: number | null | undefined, field?: string): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (field && PCT_FIELDS.has(field)) return `${(v * 100).toFixed(2)}%`;
  return String(Math.round(v * 1000) / 1000);
}

const SNAPSHOT_FIELDS = ["BPS", "roeAvg", "epsTTM", "YOYNI", "netProfit"] as const;

export function AshareFundamentalsPage() {
  const tr = useUiText()

  const [symbols, setSymbols] = useState("600519,000858,601318");
  const [result, setResult] = useState<BackfillResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const params = new URLSearchParams({ symbols });
      const body = await apiDirectRaw<BackfillResult>(
        `/api/v1/ashare/fundamentals-backfill?${params}`,
        { method: "POST" },
      );
      setResult(body ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  // ─── timeline card ───
  const [tlSymbol, setTlSymbol] = useState("000008");
  const [timeline, setTimeline] = useState<TimelineResult | null>(null);
  const [tlLoading, setTlLoading] = useState(false);
  const [tlError, setTlError] = useState<string | null>(null);

  const loadTimeline = useCallback(async (sym: string) => {
    if (!sym.trim()) return;
    setTlLoading(true);
    setTlError(null);
    try {
      setTimeline(
        await ashareGet<TimelineResult>(
          `/api/v1/ashare/financials/${encodeURIComponent(sym.trim())}`,
        ),
      );
    } catch (e) {
      setTimeline(null);
      setTlError(e instanceof Error ? e.message : String(e));
    } finally {
      setTlLoading(false);
    }
  }, []);

  // ─── snapshot card ───
  const [snapField, setSnapField] = useState<string>("BPS");
  const [snapQuarter, setSnapQuarter] = useState(""); // "" = latest
  const [snapshot, setSnapshot] = useState<SnapshotResult | null>(null);
  const [snapLoading, setSnapLoading] = useState(false);
  const [snapError, setSnapError] = useState<string | null>(null);

  const loadSnapshot = useCallback(async (field: string, quarter: string) => {
    setSnapLoading(true);
    setSnapError(null);
    try {
      const params = new URLSearchParams({ field });
      if (quarter) {
        const [y, q] = quarter.split("-");
        params.set("year", y);
        params.set("quarter", q);
      }
      setSnapshot(
        await ashareGet<SnapshotResult>(
          `/api/v1/ashare/financials-snapshot?${params}`,
        ),
      );
    } catch (e) {
      setSnapshot(null);
      setSnapError(e instanceof Error ? e.message : String(e));
    } finally {
      setSnapLoading(false);
    }
  }, []);

  // initial load once
  useEffect(() => {
    void loadTimeline("000008");
    void loadSnapshot("BPS", "");
  }, [loadTimeline, loadSnapshot]);

  return (
    <div className="ashare-fundamentals-page flex h-full flex-col gap-3 overflow-y-auto">
      <h1 className="text-sm font-semibold text-ink">{tr("A股财报PIT")}</h1>
      <p className="text-[10px] text-ink-4">
        {tr("点查式 PIT 财务回填（pubDate 点前最后一份财报）。回填结果落库后供 EP/BP/YoYNI 等基本面因子按 PIT 取数。")}
      </p>

      <V17Card title={tr("触发 PIT 财务回填")}>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("股票代码（逗号分隔）")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              placeholder="600519,000858,601318"
            />
          </label>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button
            className="rounded border border-border-soft bg-surface-2 px-3 py-1 text-[11px] text-ink disabled:opacity-50"
            disabled={running || !symbols.trim()}
            onClick={run}
          >
            {running ? tr("回填中…") : tr("触发 PIT 财务回填")}
          </button>
          {error && <span className="text-[10px] text-red-400">{error}</span>}
        </div>
      </V17Card>

      {result && (
        <V17Card title={tr("回填结果")}>
          <div className="grid grid-cols-3 gap-2">
            <StatBlock label={tr("请求股票数")} value={String(result.requested ?? "—")} />
            <StatBlock
              label={tr("落库记录数")}
              value={String(result.n_records ?? "—")}
              cls={(result.n_records ?? 0) > 0 ? "text-green-400" : undefined}
            />
            <StatBlock
              label={tr("有数据股票数")}
              value={String(result.n_symbols_with_data ?? "—")}
            />
          </div>
        </V17Card>
      )}

      <V17Card title={tr("单股财报时间线（pubDate / statDate / 字段）")}>
        <div className="flex items-center gap-2">
          <input
            className="w-28 rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
            value={tlSymbol}
            onChange={(e) => setTlSymbol(e.target.value)}
            placeholder="000008"
          />
          <button
            className="rounded border border-border-soft bg-surface-2 px-3 py-1 text-[11px] text-ink disabled:opacity-50"
            disabled={tlLoading || !tlSymbol.trim()}
            onClick={() => void loadTimeline(tlSymbol)}
          >
            {tlLoading ? tr("查询中…") : tr("查询")}
          </button>
          {tlError && <span className="text-[10px] text-red-400">{tlError}</span>}
        </div>
        {timeline && timeline.records.length > 0 && (
          <div className="mt-2 max-h-72 overflow-auto">
            <table className="w-full text-left text-[10px]">
              <thead className="text-ink-3">
                <tr>
                  <th className="py-1 pr-2">{tr("季度")}</th>
                  <th className="py-1 pr-2">statDate</th>
                  <th className="py-1 pr-2">pubDate</th>
                  <th className="py-1 pr-2">ROE</th>
                  <th className="py-1 pr-2">{tr("毛利率")}</th>
                  <th className="py-1 pr-2">epsTTM</th>
                  <th className="py-1 pr-2">{tr("YoY净利")}</th>
                  <th className="py-1 pr-2">BPS</th>
                </tr>
              </thead>
              <tbody className="text-ink">
                {timeline.records.map((r) => (
                  <tr key={`${r.year}-${r.quarter}`} className="border-t border-border-soft">
                    <td className="py-1 pr-2">{r.year}Q{r.quarter}</td>
                    <td className="py-1 pr-2">{r.statDate ?? "—"}</td>
                    <td className="py-1 pr-2">{r.pubDate ?? "—"}</td>
                    <td className="py-1 pr-2">{fmt(r.roeAvg, "roeAvg")}</td>
                    <td className="py-1 pr-2">{fmt(r.gpMargin, "gpMargin")}</td>
                    <td className="py-1 pr-2">{fmt(r.epsTTM)}</td>
                    <td className="py-1 pr-2">{fmt(r.YOYNI, "YOYNI")}</td>
                    <td className="py-1 pr-2">{fmt(r.BPS)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>

      <V17Card title={tr("因子横截面快照")}>
        <div className="flex items-center gap-2">
          <select
            className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
            aria-label={tr("快照字段")}
            value={snapField}
            onChange={(e) => {
              setSnapField(e.target.value);
              void loadSnapshot(e.target.value, snapQuarter);
            }}
          >
            {SNAPSHOT_FIELDS.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
          <select
            className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
            aria-label={tr("快照季度")}
            value={snapQuarter}
            onChange={(e) => {
              setSnapQuarter(e.target.value);
              void loadSnapshot(snapField, e.target.value);
            }}
          >
            <option value="">{tr("最新季")}</option>
            {Array.from({ length: 15 }, (_, i) => {
              const y = 2022 + Math.floor((3 + i) / 4);
              const q = ((3 + i) % 4) + 1;
              return (
                <option key={`${y}-${q}`} value={`${y}-${q}`}>
                  {y}Q{q}
                </option>
              );
            })}
          </select>
          {snapError && <span className="text-[10px] text-red-400">{snapError}</span>}
        </div>
        {snapshot && (
          <>
            <div className="mt-2 grid grid-cols-3 gap-2">
              <StatBlock label={tr("覆盖股票数")} value={String(snapshot.n_universe)} />
              <StatBlock
                label={tr("中位数")}
                value={fmt(snapshot.median, snapshot.field)}
              />
              <StatBlock
                label={tr("最高（{sym}）", { sym: snapshot.max.symbol })}
                value={fmt(snapshot.max.value, snapshot.field)}
              />
            </div>
            <div className="mt-2 flex flex-col gap-1">
              {snapshot.top.map((t, i) => {
                const maxV = snapshot.top[0]?.value || 1;
                const minV = snapshot.top[snapshot.top.length - 1]?.value || 0;
                const span = maxV - minV || 1;
                const w = 8 + (92 * (t.value - minV)) / span;
                return (
                  <div key={t.symbol} className="flex items-center gap-2 text-[10px]">
                    <span className="w-4 text-ink-3">{i + 1}</span>
                    <span className="w-14 text-ink">{t.symbol}</span>
                    <div className="h-2 flex-1 rounded bg-surface-2">
                      <div
                        className="h-2 rounded bg-accent"
                        style={{ width: `${w}%` }}
                      />
                    </div>
                    <span className="w-20 text-right text-ink-3">
                      {fmt(t.value, snapshot.field)}
                    </span>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </V17Card>
    </div>
  );
}
