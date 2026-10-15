// lib/assets/aggregate.ts — 资产聚合纯函数(无副作用,可测)
import type { AssetBalance } from "./types";

export const DASH = "—";

// 从 asset-drawer.tsx:29/33/37 抽出共享,避免私有函数复制
export function fmtMoney(v: number): string {
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function fmtSignedMoney(v: number): string {
  return `${v >= 0 ? "+" : "-"}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function fmtPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function fmtNum(v: number): string {
  if (!Number.isFinite(v)) return DASH;
  return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

/** 同 (exchange,asset) 多行合并,free/locked 相加;不突变入参 */
export function aggregate(rows: AssetBalance[]): AssetBalance[] {
  const m = new Map<string, AssetBalance>();
  for (const r of rows) {
    const key = `${r.exchange}:${r.asset}`;
    const cur = m.get(key);
    if (cur) {
      m.set(key, { ...cur, free: cur.free + r.free, locked: cur.locked + r.locked });
    } else {
      m.set(key, { ...r });
    }
  }
  return [...m.values()];
}

/** 总估值(USD):Σ (free+locked) × priceMap[asset];缺价按 0 */
export function totalUsd(rows: AssetBalance[], priceMap: Record<string, number>): number {
  return rows.reduce((sum, r) => sum + (r.free + r.locked) * (priceMap[r.asset] ?? 0), 0);
}
