"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 业务域插入页 — Hyperliquid 筛选
// 后端 /api/v1/hyperliquid/addresses/screen 返回:
// { results: ScreenedAddress[], count, classification, window_days, min_score, limit, offset, has_more }
// ScreenedAddress 字段见 app/models/hyperliquid.py:
//   address, classification(elite/inverse/neutral), score(0-100),
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

function fmtNum(n: number | undefined): string {
  if (n === undefined || n === null) return "—";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toFixed(0);
}

export function HyperliquidPage() {
  const tr = useUiText()

  const { data } = useApi<HlScreenData | null>(
    "/api/v1/hyperliquid/addresses/screen"
  );
  const d = data ?? null;
  const results = d?.results ?? [];
  const eliteCount = results.filter((r) => r.classification === "elite").length;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("HL 筛选概览")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("筛选地址")} value={String(d?.count ?? results.length)} />
          <StatBlock label={tr("精英交易者")} value={String(eliteCount)} cls="text-gold" />
          <StatBlock
            label={tr("窗口")}
            value={d?.window_days != null ? `${d.window_days}d` : "—"}
          />
        </div>
      </V17Card>
      <V17Card title={tr("Top 交易者 (30d)")}>
        <div className="flex flex-col">
          {results.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          )}
          {results.slice(0, 30).map((r, i) => {
            const cls = r.classification ?? "neutral";
            const roi = (r.metrics?.realized_pnl ?? 0) > 0;
            return (
              <div
                key={r.address}
                className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft"
              >
                <span className="w-4 text-ink-4 text-right">{i + 1}</span>
                <span className="w-12 font-medium">{tr(CLASS_LABEL[cls] ?? cls)}</span>
                <span className="flex-1 text-ink-3 font-mono truncate">{r.address.slice(0, 12)}</span>
                <span className="w-8 text-right text-ink-2 font-mono">{r.score ?? 0}</span>
                <span className="w-12 text-right font-mono num text-pos">
                  {(r.metrics?.win_rate ?? 0) * 100 > 0
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
              </div>
            );
          })}
        </div>
      </V17Card>
    </div>
  );
}
