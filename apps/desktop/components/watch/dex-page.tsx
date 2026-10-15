"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw, Search, Loader2 } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { PairCard, type DexPairData } from "./parts/pair-card";

// 旧代码 markets2/dex-pairs-panel.tsx (303 行) 吸收
// 数据层: /api/v1/dex/pairs?chain=X&limit=50 + /api/v1/dex/search?q=Y
// 视觉骨架: V17Card + StatBlock + PairCard (新前端)
// 对接逻辑: 8 链切换 + 搜索 + onPairSelect 回调 + 外链 dexscreener

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

export function DexPage() {
  const [chain, setChain] = useState<string>("ethereum");
  const [searchQuery, setSearchQuery] = useState("");
  const [committedQuery, setCommittedQuery] = useState("");
  const [searchBump, setSearchBump] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const pairsKey = `dex-pairs-${chain}`;
  const pairsPath = `/api/v1/dex/pairs?chain=${chain}&limit=50`;
  const { data: pairsData, isLoading: pairsLoading, mutate: mutatePairs } = useSWR<DexPairsResponse | null>(
    pairsKey,
    () => fetcher<DexPairsResponse>(pairsPath),
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );

  const searchKey = committedQuery ? `dex-search-${committedQuery}-${searchBump}` : null;
  const searchPath = committedQuery ? `/api/v1/dex/search?q=${encodeURIComponent(committedQuery)}` : null;
  const { data: searchData, isLoading: searchLoading } = useSWR<DexPairsResponse | null>(
    searchKey,
    () => (searchPath ? fetcher<DexPairsResponse>(searchPath) : null),
    { revalidateOnFocus: false, dedupingInterval: 5000, keepPreviousData: true }
  );

  const realPairs = pairsData?.pairs ?? null;
  const pairs = realPairs ?? [];
  const searchResults = searchData?.pairs ?? null;
  const displayPairs = committedQuery && searchResults ? searchResults : pairs;

  // 聚合统计从 pairs 派生(总 TVL / 24h 交易量 / 24h 换手),避免死占位
  const sumLiquidity = displayPairs.reduce((s, p) => s + (p.liquidity_usd ?? 0), 0);
  const sumVolume = displayPairs.reduce((s, p) => s + (p.volume_24h ?? 0), 0);
  const sumTx = displayPairs.reduce((s, p) => s + (p.tx_count_24h ?? 0), 0);
  const totalLiq = sumLiquidity >= 1_000_000 ? `${(sumLiquidity / 1_000_000).toFixed(1)}M` : (sumLiquidity >= 1_000 ? `${(sumLiquidity / 1_000).toFixed(1)}K` : String(sumLiquidity));
  const totalVol = sumVolume >= 1_000_000 ? `${(sumVolume / 1_000_000).toFixed(1)}M` : (sumVolume >= 1_000 ? `${(sumVolume / 1_000).toFixed(1)}K` : String(sumVolume));

  const handleRefresh = () => {
    setRefreshing(true);
    // 真实重拉当前视图(链对或搜索结果)
    if (committedQuery) {
      setSearchBump((k) => k + 1);
    } else {
      mutatePairs();
    }
    setTimeout(() => setRefreshing(false), 600);
  };

  const loading = pairsLoading || searchLoading;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="DEX 流动性"
        right={
          <div className="flex items-center gap-1">
            <select
              value={chain}
              onChange={(e) => {
                setChain(e.target.value);
                setSearchQuery("");
                setCommittedQuery("");
              }}
              className="h-6 rounded border border-border-soft bg-surface-2 px-1 text-[10px] text-ink-2"
            >
              {CHAIN_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <div className="flex items-center gap-1">
              <input
                type="text"
                placeholder="搜索交易对..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setCommittedQuery(searchQuery);
                }}
                className="h-6 w-28 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink-2 placeholder:text-ink-4"
              />
              <button
                onClick={() => setCommittedQuery(searchQuery)}
                disabled={loading}
                className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
                aria-label="search"
              >
                {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Search className="h-3 w-3" />}
              </button>
            </div>
            <button
              onClick={handleRefresh}
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
          {displayPairs.length === 0 ? (
            <div className="py-4 text-center text-[10px] text-ink-4">无交易对</div>
          ) : (
            displayPairs.slice(0, 50).map((pair, idx) => (
              <PairCard key={`${pair.chain_id}-${pair.pair_address}-${idx}`} pair={pair} />
            ))
          )}
        </div>
        <div className="pt-1.5 border-t border-border-soft text-[9px] text-ink-4 text-center">
          共 {displayPairs.length} 条 {committedQuery ? `(搜索: "${committedQuery}")` : `(${chain})`}
        </div>
      </V17Card>
      <V17Card title="聚合统计">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="总 TVL" value={`$${totalLiq}`} />
          <StatBlock label="24h 交易量" value={`$${totalVol}`} />
          <StatBlock label="24h 换手次数" value={String(sumTx)} cls="text-gold" />
        </div>
      </V17Card>
    </div>
  );
}
