"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { getKolSignals } from "@/lib/api/onchain";
import type { KolSignal } from "@/lib/api/onchain";
import { useUiText } from "@/components/i18n/use-ui-text";

export const DEMO_FALLBACK_MS = 8000;

const SENTIMENT_LABEL: Record<string, string> = {
  pos: "看多",
  neg: "看空",
  neutral: "中性",
};

export const DEMO_KOL_SIGNALS: KolSignal[] = [
  { id: "k1", author: "CryptoWhale", handle: "@cryptowhale", content: "BTC 巨鲸地址 3h 前累计增持 8,400 BTC,现货流入交易所后未见提走", symbol: "BTC", sentiment: "pos", ts: 1_753_500_000_000, source: "twitter" },
  { id: "k2", author: "OnChainSage", handle: "@onchainsage", content: "ETH 大额转账 128,000 ETH 至交易所,疑似派发", symbol: "ETH", sentiment: "neg", ts: 1_753_500_600_000, source: "twitter" },
  { id: "k3", author: "DeFiWatcher", handle: "@defiwatch", content: "SOL 质押解锁 640 万枚,链上活动升温", symbol: "SOL", sentiment: "neutral", ts: 1_753_501_200_000, source: "news" },
  { id: "k4", author: "MemPoolAlert", handle: "@mempoolalert", content: "UNI 巨鲸将 2,100 万枚转入做市商地址", symbol: "UNI", sentiment: "neutral", ts: 1_753_501_800_000, source: "twitter" },
  { id: "k5", author: "StableFlow", handle: "@stableflow", content: "USDT 新增 5.5 亿枚铸造,Tether 金库增持持续", symbol: "USDT", sentiment: "pos", ts: 1_753_502_400_000, source: "news" },
];

function formatTime(ts: number | undefined): string {
  if (ts == null) return "—";
  return new Date(ts).toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function KolSignalFeed() {
  const tr = useUiText();
  const [items, setItems] = useState<KolSignal[]>([]);
  const [source, setSource] = useState<"live" | "demo">("live");
  const liveRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    getKolSignals()
      .then((list) => {
        if (cancelled || list.length === 0) return;
        liveRef.current = true;
        setItems(list);
        setSource("live");
      })
      .catch(() => {});
    const timer = setTimeout(() => {
      if (cancelled || liveRef.current) return;
      setItems(DEMO_KOL_SIGNALS);
      setSource("demo");
    }, DEMO_FALLBACK_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  return (
    <div className="space-y-2">
      {source === "demo" && (
        <div className="flex items-center gap-2">
          <span className="feed-demo-badge">{tr("演示")}</span>
          <span className="text-[10px] text-ink-4">{tr("数据管道不可用,展示演示 KOL 信号")}</span>
        </div>
      )}
      <div className="max-h-[400px] overflow-y-auto space-y-1 hover-scrollbar">
        {items.length === 0 ? (
          <div className="text-center text-ink-4 text-xs py-8">{tr("等待 KOL 信号...")}</div>
        ) : (
          items.map((s, i) => {
            const sent = s.sentiment ?? "neutral";
            return (
              <div
                key={s.id ?? `${s.author}-${i}`}
                className="flex items-start gap-2 px-2 py-1.5 rounded text-xs border-b border-border-soft"
              >
                <span className="w-8 h-8 rounded-full bg-surface-2 flex items-center justify-center text-[10px] font-bold text-ink-2 shrink-0">
                  {(s.author ?? "K").slice(0, 1).toUpperCase()}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium text-ink-2 truncate">{s.author ?? s.handle ?? "KOL"}</span>
                    <span className="text-ink-4 text-[10px] truncate">{s.handle ?? ""}</span>
                    <span
                      className={cn(
                        "ml-auto text-[10px] px-1 rounded shrink-0",
                        sent === "pos" ? "text-pos" : sent === "neg" ? "text-neg" : "text-ink-4",
                      )}
                    >
                      {tr(SENTIMENT_LABEL[sent])}
                    </span>
                    <span className="text-ink-4 text-[10px] font-mono shrink-0">{formatTime(s.ts)}</span>
                  </div>
                  <p className="mt-0.5 text-ink-3 leading-relaxed line-clamp-2">
                    {s.content ?? s.summary ?? "—"}
                  </p>
                  <div className="mt-1 flex items-center gap-1.5">
                    {s.symbol && (
                      <span className="text-[10px] px-1 rounded bg-surface-2 text-ink-2 font-mono">
                        {s.symbol}
                      </span>
                    )}
                    <span className="text-[10px] text-ink-4">{s.source ?? ""}</span>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
