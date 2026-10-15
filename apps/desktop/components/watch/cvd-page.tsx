"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useContextStore } from "@/lib/store/context";

// 业务域插入页 — CVD (Cumulative Volume Delta)
// 后端 /api/v1/cvd/all 返回 { "5m": CVDBarEvent, "1h": …, "4h": … }
// (CVDBarEvent — derivatives.py:198,无 delta_* / trend 字段)
interface CvdBar {
  cvd_usd?: number;
  buy_notional_usd?: number;
  sell_notional_usd?: number;
  trade_count?: number;
}
type CvdAllData = Record<string, CvdBar>;

const INTERVALS = ["5m", "1h", "4h"] as const;

function signedUsd(v: number): string {
  const sign = v >= 0 ? "+" : "-";
  const abs = Math.abs(v);
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function usd(v: number | undefined): string {
  if (v == null) return "—";
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

export function CvdPage() {
  const { pageFilter } = useContextStore();
  const { symbol } = pageFilter;
  const { data } = useApi<CvdAllData | null>(
    `/api/v1/cvd/all?symbol=${symbol}`
  );

  const bars = data ?? {};
  const m5 = bars["5m"];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="CVD 概览">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          {INTERVALS.map((iv) => {
            const bar = bars[iv];
            const v = Number(bar?.cvd_usd ?? 0);
            return (
              <StatBlock
                key={iv}
                label={iv}
                value={bar != null ? signedUsd(v) : "—"}
                cls={v >= 0 ? "text-pos" : "text-neg"}
              />
            );
          })}
        </div>
      </V17Card>
      <V17Card title="5m 量能明细">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="主动买" value={usd(m5?.buy_notional_usd)} cls="text-pos" />
          <StatBlock label="主动卖" value={usd(m5?.sell_notional_usd)} cls="text-neg" />
          <StatBlock
            label="成交笔数"
            value={m5?.trade_count != null ? String(m5.trade_count) : "—"}
          />
        </div>
      </V17Card>
    </div>
  );
}
