"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 社交确认 — /api/v1/intelligence/social/confirmation
// shape: { confirmations[{topic,subreddit,news_correlation,community_heat,sentiment_alignment,posts_count,comments_count}],
//          total, llm_enhanced, timestamp }

interface Confirmation {
  topic?: string;
  subreddit?: string;
  news_correlation?: boolean;
  community_heat?: number;
  sentiment_alignment?: string;
  posts_count?: number;
  comments_count?: number;
}
interface SocialConfirmationData {
  confirmations?: Confirmation[];
  total?: number;
  llm_enhanced?: boolean;
  timestamp?: number;
}

function alignCls(a?: string): string {
  if (a === "aligned") return "text-pos";
  if (a === "contradictory") return "text-neg";
  return "text-gold";
}

function alignBg(a?: string): string {
  if (a === "aligned") return "bg-pos/15 text-pos";
  if (a === "contradictory") return "bg-neg/15 text-neg";
  return "bg-gold/15 text-gold";
}

function heatCls(h?: number): string {
  if (h == null) return "text-ink-4";
  if (h >= 70) return "text-neg";
  if (h >= 40) return "text-gold";
  return "text-ink-2";
}

export function IntelligenceSocialConfirmationPage() {
  const tr = useUiText()

  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<SocialConfirmationData | null>(
    "/api/v1/intelligence/social/confirmation",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const confirmations = d?.confirmations ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("社交确认")}
        right={
          <span className="flex items-center gap-2">
            {d?.llm_enhanced && (
              <span className="rounded bg-purple-400/20 px-1.5 py-0.5 text-[9px] text-purple-400">LLM</span>
            )}
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || isLoading}
              aria-label={tr("刷新")}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </span>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={tr("话题")} value={String(d?.total ?? 0)} cls="text-ink" />
          <StatBlock label={tr("新闻相关")} value={String(confirmations.filter((c) => c.news_correlation).length)} cls="text-pos" />
          <StatBlock label={tr("方向一致")} value={String(confirmations.filter((c) => c.sentiment_alignment === "aligned").length)} cls="text-gold" />
          <StatBlock label={tr("高热度")} value={String(confirmations.filter((c) => (c.community_heat ?? 0) >= 70).length)} cls="text-neg" />
        </div>
      </V17Card>

      <V17Card title={tr("社交 × 新闻交叉验证 ({length})", { length: confirmations.length })}>
        {confirmations.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">
            {isLoading ? tr("加载中…") : tr("暂无数据")}
          </div>
        ) : (
          <div className="max-h-96 overflow-auto">
            {confirmations.map((c, i) => (
              <div key={i} className="border-b border-border-soft py-1.5 text-[10px]">
                <div className="flex items-center gap-2">
                  <span className="flex-1 text-ink truncate">{c.topic}</span>
                  <span className={`rounded px-1.5 py-0.5 text-[9px] font-mono ${alignBg(c.sentiment_alignment)}`}>
                    {c.sentiment_alignment ?? "—"}
                  </span>
                  <span className={`font-mono num w-12 text-right ${heatCls(c.community_heat)}`}>
                    heat {c.community_heat ?? 0}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[9px] text-ink-4">
                  <span>{c.subreddit}</span>
                  <span>{c.news_correlation ? tr("与新闻交叉") : tr("独立话题")}</span>
                  <span className="ml-auto font-mono">{c.posts_count ?? 0} {tr("帖 ·")} {c.comments_count ?? 0} {tr("评论")}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}
