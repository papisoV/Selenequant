"use client";

import { fmtNum } from "@/lib/assets/aggregate";
import type { AssetBalance } from "@/lib/assets/types";

export function AssetTable({ rows }: { rows: AssetBalance[] }) {
  if (rows.length === 0) {
    return <div className="py-3 text-center text-[10px] text-ink-4">该交易所暂无资产</div>;
  }
  return (
    <table className="w-full text-[10px]">
      <thead>
        <tr className="text-ink-4">
          <th className="py-1 text-left font-medium">资产</th>
          <th className="py-1 text-right font-medium">可用</th>
          <th className="py-1 text-right font-medium">冻结</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.asset} className="border-t border-border-soft">
            <td className="py-1 font-mono text-ink">{r.asset}</td>
            <td className="py-1 text-right font-mono text-ink">{fmtNum(r.free)}</td>
            <td className="py-1 text-right font-mono text-ink-3">{fmtNum(r.locked)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
