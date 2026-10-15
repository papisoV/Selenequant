"use client";

import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import { usePaperStore } from "@/lib/store/paper";
import { useShallow } from "zustand/react/shallow";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { StrategyStatus } from "@/components/trading/strategy-status";

// /api/v1/tof/current → ToxicOrderflowScore: {exchange, symbol, cvd_delta,
// liq_imbalance, book_imbalance, toxicity_score(0-100), direction, mark_price, timestamp}
interface TofCurrent {
  exchange?: string;
  symbol?: string;
  toxicity_score?: number;
  direction?: string;
  cvd_delta?: number;
  liq_imbalance?: number;
  book_imbalance?: number;
  mark_price?: number;
  message?: string;
}

// USD 名义额；负数保持负号（后端 cvd_delta / liq_imbalance 都是有符号美元额）
function formatUsd(val: number): string {
  const abs = Math.abs(val);
  const sign = val < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

function signedUsd(val: number): string {
  return val >= 0 ? `+${formatUsd(val)}` : formatUsd(val);
}

// book_imbalance 是 [-1,1] 比值，不是美元额
function signedPct(val: number): string {
  return `${val >= 0 ? "+" : ""}${(val * 100).toFixed(1)}%`;
}

const DIR_LABEL: Record<string, string> = { bullish: "看多", bearish: "看空", neutral: "中性" };
const DIR_CLS: Record<string, string> = { bullish: "text-pos", bearish: "text-neg", neutral: "text-ink-3" };

// 设计稿 L4761 renderPageTof — 交易运维
export function TofPage() {
  const { symBase, pageFilter } = useContextStore();
  const symbol = `${symBase}/USDT`;
  const { data: tofReal, isLoading } = useApi<TofCurrent | null>(
    `/api/v1/tof/current?symbol=${encodeURIComponent(symbol)}&cvd_interval=${pageFilter.tf === "1h" ? "5m" : pageFilter.tf === "4h" ? "1h" : "4h"}`
  );

  // 运行日志数据源：usePaperStore.fills（WS 实时推）
  const fills = usePaperStore(useShallow((s) => s.fills));
  const streamState = usePaperStore((s) => s.streamState);

  const score = tofReal?.toxicity_score;
  const direction = tofReal?.direction ?? "neutral";
  const cvd = tofReal?.cvd_delta;
  const liqImb = tofReal?.liq_imbalance;
  const obImb = tofReal?.book_imbalance;

  const logs: string[] = fills.length > 0
    ? fills.slice(0, 8).map((f) => {
        const ts = f.timestamp ?? f.ts ?? 0;
        const t = ts > 0 ? new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
        const sym = f.symbol ?? "?";
        const side = f.side ?? "?";
        const qty = f.quantity_base ?? f.quantity ?? 0;
        const price = f.price ?? 0;
        return `[${t}] FILL ${side.toUpperCase()} ${sym} ${qty} @ ${price}`;
      })
    : [];

  // toxicity_score 0-100，越高越毒；>=66 红 / >=33 黄 / 其余绿
  const scoreColor = score == null ? "text-ink-4" : score >= 66 ? "text-neg" : score >= 33 ? "text-gold" : "text-pos";

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={`TOF Score · ${symBase}`}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
          <StatBlock label="毒性分数" value={score != null ? score.toFixed(1) : "—"} cls={scoreColor} />
          <StatBlock label="方向" value={DIR_LABEL[direction] ?? direction} cls={DIR_CLS[direction] ?? "text-ink-3"} />
          <StatBlock label="CVD" value={cvd != null ? signedUsd(cvd) : "—"} cls={cvd != null && cvd >= 0 ? "text-pos" : "text-neg"} />
          <StatBlock label="爆仓失衡" value={liqImb != null ? signedUsd(liqImb) : "—"} cls={liqImb != null && liqImb >= 0 ? "text-neg" : "text-pos"} />
          <StatBlock label="盘口失衡" value={obImb != null ? signedPct(obImb) : "—"} cls={obImb != null && obImb >= 0 ? "text-pos" : "text-neg"} />
        </div>
        <div className="text-[9px] text-ink-4 mt-1.5 leading-relaxed">
          数据源: /api/v1/tof/current · {streamState === "live" ? "WS live" : streamState === "degraded" ? "WS degraded" : "WS recovering"}
          <br />
          毒性分数 = CVD + 爆仓失衡 + 盘口失衡加权综合分(0-100，越高越毒)；爆仓失衡为正=多头被爆(看空)
        </div>
      </V17Card>
      <V17Card title="运行日志">
        <div className="tof-log flex flex-col gap-0.5">
          {logs.length === 0 ? (
            <div className="text-[10px] text-ink-4 py-2 text-center">
              {isLoading ? "加载中..." : "暂无成交日志 (等待 WS fill 推送)"}
            </div>
          ) : logs.map((l, i) => (
            <div key={i} className="tof-log-line text-[10px] text-ink-3 font-mono py-0.5">
              {l}
            </div>
          ))}
        </div>
      </V17Card>
      <V17Card title="策略状态">
        <StrategyStatus />
      </V17Card>
    </div>
  );
}
