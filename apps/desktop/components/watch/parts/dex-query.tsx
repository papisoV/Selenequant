"use client";

import { useState } from "react";
import { Search } from "lucide-react";

// DEX 参数化孤儿页共享查询条:
// slug 是静态目录(dex-pair-chain-pair-address 等拿不到 path 参数),
// 故页面用输入框让用户填 链 + 地址/符号,提交后驱动 useApi。

export const DEX_CHAINS = [
  "ethereum",
  "bsc",
  "solana",
  "arbitrum",
  "base",
  "polygon",
  "avalanche",
  "optimism",
] as const;

export interface DexQueryState {
  chain: string;
  address: string;
  committed: string; // 提交后的地址/符号,驱动请求
}

export function useDexQuery(initialChain = "ethereum"): {
  query: DexQueryState;
  setChain: (c: string) => void;
  setAddress: (a: string) => void;
  submit: () => void;
  active: boolean; // 已提交过(有生效请求)
} {
  const [chain, setChain] = useState<string>(initialChain);
  const [address, setAddress] = useState("");
  const [committed, setCommitted] = useState("");

  const submit = () => setCommitted(address.trim());

  return {
    query: { chain, address, committed },
    setChain,
    setAddress,
    submit,
    active: committed.length > 0,
  };
}

export function DexQueryBar({
  query,
  onChainChange,
  onAddressChange,
  onSubmit,
  placeholder = "输入地址 / 符号…",
}: {
  query: DexQueryState;
  onChainChange: (c: string) => void;
  onAddressChange: (a: string) => void;
  onSubmit: () => void;
  placeholder?: string;
}) {
  return (
    <div className="flex items-center gap-1">
      <select
        value={query.chain}
        onChange={(e) => onChainChange(e.target.value)}
        className="h-6 rounded border border-border-soft bg-surface-2 px-1 text-[10px] text-ink-2"
        aria-label="chain"
      >
        {DEX_CHAINS.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>
      <input
        type="text"
        placeholder={placeholder}
        value={query.address}
        onChange={(e) => onAddressChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onSubmit();
        }}
        className="h-6 w-40 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink-2 placeholder:text-ink-4"
      />
      <button
        onClick={onSubmit}
        className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
        aria-label="query"
      >
        <Search className="h-3 w-3" />
      </button>
    </div>
  );
}
