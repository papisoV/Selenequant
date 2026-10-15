"use client";

import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { PairCard, type DexPairData } from "./parts/pair-card";
import { DexQueryBar, useDexQuery } from "./parts/dex-query";

// 孤儿页 dex-pair-chain-pair-address(+_address 变体) → /api/v1/dex/pair/{chain}/{pair_address}
// slug 是静态目录拿不到 path 参数,页面用查询条让用户填 链 + 地址。
// 后端返回 { pair: DexPairData | null, chain }

interface DexPairDetailResponse {
  pair: DexPairData | null;
  chain: string;
}

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

export function DexPairDetailPage() {
  const { query, setChain, setAddress, submit, active } = useDexQuery();

  const key = active ? `dex-pair-detail-${query.chain}-${query.committed}` : null;
  const path = active
    ? `/api/v1/dex/pair/${encodeURIComponent(query.chain)}/${encodeURIComponent(query.committed)}`
    : null;
  const { data, isLoading } = useSWR<DexPairDetailResponse | null>(
    key,
    () => (path ? fetcher<DexPairDetailResponse>(path) : null),
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );

  const pair = data?.pair ?? null;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="DEX 对链"
        right={<DexQueryBar query={query} onChainChange={setChain} onAddressChange={setAddress} onSubmit={submit} placeholder="输入 pair 地址…" />}
      >
        {!active ? (
          <div className="py-4 text-center text-[10px] text-ink-4">选择链并输入 pair 地址查询</div>
        ) : isLoading ? (
          <div className="py-4 text-center text-[10px] text-ink-4">加载中…</div>
        ) : pair ? (
          <div className="space-y-2">
            <PairCard pair={pair} />
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              <StatBlock label="价格 (USD)" value={pair.price_usd != null ? `$${pair.price_usd.toFixed(6)}` : "—"} />
              <StatBlock label="流动性" value={pair.liquidity_usd != null ? `$${fmt(pair.liquidity_usd)}` : "—"} cls="text-gold" />
              <StatBlock label="24h 交易量" value={`$${fmt(pair.volume_24h)}`} />
            </div>
          </div>
        ) : (
          <div className="py-4 text-center text-[10px] text-ink-4">
            {data?.chain ?? query.chain} 上未找到该 pair
          </div>
        )}
      </V17Card>
    </div>
  );
}

function fmt(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}K` : String(n);
}
