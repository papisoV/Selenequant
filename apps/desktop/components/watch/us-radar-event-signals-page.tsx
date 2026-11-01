"use client";

import { useState } from "react";
import { RefreshCw, ExternalLink } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useMarketStore } from "@/lib/store/market";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 事件信号 — /api/v1/us-radar/event-signals
// shape: { signals[], total, llm_enhanced, provider, data_mode, timestamp }
// signal: { event_id, symbol, event_type, headline, source, published_at, url,
//           sentiment_score, impact_score, confidence, signal_lane,
//           eligible_for_signal_flow, quality_flags[], suggested_actions[],
//           evidence[], risk_flags[], llm_enhanced }

interface EventSignal {
  event_id?: string;
  symbol?: string;
  event_type?: string;
  headline?: string;
  source?: string;
  published_at?: number;
  url?: string;
  impact_score?: number;
  confidence?: number;
  signal_lane?: string;
  suggested_actions?: string[];
  risk_flags?: string[];
  llm_enhanced?: boolean;
}
interface EventSignalsData {
  signals?: EventSignal[];
  total?: number;
  llm_enhanced?: boolean;
  data_mode?: string;
  timestamp?: number;
}

const EVENT_TYPE_LABEL: Record<string, string> = {
  earnings: "财报",
  regulatory: "监管",
  filing: "申报",
  mover: "异动",
  macro: "宏观",
  news_event: "新闻",
};

const LANE_STYLE: Record<string, string> = {
  high_priority: "bg-pos/20 text-pos",
  watchlist: "bg-gold/20 text-gold",
  background: "bg-surface-3 text-ink-3",
};

const bare = (s: string): string => s.replace(/[-/:].*$/, "");

function fmtTime(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString("en-US", { hour12: false });
}

export function EventSignalsPage() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, isLoading, mutate } = useApi<EventSignalsData | null>("/api/v1/us-radar/event-signals");
  const [refreshing, setRefreshing] = useState(false);
  const { setSymBase } = useMarketStore();

  const d = data ?? null;
  const signals = d?.signals ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const openSymbol = (sym: string) => {
    setSymBase(bare(sym));
    window.location.href = "/watch/us-radar-symbols-symbol";
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("事件信号")}
        right={
          <div className="flex items-center gap-2">
            <span className="text-[9px] text-ink-4 font-mono">
              {d ? tr("{p0} 条 · {p1} · {p2}", { p0: d.total ?? signals.length, p1: d.data_mode ?? "", p2: d.llm_enhanced ? "LLM" : tr("规则") }) : ""}
            </span>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || isLoading}
              aria-label={tr("刷新")}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        {signals.length === 0 ? (
          isLoading ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("加载中…")}</div>
          ) : (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          )
        ) : (
          <div className="flex flex-col">
            {signals.map((s) => (
              <div key={s.event_id} className="flex flex-col gap-1 border-b border-border-soft py-2 text-[10px]">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => openSymbol(s.symbol ?? "")}
                    className="w-14 truncate text-left font-mono text-gold hover:underline"
                  >
                    {s.symbol}
                  </button>
                  <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">
                    {tr(EVENT_TYPE_LABEL[s.event_type ?? ""] ?? s.event_type ?? "")}
                  </span>
                  <span className={`rounded px-1.5 py-0.5 text-[9px] ${LANE_STYLE[s.signal_lane ?? ""] ?? "bg-surface-3 text-ink-3"}`}>
                    {s.signal_lane}
                  </span>
                  {s.llm_enhanced && (
                    <span className="rounded bg-purple-400/20 px-1.5 py-0.5 text-[9px] text-purple-400">LLM</span>
                  )}
                  {s.risk_flags?.map((f) => (
                    <span key={f} className="rounded bg-neg/20 px-1.5 py-0.5 text-[9px] text-neg">{f}</span>
                  ))}
                  <div className="flex-1" />
                  <span className="font-mono text-ink-4">{fmtTime(s.published_at)}</span>
                </div>
                <div className="flex items-center gap-2 pl-0">
                  <span className="text-ink">{s.headline}</span>
                  {s.url && (
                    <a href={s.url} target="_blank" rel="noreferrer" className="text-ink-4 hover:text-gold" aria-label={tr("打开原文")}>
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
                <div className="flex items-center gap-3 pl-0 text-[9px] text-ink-4">
                  <span className="font-mono">
                    {tr("置信")} <span className="text-ink-2">{((s.confidence ?? 0) * 100).toFixed(0)}%</span>
                  </span>
                  <span className="font-mono">
                    {tr("影响")} <span className="text-ink-2">{((s.impact_score ?? 0) * 100).toFixed(0)}%</span>
                  </span>
                  {s.suggested_actions?.length ? (
                    <span>
                      {tr("建议 ·")}{" "}
                      <span className="text-ink-2">{s.suggested_actions.join(" / ")}</span>
                    </span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}
