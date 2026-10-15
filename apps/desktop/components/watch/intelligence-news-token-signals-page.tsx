"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 代币信号 — /api/v1/intelligence/news/token-signals
// shape: { tokens{ BTC{ direction, score, confidence, key_events[], price_context{price, change_24h} } },
//          data_mode, llm_enhanced, timestamp }

interface TokenSignal {
  direction?: string;
  score?: number;
  confidence?: number;
  key_events?: string[];
  price_context?: { price?: number; change_24h?: number };
}
interface TokenSignalsData {
  tokens?: Record<string, TokenSignal>;
  data_mode?: string;
  llm_enhanced?: boolean;
  timestamp?: number;
}

function dirCls(d?: string): string {
  if (d === "bullish") return "text-pos";
  if (d === "bearish") return "text-neg";
  return "text-gold";
}

function scoreBg(s?: number): string {
  if (s == null) return "bg-ink-4/20";
  if (s >= 0.6) return "bg-pos";
  if (s <= 0.4) return "bg-neg";
  return "bg-gold";
}

export function IntelligenceNewsTokenSignalsPage() {
  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<TokenSignalsData | null>(
    "/api/v1/intelligence/news/token-signals",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const tokens = Object.entries(d?.tokens ?? {});

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="代币信号"
        right={
          <span className="flex items-center gap-2">
            <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 font-mono">
              {d?.data_mode ?? "—"}{d?.llm_enhanced ? " · LLM" : ""}
            </span>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || isLoading}
              aria-label="刷新"
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </span>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          {tokens.length === 0 ? (
            <div className="col-span-3 text-center text-ink-4 text-[11px] py-3">
              {isLoading ? "加载中…" : "暂无数据"}
            </div>
          ) : (
            tokens.map(([name, t]) => {
              const score = t.score ?? 0;
              return (
                <div key={name} className="rounded border border-border-soft bg-surface-2 p-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-semibold text-ink">{name}</span>
                    <span className={`ml-auto rounded bg-surface-3 px-1.5 py-0.5 text-[9px] font-mono ${dirCls(t.direction)}`}>
                      {t.direction ?? "—"}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <div className="h-1.5 flex-1 bg-surface-3 rounded overflow-hidden">
                      <div className={`h-full ${scoreBg(score)}`} style={{ width: `${Math.min(100, score * 100)}%` }} />
                    </div>
                    <span className="w-9 text-right font-mono num text-[9px] text-ink-2">{score.toFixed(2)}</span>
                  </div>
                  <div className="mt-1 flex justify-between text-[9px] text-ink-4">
                    <span>置信度 {t.confidence != null ? `${(t.confidence * 100).toFixed(0)}%` : "—"}</span>
                    <span>
                      {t.price_context?.price != null ? `$${t.price_context.price.toLocaleString()}` : "—"}
                      {t.price_context?.change_24h != null ? (
                        <span className={t.price_context.change_24h >= 0 ? " text-pos" : " text-neg"}>
                          {" "}{t.price_context.change_24h >= 0 ? "+" : ""}{t.price_context.change_24h}%
                        </span>
                      ) : null}
                    </span>
                  </div>
                  {(t.key_events?.length ?? 0) > 0 && (
                    <div className="mt-1.5 flex flex-col gap-0.5">
                      {t.key_events!.map((e, i) => (
                        <div key={i} className="text-[9px] text-ink-4 truncate">• {e}</div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </V17Card>
    </div>
  );
}
