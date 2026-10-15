"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { getWhaleTransfers } from "@/lib/api/onchain";
import type { WhaleTransfer } from "@/lib/api/onchain";

export const DEMO_FALLBACK_MS = 8000;

const DIRECTION_OPTIONS = ["all", "in", "out"] as const;
type DirectionFilter = (typeof DIRECTION_OPTIONS)[number];

const DIRECTION_LABEL: Record<string, string> = {
  in: "流入",
  out: "流出",
  internal: "内部",
};

export const DEMO_WHALE_TRANSFERS: WhaleTransfer[] = [
  { hash: "0x9a1f2b3c4d5e6f708192a3b4c5d6e7f8", token: "BTC", symbol: "BTC", value_usd: 82_400_000, amount: 1280, chain: "ethereum", direction: "in", confirmations: 24, ts: 1_753_500_000_000 },
  { hash: "0x8f2e1d4c5b6a79801122334455667788", token: "ETH", symbol: "ETH", value_usd: 41_200_000, amount: 12_800, chain: "ethereum", direction: "out", confirmations: 18, ts: 1_753_500_600_000 },
  { hash: "0x7a1b2c3d4e5f60718293a4b5c6d7e8f9", token: "SOL", symbol: "SOL", value_usd: 12_800_000, amount: 64_000, chain: "solana", direction: "in", confirmations: 30, ts: 1_753_501_200_000 },
  { hash: "0x6b5a4c3d2e1f00918273a4b5c6d7e8f90", token: "USDT", symbol: "USDT", value_usd: 55_000_000, amount: 55_000_000, chain: "tron", direction: "internal", confirmations: 12, ts: 1_753_501_800_000 },
  { hash: "0x5c4b3a2d1e0f8190273a4b5c6d7e8f901", token: "BTC", symbol: "BTC", value_usd: 210_000_000, amount: 3200, chain: "ethereum", direction: "out", confirmations: 42, ts: 1_753_502_400_000 },
];

