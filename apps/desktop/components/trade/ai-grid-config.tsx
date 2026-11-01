"use client";

import { useState } from "react";
import { apiDirect } from "@/lib/api-client";
import { suggestGridRange, buildGridLevels, type GridRange } from "@/lib/quant/grid-suggest";
import { useUiText } from "@/components/i18n/use-ui-text"

export function AiGridConfig() {
  const tr = useUiText()

  const [price, setPrice] = useState("65000");
  const [vol, setVol] = useState("0.05");
  const [levels, setLevels] = useState("8");
  const [range, setRange] = useState<GridRange | null>(null);
  const [startResult, setStartResult] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const suggest = () => {
    const priceN = Number(price) || 0;
    const volN = Number(vol) || 0.05;
    const levelsN = Number(levels) || 8;
    setRange(suggestGridRange({ currentPrice: priceN, volatilityPct: volN, levelCount: levelsN }));
    setStartResult(null);
  };

  const start = async () => {
    if (!range) return;
    setStarting(true);
    setStartResult(null);
    try {
      const gridLevels = buildGridLevels(range);
      await apiDirect("/api/v1/grid/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upper: range.upper, lower: range.lower, levels: gridLevels }),
      });
      setStartResult("网格已启动(实时)");
    } catch {
      setStartResult("网格已启动(演示):后端不可达,区间已预览");
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="ai-grid-config flex flex-col gap-2">
      <div className="grid grid-cols-3 gap-2">
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("当前价")}
          <input aria-label={tr("当前价")} type="number" value={price} onChange={(e) => setPrice(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("波动率")}
          <input aria-label={tr("波动率")} type="number" value={vol} onChange={(e) => setVol(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("档位数")}
          <input aria-label={tr("档位数")} type="number" value={levels} onChange={(e) => setLevels(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
      </div>

      <div className="flex items-center gap-2">
        <button type="button" onClick={suggest}
          className="w-fit rounded bg-gold px-2 py-1 text-[10px] font-medium text-black transition-opacity hover:opacity-90">
          {tr("AI 建议区间")}
        </button>
        {range && (
          <button type="button" onClick={start} disabled={starting}
            className="w-fit rounded bg-gold px-2 py-1 text-[10px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50">
            {starting ? tr("启动中…") : tr("启动网格")}
          </button>
        )}
      </div>

      {range && (
        <>
          <div className="grid grid-cols-3 gap-2 text-[10px]">
            <div className="rounded border border-border-soft px-2 py-1">
              <div className="text-ink-4">{tr("上界")}</div>
              <div className="font-mono text-pos">{range.upper}</div>
            </div>
            <div className="rounded border border-border-soft px-2 py-1">
              <div className="text-ink-4">{tr("下界")}</div>
              <div className="font-mono text-neg">{range.lower}</div>
            </div>
            <div className="rounded border border-border-soft px-2 py-1">
              <div className="text-ink-4">{tr("每格")}</div>
              <div className="font-mono text-ink">{range.stepPercent}%</div>
            </div>
          </div>
          <div className="flex flex-wrap gap-1">
            {buildGridLevels(range).map((lv, i) => (
              <span key={i} className="grid-level rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[9px] text-ink-3">
                {lv}
              </span>
            ))}
          </div>
        </>
      )}

      {startResult && (
        <div className={`text-[10px] ${startResult.includes("演示") ? "text-gold" : "text-pos"}`}>
          {tr(startResult)}
          {startResult.includes("演示") && <span className="feed-demo-badge ml-2">{tr("演示")}</span>}
        </div>
      )}
    </div>
  );
}
