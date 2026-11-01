"use client";

import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { PairCard, type DexPairData } from "./parts/pair-card";
import { DexQueryBar, useDexQuery } from "./parts/dex-query";
import { useUiText } from "@/components/i18n/use-ui-text"

// 孤儿页 dex-tokens-token-address(+_address 变体) → /api/v1/dex/tokens/{token_address}
// slug 是静态目录拿不到 path 参数,页面用查询条让用户填 链 + 代币地址。
// 后端返回 { pairs: DexPairData[], count, token_address, chain }

interface DexTokenResponse {
  pairs: DexPairData[];
  count: number;
  token_address: string;
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

export function DexTokensPage() {
  const tr = useUiText()

  const { query, setChain, setAddress, submit, active } = useDexQuery();

  const key = active ? `dex-tokens-${query.chain}-${query.committed}` : null;
  const path = active
    ? `/api/v1/dex/tokens/${encodeURIComponent(query.committed)}?chain=${encodeURIComponent(query.chain)}&limit=50`
    : null;
  const { data, isLoading } = useSWR<DexTokenResponse | null>(
    key,
    () => (path ? fetcher<DexTokenResponse>(path) : null),
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );

  const pairs = data?.pairs ?? [];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("DEX 代币")}
        right={<DexQueryBar query={query} onChainChange={setChain} onAddressChange={setAddress} onSubmit={submit} placeholder={tr("输入代币地址…")} />}
      >
        {!active ? (
          <div className="py-4 text-center text-[10px] text-ink-4">{tr("选择链并输入代币地址查询")}</div>
        ) : isLoading ? (
          <div className="py-4 text-center text-[10px] text-ink-4">{tr("加载中…")}</div>
        ) : (
          <>
            <div className="max-h-[320px] overflow-y-auto space-y-2">
              {pairs.length === 0 ? (
                <div className="py-4 text-center text-[10px] text-ink-4">{tr("无该代币的交易对")}</div>
              ) : (
                pairs.slice(0, 50).map((pair, idx) => (
                  <PairCard key={`${pair.chain_id}-${pair.pair_address}-${idx}`} pair={pair} />
                ))
              )}
            </div>
            <div className="pt-1.5 border-t border-border-soft text-[9px] text-ink-4 text-center">
              {tr("共")} {pairs.length} {tr("条 (")}{data?.chain ?? query.chain})
            </div>
          </>
        )}
      </V17Card>
      <V17Card title={tr("代币统计")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("交易对数")} value={String(pairs.length)} />
          <StatBlock
            label={tr("总流动性")}
            value={`$${fmt(pairs.reduce((s, p) => s + (p.liquidity_usd ?? 0), 0))}`}
            cls="text-gold"
          />
          <StatBlock
            label={tr("24h 交易量")}
            value={`$${fmt(pairs.reduce((s, p) => s + (p.volume_24h ?? 0), 0))}`}
          />
        </div>
      </V17Card>
    </div>
  );
}

function fmt(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}K` : String(n);
}
