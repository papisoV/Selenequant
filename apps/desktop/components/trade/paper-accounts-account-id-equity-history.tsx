"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { Loader2, RefreshCw, TrendingUp } from "lucide-react";

// /api/v1/paper/accounts/{id}/equity-history?limit=N 返回 list — {timestamp, equity}
interface EquityPoint {
  timestamp?: string | number;
  equity?: number;
}

const LIMIT_OPTIONS = [50, 100, 200, 500];

export function PaperEquityHistoryPage() {
  const defaultId = useAccountId();
  const [accountId, setAccountId] = useState(defaultId);
  const [limit, setLimit] = useState(200);

  const { data, isLoading, mutate } = useApi<EquityPoint[]>(
    accountId ? `/api/v1/paper/accounts/${encodeURIComponent(accountId)}/equity-history?limit=${limit}` : null
  );

  const points = (data ?? []).filter((p) => Number.isFinite(Number(p.equity)));

  // SVG 折线图
  const W = 800, H = 260, P = 32;
  const values = points.map((p) => Number(p.equity));
  const min = values.length > 0 ? Math.min(...values) : 0;
  const max = values.length > 0 ? Math.max(...values) : 1;
  const range = max - min || 1;
  const pathD = values.length > 0
    ? values.map((v, i) => {
        const x = P + (i / Math.max(values.length - 1, 1)) * (W - 2 * P);
        const y = H - P - ((v - min) / range) * (H - 2 * P);
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ")
    : "";
  const areaD = values.length > 0
    ? `${pathD} L${(W - P).toFixed(1)},${H - P} L${P},${H - P} Z`
    : "";

  const lastPoint = values[values.length - 1];
  const firstPoint = values[0];
  const change = (firstPoint != null && lastPoint != null) ? lastPoint - firstPoint : 0;
  const changePct = (firstPoint != null && firstPoint !== 0 && lastPoint != null)
    ? (change / firstPoint) * 100
    : 0;

  function fmtTime(ts: string | number | undefined) {
    if (ts == null) return "—";
    const n = Number(ts);
    if (Number.isNaN(n)) return String(ts);
    // ms epoch → 本地时间(YYYY-MM-DD HH:MM)
    const d = new Date(n);
    if (Number.isNaN(d.getTime())) return String(ts);
    const pad = (x: number) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="权益历史"
        right={<span className="flex items-center gap-1 font-mono text-ink-3"><TrendingUp className="h-3 w-3" />{accountId}</span>}
      >
        {/* 账户 + limit 控制 */}
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <label className="text-[10px] text-ink-3" htmlFor="eq-acct">选择账户</label>
          <input
            id="eq-acct"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            placeholder="账户 ID"
            className="w-40 rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
          />
          <label className="ml-2 text-[10px] text-ink-3" htmlFor="eq-limit">点数</label>
          <select
            id="eq-limit"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
          >
            {LIMIT_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <button
            onClick={() => mutate()}
            className="flex items-center gap-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-2 hover:text-gold"
          >
            <RefreshCw className="h-3 w-3" />刷新
          </button>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : points.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-8">暂无权益历史数据(账户需先产生快照)</div>
        ) : (
          <>
            <div className="mb-2 flex flex-wrap gap-4 text-[10px]">
              <span>区间: <span className="font-mono text-gold">{min.toFixed(2)}</span> → <span className="font-mono text-gold">{max.toFixed(2)}</span></span>
              <span>最新: <span className={`font-mono ${change >= 0 ? "text-pos" : "text-neg"}`}>{lastPoint.toFixed(2)}</span></span>
              <span>区间变动: <span className={`font-mono ${change >= 0 ? "text-pos" : "text-neg"}`}>{change >= 0 ? "+" : ""}{change.toFixed(2)} ({changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%)</span></span>
              <span>点数: <span className="font-mono">{points.length}</span></span>
            </div>
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto border border-border-soft rounded">
              <defs>
                <linearGradient id="eq-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-gold)" stopOpacity="0.25" />
                  <stop offset="100%" stopColor="var(--color-gold)" stopOpacity="0" />
                </linearGradient>
              </defs>
              <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="var(--color-border-soft)" strokeWidth="0.5" />
              <line x1={P} y1={P} x2={P} y2={H - P} stroke="var(--color-border-soft)" strokeWidth="0.5" />
              {areaD && <path d={areaD} fill="url(#eq-fill)" />}
              <path d={pathD} fill="none" stroke="var(--color-gold)" strokeWidth="1.5" />
              {/* 网格线 */}
              {[0.25, 0.5, 0.75].map((f) => (
                <line
                  key={f}
                  x1={P} y1={P + f * (H - 2 * P)} x2={W - P} y2={P + f * (H - 2 * P)}
                  stroke="var(--color-border-soft)" strokeWidth="0.3" strokeDasharray="3 3"
                />
              ))}
            </svg>

            {/* 明细表 */}
            <div className="dtable mt-2 max-h-64 overflow-auto">
              <div className="grid grid-cols-2 gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono sticky top-0 bg-surface">
                <span>时间</span><span className="text-right">权益</span>
              </div>
              {points.slice().reverse().map((p, i) => (
                <div key={i} className="grid grid-cols-2 gap-2 text-[10px] py-1 border-b border-border-soft items-center">
                  <span className="text-ink-3 font-mono">{fmtTime(p.timestamp)}</span>
                  <span className="num text-right font-mono text-gold">{Number(p.equity).toFixed(2)}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </V17Card>
    </div>
  );
}
