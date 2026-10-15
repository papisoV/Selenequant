"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card } from "@/components/shared/page-filter-bar";

// 社交热点 — /api/v1/intelligence/social/trending
// shape: { topics[{title,subreddit,score,upvote_ratio,url,num_comments}], updated_at }

interface Topic {
  title?: string;
  subreddit?: string;
  score?: number;
  upvote_ratio?: number;
  url?: string;
  num_comments?: number;
}
interface SocialTrendingData {
  topics?: Topic[];
  updated_at?: number;
}

function fmtTime(ts?: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function scoreCls(s?: number): string {
  if (s == null) return "text-ink-4";
  if (s >= 2000) return "text-neg";
  if (s >= 500) return "text-gold";
  return "text-ink-2";
}

export function IntelligenceSocialTrendingPage() {
  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<SocialTrendingData | null>(
    "/api/v1/intelligence/social/trending",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const topics = d?.topics ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="社交热点"
        right={
          <span className="flex items-center gap-2">
            <span className="text-[9px] text-ink-4 font-mono">更新于 {fmtTime(d?.updated_at)}</span>
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
        {topics.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">
            {isLoading ? "加载中…" : "暂无数据"}
          </div>
        ) : (
          <div className="max-h-[480px] overflow-auto">
            {topics.map((t, i) => {
              const rank = i + 1;
              return (
                <div key={i} className="flex items-center gap-2 border-b border-border-soft py-1.5 text-[10px]">
                  <span className="w-6 text-right font-mono text-ink-4">#{rank}</span>
                  <div className="flex-1 min-w-0">
                    <a href={t.url} target="_blank" rel="noreferrer" className="block text-ink truncate hover:text-gold">
                      {t.title}
                    </a>
                    <div className="flex items-center gap-2 text-[9px] text-ink-4">
                      <span>{t.subreddit}</span>
                      <span>↑ {(t.upvote_ratio ?? 0) * 100}%</span>
                      <span className="font-mono">{t.num_comments ?? 0} 评论</span>
                    </div>
                  </div>
                  <span className={`font-mono num w-16 text-right ${scoreCls(t.score)}`}>
                    {t.score ?? 0}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>
    </div>
  );
}
