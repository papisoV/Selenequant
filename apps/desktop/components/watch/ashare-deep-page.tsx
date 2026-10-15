"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card } from "@/components/shared/page-filter-bar";

// A股深度研究页 — 数据源 AKShare(经 /api/v1/ashare_deep/*)
// 端点: fund-flow/{symbol} / margin/{symbol} / lhb?date= / northbound
// 表格按 akshare 列名通用渲染(研究终端,列可变),空数据降级 "暂无"

type Row = Record<string, unknown>;

const todayStr = () => new Date().toISOString().slice(0, 10);

const fmtVal = (v: unknown): string => {
  if (v == null || v === "") return "—";
  if (typeof v === "number") return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return String(v);
};

function DataTable({ rows, max = 10, empty }: { rows: Row[]; max?: number; empty: string }) {
  if (!rows.length) {
    return <p className="py-2 text-center text-[11px] text-ink-4">{empty}</p>;
  }
  const keys = Object.keys(rows[0]).slice(0, 6);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] font-mono">
        <thead>
          <tr className="text-ink-4 text-left">
            {keys.map((k) => (
              <th key={k} className="py-1 pr-3">{k}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(-max).map((r, i) => (
            <tr key={i} className="border-t border-border-soft text-ink">
              {keys.map((k) => (
                <td key={k} className="py-1 pr-3">{fmtVal(r[k])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AshareDeepPage() {
  const [symbol, setSymbol] = useState("600519");
  const [active, setActive] = useState("600519");
  const [lhbDate, setLhbDate] = useState(todayStr());

  const fund = useApi<Row[] | null>(`/api/v1/ashare/fund-flow/${active}`);
  const margin = useApi<Row[] | null>(`/api/v1/ashare/margin/${active}`);
  const lhb = useApi<Row[] | null>(`/api/v1/ashare/lhb?date=${lhbDate.replace(/-/g, "")}`);
  const northbound = useApi<Row[] | null>(`/api/v1/ashare/northbound`);

  const submit = () => {
    const code = symbol.trim();
    if (/^\d{6}$/.test(code)) setActive(code);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="A股深度研究 (AKShare)">
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="bg-surface-2 border border-border-soft rounded px-2 py-1 text-[12px] text-ink w-28"
            placeholder="6位代码"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <button
            className="bg-surface-2 border border-border-soft rounded px-2 py-1 text-[11px] text-ink hover:bg-surface"
            onClick={submit}
          >
            查询
          </button>
          <span className="text-[11px] text-ink-3">当前: <span className="font-mono text-gold">{active}</span></span>
          <span className="text-[11px] text-ink-4">数据源 AKShare,可选依赖(未装时返回 404+提示,不 500)</span>
        </div>
      </V17Card>

      <V17Card title="资金流 (东财个股)">
        <DataTable rows={fund.data ?? []} max={10} empty="暂无资金流数据(可能未装 akshare 或数据源无此区间)" />
      </V17Card>

      <V17Card title="融资融券">
        <DataTable rows={margin.data ?? []} max={10} empty="暂无融资融券数据" />
      </V17Card>

      <V17Card title="龙虎榜" right={lhbDate}>
        <div className="mb-2">
          <input
            type="date"
            className="bg-surface-2 border border-border-soft rounded px-2 py-1 text-[12px] text-ink"
            value={lhbDate}
            onChange={(e) => e.target.value && setLhbDate(e.target.value)}
          />
          <span className="text-[11px] text-ink-4 ml-2">选择交易日</span>
        </div>
        <DataTable rows={lhb.data ?? []} max={15} empty="暂无该日龙虎榜(可能是非交易日或数据未发布)" />
      </V17Card>

      <V17Card title="北向资金 (每日)">
        <DataTable rows={northbound.data ?? []} max={10} empty="暂无北向资金数据" />
      </V17Card>
    </div>
  );
}
