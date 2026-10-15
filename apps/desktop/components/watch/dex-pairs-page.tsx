"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { PairCard, type DexPairData } from "./parts/pair-card";

// 孤儿页 dex-pairs → /api/v1/dex/pairs?chain=X&limit=50
// 专属组件:链筛选 + 真实 pairs + 派生聚合统计

interface DexPairsResponse {
  pairs: DexPairData[];
  count: number;
  chain: string;
}

const CHAIN_OPTIONS = [
  { value: "ethereum", label: "Ethereum" },
  { value: "bsc", label: "BSC" },
  { value: "solana", label: "Solana" },
  { value: "arbitrum", label: "Arbitrum" },
  { value: "base", label: "Base" },
  { value: "polygon", label: "Polygon" },
  { value: "avalanche", label: "Avalanche" },
  { value: "optimism", label: "Optimism" },
] as const;

async function fetcher<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    if (!res.ok) return null;
    const body = await res.json();
    if (body && typeof body === "object" && "data" in body && "meta" in body) {
      if (body.error) return null;
      return (body.data ?? null) as T;
    }
    return body as T;
  } catch {
    return null;
  }
}

export function DexPairsPage() {
  const [chain, setChain] = useState<string>("ethereum");
  const [refreshing, setRefreshing] = useState(false);

  const key = `dex-pairs-orphan-${chain}`;
  const path = `/api/v1/dex/pairs?chain=${chain}&limit=50`;
  const { data, mutate } = useSWR<DexPairsResponse | null>(key, () => fetcher<DexPairsResponse>(path), {
    revalidateOnFocus: false,
    dedupingInterval: 5000,
  });

  const d = data ?? null;
  const pairs = d?.pairs ?? [];

  const sumLiquidity = pairs.reduce((s, p) => s + (p.liquidity_usd ?? 0), 0);
  const sumVolume = pairs.reduce((s, p) => s + (p.volume_24h ?? 0), 0);
  const sumTx = pairs.reduce((s, p) => s + (p.tx_count_24h ?? 0), 0);
  const fmt = (n: number) =>
    n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}K` : String(n);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="DEX 交易对"
        right={
          <div className="flex items-center gap-1">
            <select
              value={chain}
              onChange={(e) => setChain(e.target.value)}
              className="h-6 rounded border border-border-soft bg-surface-2 px-1 text-[10px] text-ink-2"
            >
              {CHAIN_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <button
              onClick={() => {
                setRefreshing(true);
                mutate();
                setTimeout(() => setRefreshing(false), 600);
              }}
              disabled={refreshing}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
              aria-label="refresh"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        <div className="max-h-[400px] overflow-y-auto space-y-2">
          {pairs.length === 0 ? (
            <div className="py-4 text-center text-[10px] text-ink-4">无交易对</div>
          ) : (
            pairs.slice(0, 50).map((pair, idx) => (
              <PairCard key={`${pair.chain_id}-${pair.pair_address}-${idx}`} pair={pair} />
            ))
          )}
        </div>
        <div className="pt-1.5 border-t border-border-soft text-[9px] text-ink-4 text-center">
          共 {pairs.length} 条 ({chain})
        </div>
      </V17Card>
      <V17Card title="聚合统计">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="总 TVL" value={`$${fmt(sumLiquidity)}`} />
          <StatBlock label="24h 交易量" value={`$${fmt(sumVolume)}`} />
          <StatBlock label="24h 换手" value={String(sumTx)} cls="text-gold" />
        </div>
      </V17Card>
    </div>
  );
}
