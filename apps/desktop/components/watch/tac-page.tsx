"use client";

import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import { PageFilterBar, V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

interface FundingRates {
  rates?: Record<string, { current?: number; next?: number } | number>;
}

// 设计稿 L4440 renderPageTac — 资金费率
export function TacPage() {
  const tr = useUiText()

  const { pageFilter } = useContextStore();
  const { data: realData, isLoading } = useApi<FundingRates | null>(
    "/api/v1/derivatives/funding-rates"
  );

  const rows = (() => {
    const rates = realData?.rates;
    if (rates && Object.keys(rates).length > 0) {
      return Object.entries(rates).map(([sym, v]) => {
        const cur = typeof v === "number" ? v : Number(v?.current ?? 0);
        const next = typeof v === "number" ? cur : Number(v?.next ?? cur);
        const trend = cur > 0.05 ? "🔥" : cur < -0.05 ? "❄" : "·";
        return { s: sym, cur, next, trend };
      });
    }
    return [];
  })();

  const avgCur = rows.length > 0 ? rows.reduce((s, r) => s + r.cur, 0) / rows.length : 0;
  const maxCur = rows.length > 0 ? Math.max(...rows.map((r) => r.cur)) : 0;
  const minCur = rows.length > 0 ? Math.min(...rows.map((r) => r.cur)) : 0;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("资金费率 (下一周期 · {tf})", { tf: pageFilter.tf })}>
        <PageFilterBar tfs={["1h", "4h", "24h"]} showEx={false} />
        <div className="tac-list flex flex-col mt-2">
          {rows.length === 0 ? (
            <div className="py-6 text-center text-[10px] text-ink-4">
              {isLoading ? tr("加载中...") : tr("暂无资金费率数据")}
            </div>
          ) : rows.map((r) => {
            const color =
              r.cur > 0
                ? "text-neg"
                : r.cur < 0
                  ? "text-pos"
                  : "text-ink-4";
            return (
              <div
                key={r.s}
                className="tac-row flex items-center gap-3 text-[10px] py-0.5 border-b border-border-soft"
              >
                <span className="tac-sym w-10 text-ink-2">{r.s}</span>
                <span className={`tac-cur num w-20 font-mono ${color}`}>
                  {(r.cur * 100).toFixed(4)}%
                </span>
                <span className="tac-next num w-20 font-mono text-ink-3">
                  {(r.next * 100).toFixed(4)}%
                </span>
                <span className="tac-trend w-6 text-center">{r.trend}</span>
              </div>
            );
          })}
        </div>
      </V17Card>
      <V17Card title={tr("统计")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("平均费率")} value={rows.length > 0 ? `${avgCur >= 0 ? "+" : ""}${(avgCur * 100).toFixed(4)}%` : "—"} />
          <StatBlock label={tr("最高")} value={rows.length > 0 ? `${maxCur >= 0 ? "+" : ""}${(maxCur * 100).toFixed(4)}%` : "—"} cls="text-neg" />
          <StatBlock label={tr("最低")} value={rows.length > 0 ? `${minCur >= 0 ? "+" : ""}${(minCur * 100).toFixed(4)}%` : "—"} cls="text-pos" />
        </div>
      </V17Card>
    </div>
  );
}
