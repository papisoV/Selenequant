"use client";

import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import { PageFilterBar, V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { WorkspaceLink } from "@/components/shared/workspace-link";
import { useUiText } from "@/components/i18n/use-ui-text"

// 后端 /api/v1/signals/current 返回**单个** CompositeSignal 对象
// (derivatives.py:457 — symbol / exchange / side / strength 0-100 / factors[] /
//  confidence 0-1 / mark_price / timestamp)。无 src / ts_min / impact 字段。
interface SignalFactor {
  name?: string;
  value?: number;
  weight?: number;
  contribution?: number;
  direction?: string;
}

interface CompositeSignal {
  symbol?: string;
  exchange?: string;
  side?: string;
  strength?: number;
  factors?: SignalFactor[];
  confidence?: number;
  mark_price?: number;
}

const SIDE_LABEL: Record<string, string> = {
  buy: "看多",
  sell: "看空",
  neutral: "中性",
};

const DIR_LABEL: Record<string, string> = {
  bullish: "偏多",
  bearish: "偏空",
  neutral: "中性",
};

// 设计稿 L4456 renderPageSig — 信号中心
export function SigPage() {
  const tr = useUiText()

  const { pageFilter } = useContextStore();

  const { data, isLoading } = useApi<CompositeSignal | null>(
    `/api/v1/signals/current?symbol=${pageFilter.symbol}`
  );

  const sig = data ?? null;
  const strength = Number(sig?.strength ?? 0);
  const confidence = Number(sig?.confidence ?? 0);
  const side = sig?.side ?? "neutral";
  const sideCls = side === "buy" ? "text-pos" : side === "sell" ? "text-neg" : "text-ink-4";
  const factors = Array.isArray(sig?.factors) ? sig!.factors! : [];

  return (
    <div className="sig-feed flex h-full flex-col gap-2 overflow-y-auto">
      <div className="flex items-center justify-between">
        <PageFilterBar tfs={["1h", "4h", "24h"]} showEx={false} />
        <WorkspaceLink source="sig" />
      </div>

      {sig == null ? (
        <div className="py-6 text-center text-[10px] text-ink-4">
          {isLoading ? tr("加载中...") : tr("暂无信号数据")}
        </div>
      ) : (
        <>
          <V17Card title={tr("{p0} 综合信号", { p0: sig.symbol ?? "—" })}>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              <StatBlock label={tr("方向")} value={tr(SIDE_LABEL[side] ?? side)} cls={sideCls} />
              <StatBlock label={tr("强度")} value={strength.toFixed(1)} cls="text-gold" />
              <StatBlock label={tr("置信度")} value={`${(confidence * 100).toFixed(0)}%`} />
            </div>
          </V17Card>

          <V17Card title={tr("因子拆解")}>
            <div className="flex flex-col">
              {factors.length === 0 && (
                <div className="py-3 text-center text-[10px] text-ink-4">{tr("暂无因子")}</div>
              )}
              {factors.map((f, i) => {
                const dir = f.direction ?? "neutral";
                const cls =
                  dir === "bullish" ? "text-pos" : dir === "bearish" ? "text-neg" : "text-ink-4";
                return (
                  <div
                    key={f.name ?? i}
                    className="flex items-center gap-2 border-b border-border-soft py-1 text-[10px]"
                  >
                    <span className="w-20 truncate text-ink">{f.name ?? "—"}</span>
                    <span className="flex-1 text-right font-mono num text-ink-3">
                      w={Number(f.weight ?? 0).toFixed(2)}
                    </span>
                    <span className={`w-14 text-right font-mono num ${cls}`}>
                      {Number(f.value ?? 0).toFixed(2)}
                    </span>
                    <span className={`w-12 text-right ${cls}`}>{tr(DIR_LABEL[dir] ?? dir)}</span>
                  </div>
                );
              })}
            </div>
          </V17Card>
        </>
      )}
    </div>
  );
}
