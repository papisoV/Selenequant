"use client";

import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

// PairCard — dex 页专用,单个交易对卡片
// 旧代码 markets2/dex-pairs-panel.tsx:223-289

export interface DexPairData {
  chain_id: string;
  dex_id: string;
  pair_address: string;
  base_token_symbol: string;
  base_token_address: string;
  quote_token_symbol: string;
  quote_token_address: string;
  price_usd: number | null;
  price_native: number;
  volume_24h: number;
  liquidity_usd: number | null;
  tx_count_24h: number;
  price_change_24h: number | null;
  url: string | null;
  timestamp: number;
}

function formatNumber(num: number | null, decimals = 2): string {
  if (num === null || num === undefined) return "-";
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return num.toFixed(decimals);
}

export function PairCard({ pair, onSelect }: { pair: DexPairData; onSelect?: (pair: DexPairData) => void }) {
  const isPositive = (pair.price_change_24h ?? 0) >= 0;

  return (
    <div
      onClick={() => onSelect?.(pair)}
      className={cn(
        "p-2 rounded-lg border border-border-soft bg-surface-2 hover:bg-surface-3 cursor-pointer transition-colors",
        "space-y-1.5"
      )}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-[11px] text-ink">{pair.base_token_symbol}</span>
          <span className="text-ink-4">/</span>
          <span className="text-ink-3 text-[11px]">{pair.quote_token_symbol}</span>
        </div>
        {pair.price_change_24h !== null && pair.price_change_24h !== undefined && (
          <span
            className={cn(
              "text-[10px] font-mono",
              isPositive ? "text-pos" : "text-neg"
            )}
          >
            {isPositive ? "+" : ""}
            {pair.price_change_24h.toFixed(2)}%
          </span>
        )}
      </div>
      <div className="flex items-center justify-between text-[10px]">
        <span className="font-mono text-ink-2">
          {pair.price_usd !== null ? `$${formatNumber(pair.price_usd, 6)}` : "-"}
        </span>
        <span className="text-ink-4">
          Vol: ${formatNumber(pair.volume_24h)}
        </span>
      </div>
      <div className="flex items-center justify-between text-[10px] text-ink-4">
        <span>Liq: ${formatNumber(pair.liquidity_usd)}</span>
        <div className="flex items-center gap-1.5">
          <span className="capitalize">{pair.chain_id}</span>
          <span className="opacity-60">•</span>
          <span className="uppercase">{pair.dex_id}</span>
        </div>
      </div>
      {pair.url && (
        <a
          href={pair.url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center gap-1 text-[10px] text-gold hover:underline"
        >
          <ExternalLink className="h-3 w-3" />
          Dexscreener
        </a>
      )}
    </div>
  );
}
