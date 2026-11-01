"use client";

import { preload } from "swr";
import { useApi, fetcher } from "@/lib/hooks/use-api";
import { PageFilterBar, V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { WorkspaceLink } from "@/components/shared/workspace-link";
import { useUiText } from "@/components/i18n/use-ui-text"

// 后端 /api/v1/gex/current 返回 GEXResult：
// {underlying_price, net_gex, dealer_positioning, total_call_gex, total_put_gex,
//  key_levels:[{price, net_gex, label}], strikes:[OptionsStrikeData], ...}
// strikes 无顶层 gex——每档净 GEX 用与后端一致的公式重建：call_gamma*call_oi - put_gamma*put_oi
interface GexStrike {
  strike?: number;
  call_oi?: number;
  put_oi?: number;
  call_gamma?: number;
  put_gamma?: number;
}
interface GexKeyLevel { price?: number; net_gex?: number; label?: string }
interface GexResponse {
  strikes?: GexStrike[];
  underlying_price?: number;
  net_gex?: number;
  dealer_positioning?: string;
  total_call_gex?: number;
  total_put_gex?: number;
  key_levels?: GexKeyLevel[];
}

const POS_GEX = "long_gamma";
const NEG_GEX = "short_gamma";
const DIR_LABEL: Record<string, string> = { long_gamma: "做市商多 Gamma", short_gamma: "做市商空 Gamma", neutral: "中性" };

// 设计稿 L4398 renderPageGex — Gamma Exposure Profile
// hover 预载:预热静态端点进 SWR global cache
// preload 不做 catch，预载失败必须自己认领，否则成为 unhandledRejection。
export function preloadData() {
  preload("/api/v1/gex/current?currency=BTC", fetcher).catch(() => {});
}

export function GexPage() {
  const tr = useUiText()

  const { data: gexReal, isLoading } = useApi<GexResponse>(
    "/api/v1/gex/current?currency=BTC"
  );
  const realSpot = gexReal?.underlying_price != null ? Number(gexReal.underlying_price) : null;
  const realStrikes = gexReal?.strikes;
  const realNetGex = gexReal?.net_gex != null ? Number(gexReal.net_gex) : null;
  const dealerPos = gexReal?.dealer_positioning;
  // 正/负 GEX 总量：后端 GEXResult 用 total_call_gex / total_put_gex 表达
  const realPosTotal = gexReal?.total_call_gex != null ? Number(gexReal.total_call_gex) : null;
  const realNegTotal = gexReal?.total_put_gex != null ? Number(gexReal.total_put_gex) : null;

  const hasData = !!(realStrikes && realStrikes.length > 0 && realSpot != null);

  const gammaPoints: { k: number; gex: number }[] = [];
  if (hasData) {
    realStrikes!.forEach((s) => {
      const k = Number(s.strike ?? 0);
      const gex = Number(s.call_gamma ?? 0) * Number(s.call_oi ?? 0) - Number(s.put_gamma ?? 0) * Number(s.put_oi ?? 0);
      gammaPoints.push({ k, gex });
    });
  }
  const maxAbs = gammaPoints.length > 0 ? Math.max(...gammaPoints.map((p) => Math.abs(p.gex))) : 0;
  // 翻转点：后端把 gamma_flip 放在 key_levels 里，无顶层 flip_point
  const flipLevel = (gexReal?.key_levels ?? []).find((l) => l.label === "gamma_flip");
  const flipPoint = flipLevel?.price != null ? { k: Number(flipLevel.price), gex: 0 } : null;

  // 价格轴用真实 strike 范围，避免捏造 ±8000 刻度
  const strikeMin = gammaPoints.length > 0 ? gammaPoints[0].k : null;
  const strikeMax = gammaPoints.length > 0 ? gammaPoints[gammaPoints.length - 1].k : null;
  // 现货高亮：取最接近现货的一档（strike 未必精确等于现货）
  const nearestSpotK = realSpot != null && gammaPoints.length > 0
    ? gammaPoints.reduce((best, p) => (Math.abs(p.k - realSpot) < Math.abs(best - realSpot) ? p.k : best), gammaPoints[0].k)
    : null;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("Gamma 暴露 Profile")}>
        <div className="flex items-center justify-between gap-2">
          <PageFilterBar tfs={["1h", "4h", "24h"]} showEx={false} />
          <WorkspaceLink source="gex" />
        </div>
        {!hasData ? (
          <div className="py-10 text-center text-[10px] text-ink-4">
            {isLoading ? tr("加载中...") : tr("暂无 GEX 数据")}
          </div>
        ) : (
          <>
            <div
              className="gex-chart flex items-end gap-[2px] border-b border-border-soft pb-1 mt-2"
              style={{ height: 160 }}
            >
              {gammaPoints.map((p) => {
                const h = maxAbs > 0 ? (Math.abs(p.gex) / maxAbs) * 100 : 0;
                const color = p.gex >= 0 ? "#31aa40" : "#e85854";
                const isSpot = nearestSpotK != null && p.k === nearestSpotK;
                return (
                  <div
                    key={p.k}
                    className="gex-bar flex-1"
                    style={{
                      height: `${h}%`,
                      background: color,
                      borderTop: isSpot ? "2px solid var(--gold)" : undefined,
                    }}
                    title={`${p.k} GEX ${p.gex.toFixed(1)}`}
                  />
                );
              })}
            </div>
            {strikeMin != null && strikeMax != null && (
              <div className="flex justify-between text-[9px] text-ink-4 mt-1 font-mono">
                <span>${strikeMin.toLocaleString()}</span>
                <span>{tr("翻转点")}</span>
                <span>${strikeMax.toLocaleString()}</span>
              </div>
            )}
          </>
        )}
      </V17Card>
      <V17Card title={tr("统计")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
          <StatBlock label={tr("现货")} value={realSpot != null ? `$${realSpot.toLocaleString()}` : "—"} cls="text-gold" />
          <StatBlock label={tr("翻转点")} value={flipPoint ? `$${flipPoint.k.toLocaleString()}` : "—"} />
          <StatBlock label={tr("净 GEX")} value={realNetGex != null ? realNetGex.toFixed(2) : "—"} cls={realNetGex != null ? (realNetGex >= 0 ? "text-pos" : "text-neg") : "text-ink-4"} />
          <StatBlock label={tr("正 GEX 总量")} value={realPosTotal != null ? `+${realPosTotal.toFixed(0)}` : "—"} cls="text-pos" />
          <StatBlock label={tr("负 GEX 总量")} value={realNegTotal != null ? `-${realNegTotal.toFixed(0)}` : "—"} cls="text-neg" />
        </div>
        <div className="text-[9px] text-ink-4 mt-1.5">
          {tr("做市商持仓:")} {dealerPos ? tr(DIR_LABEL[dealerPos] ?? dealerPos) : "—"}
          {" · "}
          {tr("正 Gamma 均值回归 / 负 Gamma 助涨助跌")}
        </div>
      </V17Card>
    </div>
  );
}
