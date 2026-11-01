"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 新闻摘要 — /api/v1/intelligence/news/summary
// shape: { summary, headlines[{title,source,url,published_at,sentiment}], pulse{positive,negative,neutral}, llm_available }

interface Headline {
  title?: string;
  source?: string;
  url?: string;
  published_at?: number;
  sentiment?: string;
}
interface NewsSummaryData {
  summary?: string | null;
  headlines?: Headline[];
  pulse?: { positive?: number; negative?: number; neutral?: number };
  llm_available?: boolean;
}

function sentCls(s?: string): string {
  if (s === "positive") return "text-pos";
  if (s === "negative") return "text-neg";
  return "text-gold";
}

function fmtTime(ts?: number): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function IntelligenceNewsSummaryPage() {
  const tr = useUiText()

  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<NewsSummaryData | null>(
    "/api/v1/intelligence/news/summary",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const headlines = d?.headlines ?? [];
  const pulse = d?.pulse ?? {};
  const total = (pulse.positive ?? 0) + (pulse.negative ?? 0) + (pulse.neutral ?? 0) || 1;

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const pct = (n?: number) => Math.round(((n ?? 0) / total) * 100);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("新闻摘要")}
        right={
          <span className="flex items-center gap-2">
            <span className="text-[9px] text-ink-4">{d?.llm_available ? tr("LLM 摘要") : tr("规则摘要")}</span>
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
        <div className="rounded bg-surface-2 p-2 text-[10px] text-ink-2 leading-relaxed">
          {isLoading && !d ? tr("加载中…") : (d?.summary ?? tr("暂无摘要"))}
        </div>
        <div className="mt-3 grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("积极")} value={`${pct(pulse.positive)}%`} cls="text-pos" />
          <StatBlock label={tr("消极")} value={`${pct(pulse.negative)}%`} cls="text-neg" />
          <StatBlock label={tr("中性")} value={`${pct(pulse.neutral)}%`} cls="text-gold" />
        </div>
      </V17Card>

      <V17Card title={tr("头条 ({length})", { length: headlines.length })}>
        {headlines.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
        ) : (
          <div className="max-h-96 overflow-auto">
            {headlines.map((h, i) => (
              <div key={i} className="border-b border-border-soft py-1.5 text-[10px]">
                <a
                  href={h.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-ink hover:text-gold"
                >
                  {h.title}
                </a>
                <div className="mt-0.5 flex items-center gap-2">
                  <span className="text-ink-4">{h.source}</span>
                  <span className="text-ink-4 font-mono">{fmtTime(h.published_at)}</span>
                  <span className={`ml-auto font-mono ${sentCls(h.sentiment)}`}>{h.sentiment}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}