function formatUSD(val: number | undefined): string {
  if (val == null) return "—";
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

function formatTime(ts: number | undefined): string {
  if (ts == null) return "—";
  return new Date(ts).toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function shortAddr(addr: string | undefined): string {
  if (!addr) return "—";
  return addr.length <= 12 ? addr : `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export function WhaleTransferFeed({ symbolFilter }: { symbolFilter?: string }) {
  const [items, setItems] = useState<WhaleTransfer[]>([]);
  const [source, setSource] = useState<"live" | "demo">("live");
  const [dirFilter, setDirFilter] = useState<DirectionFilter>("all");
  const liveRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    getWhaleTransfers()
      .then((res) => {
        if (cancelled) return;
        const list = res?.transfers ?? [];
        if (list.length === 0) return;
        liveRef.current = true;
        setItems(list);
        setSource("live");
      })
      .catch(() => {});
    const timer = setTimeout(() => {
      if (cancelled || liveRef.current) return;
      setItems(DEMO_WHALE_TRANSFERS);
      setSource("demo");
    }, DEMO_FALLBACK_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  const filtered = useMemo(() => {
    let list = items;
    if (dirFilter !== "all") {
      list = list.filter((t) => (t.direction ?? "internal") === dirFilter);
    }
    if (symbolFilter) {
      const base = symbolFilter.split("/")[0];
      list = list.filter(
        (t) =>
          (t.symbol ?? t.token ?? "").includes(base) ||
          (t.symbol ?? "") === symbolFilter ||
          (t.symbol ?? "") === `${base}-USDT` ||
          (t.symbol ?? "") === `${base}-USDT-SWAP`,
      );
    }
    return list;
  }, [items, dirFilter, symbolFilter]);

  const totalUsd = items.reduce((s, t) => s + (t.value_usd ?? 0), 0);
  const inCount = items.filter((t) => t.direction === "in").length;
  const outCount = items.filter((t) => t.direction === "out").length;

  return (
    <div className="space-y-3">
      {source === "demo" && (
        <div className="flex items-center gap-2">
          <span className="feed-demo-badge">演示</span>
          <span className="text-[10px] text-ink-4">数据管道不可用,展示演示巨鲸转账</span>
        </div>
      )}
      <div className="grid grid-cols-4 gap-2 text-xs">
        <div className="bg-surface-2 rounded p-2 text-center">
          <div className="font-mono font-bold text-ink-2">{items.length}</div>
          <div className="text-ink-4 text-[10px]">笔数</div>
        </div>
        <div className="bg-surface-2 rounded p-2 text-center">
          <div className="font-mono font-bold text-ink-2">{formatUSD(totalUsd)}</div>
          <div className="text-ink-4 text-[10px]">总金额</div>
        </div>
        <div className="bg-surface-2 rounded p-2 text-center">
          <div className="font-mono font-bold text-pos">{inCount}</div>
          <div className="text-ink-4 text-[10px]">流入</div>
        </div>
        <div className="bg-surface-2 rounded p-2 text-center">
          <div className="font-mono font-bold text-neg">{outCount}</div>
          <div className="text-ink-4 text-[10px]">流出</div>
        </div>
      </div>

      <div className="flex items-center gap-1.5 flex-wrap">
        {DIRECTION_OPTIONS.map((d) => (
          <button
            key={d}
            onClick={() => setDirFilter(d)}
            className={cn(
              "px-2.5 py-0.5 text-[11px] rounded transition-colors",
              dirFilter === d
                ? "bg-gold/20 text-gold"
                : "bg-surface-2 text-ink-3 hover:bg-surface-3",
            )}
          >
            {d === "all" ? "全部" : DIRECTION_LABEL[d]}
          </button>
        ))}
        {symbolFilter && (
          <span className="text-[11px] text-ink-3 px-2 py-0.5 bg-surface-2 rounded">
            {symbolFilter}
          </span>
        )}
      </div>

      <div className="max-h-[400px] overflow-y-auto space-y-1 hover-scrollbar">
        {filtered.length === 0 ? (
          <div className="text-center text-ink-4 text-xs py-8">等待巨鲸数据...</div>
        ) : (
          filtered.map((t, i) => {
            const dir = t.direction ?? "internal";
            const isIn = dir === "in";
            const isOut = dir === "out";
            return (
              <div
                key={t.hash ?? `${dir}-${i}`}
                data-direction={dir}
                className="flex items-center gap-2 px-2 py-1.5 rounded text-xs font-mono border-b border-border-soft"
              >
                <span className="w-14 shrink-0 text-ink-4">{formatTime(t.ts)}</span>
                <span className="w-14 truncate">{t.token ?? t.symbol ?? "—"}</span>
                <span
                  className={cn(
                    "w-16 text-right shrink-0",
                    isIn ? "text-pos" : isOut ? "text-neg" : "text-ink-3",
                  )}
                >
                  {formatUSD(t.value_usd)}
                </span>
                <span
                  className={cn(
                    "w-12 shrink-0 text-center rounded px-1 text-[10px]",
                    isIn ? "text-pos" : isOut ? "text-neg" : "text-ink-3",
                  )}
                >
                  {DIRECTION_LABEL[dir] ?? dir}
                </span>
                <span className="flex-1 min-w-0 truncate text-ink-3 flex items-center gap-1">
                  {isIn ? (
                    <ArrowUp className="h-3 w-3 text-pos shrink-0" />
                  ) : isOut ? (
                    <ArrowDown className="h-3 w-3 text-neg shrink-0" />
                  ) : (
                    <ArrowDown className="h-3 w-3 text-ink-3 shrink-0" />
                  )}
                  <span className="truncate">
                    {shortAddr(t.from)} → {shortAddr(t.to)}
                  </span>
                </span>
                <span className="w-14 shrink-0 text-right text-ink-4">
                  {t.confirmations != null ? `${t.confirmations} 确认` : "—"}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
