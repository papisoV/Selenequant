"use client";

import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { GridControlPanel } from "@/components/trade/grid-control-panel";
import { useUiText } from "@/components/i18n/use-ui-text"

// 后端 /api/v1/grid/state 返回 GridState
interface GridFill { price: number; side: "buy" | "sell"; size: number; }
interface GridState {
  symbol?: string;
  upper?: number;
  lower?: number;
  levels?: number;
  size?: number;
  cur_price?: number;
  fills?: GridFill[];
}

// 设计稿 L4691 renderPageGrid + L4737 bindGridParams — 网格策略
export function GridPage() {
  const tr = useUiText()

  const { data: gridReal, isLoading } = useApi<GridState | null>("/api/v1/grid/state");
  const { pageFilter, symBase, gridParams, setGridParams } = useContextStore();
  const sym = `${symBase}-PERP`;
  const { upper, lower, levels, size } = gridParams;

  const curPx = gridReal?.cur_price ?? null;
  const realFills = gridReal?.fills ?? [];

  let markerPct: number | null = null;
  if (curPx != null && upper > lower) {
    markerPct = ((curPx - lower) / (upper - lower)) * 100;
    markerPct = Math.max(0, Math.min(100, markerPct));
  }

  const gridLines: { price: number; side: "buy" | "sell"; filled: boolean; isClosest: boolean }[] = [];
  for (let i = 0; i <= levels; i++) {
    const price = lower + ((upper - lower) * i) / levels;
    const side: "buy" | "sell" = i % 2 === 0 ? "buy" : "sell";
    const matchedFill = realFills.find((f) => Math.abs(f.price - price) < (upper - lower) / levels / 2);
    const filled = !!matchedFill;
    const isClosest = curPx != null && Math.abs(price - curPx) < (upper - lower) / levels / 2;
    gridLines.push({ price, side, filled, isClosest });
  }
  const perFill = realFills.map((f) => ({
    ...f,
    pnl: curPx != null ? (curPx - f.price) * f.size * (f.side === "buy" ? 1 : -1) : 0,
  }));
  const totalPnl = perFill.reduce((s, x) => s + x.pnl, 0);
  const filledCount = realFills.length;
  const pendingCount = Math.max(0, levels + 1 - filledCount);

  const onChange = (key: keyof typeof gridParams, v: number) => {
    if (isNaN(v)) return;
    if (key === "levels" && (v < 2 || v > 50)) return;
    if (key === "size" && v <= 0) return;
    if (key === "upper" && v <= gridParams.lower) return;
    if (key === "lower" && v >= gridParams.upper) return;
    setGridParams({ [key]: v });
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("网格 {sym} · {lower}-{upper}", { sym: sym, lower: lower, upper: upper })}>
        <div className="grid-marker-bar flex items-center gap-2 mb-2">
          <span className="grid-marker-lower num text-[9px] text-ink-4 font-mono">${lower.toFixed(0)}</span>
          <div className="grid-marker-track relative flex-1 h-2 bg-surface-2 rounded-sm">
            {markerPct != null && (
              <div
                className="grid-marker-cur absolute"
                style={{ left: `${markerPct.toFixed(1)}%`, top: -2 }}
                title={tr("当前价 {p0}", { p0: curPx != null ? `$${curPx.toFixed(0)}` : "—" })}
              >
                <span className="grid-marker-label num text-[9px] text-gold font-mono">
                  {curPx != null ? `$${curPx.toFixed(0)}` : "—"}
                </span>
              </div>
            )}
          </div>
          <span className="grid-marker-upper num text-[9px] text-ink-4 font-mono">${upper.toFixed(0)}</span>
        </div>
        <div className="grid-list flex flex-col">
          {gridLines.map((l, i) => (
            <div
              key={i}
              className={`grid-line flex items-center gap-3 text-[10px] py-0.5 border-b border-border-soft ${l.side}${l.filled ? " filled" : ""}${l.isClosest ? " current" : ""}`}
            >
              <span className="grid-price num font-mono w-20 text-ink-2">${l.price.toFixed(0)}</span>
              <span className={`grid-side w-10 ${l.side === "buy" ? "text-pos" : "text-neg"}`}>
                {l.side.toUpperCase()}
              </span>
              <span className="grid-status flex-1 text-right text-ink-4">
                {l.filled ? tr("✓ 已成交") : tr("挂单中")}
              </span>
            </div>
          ))}
        </div>
        {gridReal == null && !isLoading && (
          <div className="text-[10px] text-ink-4 mt-2 text-center">{tr("暂无网格策略数据")}</div>
        )}
      </V17Card>
      <V17Card title={tr("参数编辑")}>
        <div className="grid grid-cols-4 gap-2 text-[10px]">
          {(["upper", "lower", "levels", "size"] as const).map((key) => (
            <div key={key} className="grid-param">
              <label className="text-ink-4 text-[9px]">
                {key === "upper" ? tr("上限") : key === "lower" ? tr("下限") : key === "levels" ? tr("网格数") : tr("单量")}
              </label>
              <input
                type="number"
                className="grid-param-input mt-0.5 w-full rounded border border-border-soft bg-surface-2 px-1.5 py-1 font-mono text-ink"
                value={gridParams[key]}
                step={key === "upper" || key === "lower" ? 100 : key === "levels" ? 1 : 0.01}
                min={key === "levels" ? 2 : key === "size" ? 0.001 : undefined}
                max={key === "levels" ? 50 : undefined}
                onChange={(e) => onChange(key, parseFloat(e.target.value))}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                }}
              />
            </div>
          ))}
        </div>
        <div className="text-[9px] text-ink-4 mt-1.5">
          {tr("下限 < 当前价 < 上限 · 网格 2-50")}
        </div>
        <div className="grid grid-cols-4 gap-2 mt-2.5">
          <StatBlock label={tr("网格数")} value={levels + 1} />
          <StatBlock label={tr("区间")} value={`$${((upper - lower) / 1000).toFixed(0)}K`} />
          <StatBlock label={tr("已成交")} value={filledCount} cls="text-pos" />
          <StatBlock label={tr("挂单")} value={pendingCount} />
        </div>
      </V17Card>
      <V17Card title={tr("PnL 拆解")}>
        <div className="grid-pnl-list flex flex-col">
          {perFill.length === 0 ? (
            <div className="text-[10px] text-ink-4">{tr("无成交")}</div>
          ) : (
            perFill.map((f, i) => (
              <div
                key={i}
                className="grid-pnl-row flex items-center gap-3 text-[10px] py-0.5 border-b border-border-soft"
              >
                <span className="grid-pnl-price num font-mono text-ink-2 w-20">
                  ${f.price.toFixed(0)}
                </span>
                <span className={`grid-pnl-side w-10 ${f.side === "buy" ? "text-pos" : "text-neg"}`}>
                  {f.side.toUpperCase()}
                </span>
                <span className="grid-pnl-size num font-mono text-ink-3 w-12">
                  {f.size.toFixed(3)}
                </span>
                <span className={`grid-pnl-val num flex-1 text-right font-mono ${f.pnl >= 0 ? "text-pos" : "text-neg"}`}>
                  {f.pnl >= 0 ? "+" : ""}{f.pnl.toFixed(2)}
                </span>
              </div>
            ))
          )}
        </div>
        <div className="flex justify-between text-[11px] mt-2 pt-2 border-t border-border-soft">
          <span className="text-ink-4">{tr("合计")}</span>
          <span className={`grid-pnl-total font-mono font-semibold ${totalPnl >= 0 ? "text-pos" : "text-neg"}`}>
            {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)} USDT
          </span>
        </div>
      </V17Card>
      <GridControlPanel />
    </div>
  );
}
