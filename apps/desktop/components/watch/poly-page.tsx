"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card } from "@/components/shared/page-filter-bar";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { getPolyEvent, type PolyEventDetail } from "@/lib/api/polymarket";
import { Loader2, Info, RefreshCw } from "lucide-react";
import { EventsListCard } from "./polymarket-pages";
import { useUiText, useLocale } from "@/components/i18n/use-ui-text"

/**
 * 标题/问题成对返回 `x_zh` 与 `x` 两个字段(后端同时带中英),不是同一文案的
 * 两种译法 —— en 下必须取英文原字段,否则整块内容在英文界面里仍是中文。
 * 单边缺失时才回落到另一边。
 */
const pickZh = (zh: string | undefined, en: string | undefined, locale: string): string | undefined =>
  (locale === "en" ? en || zh : zh || en);

// 后端 /api/v1/polymarket/events 返回 {events: [{title, title_zh, markets: [{question, outcomes: [{name, price}]}], volume_24h}], count}
interface PolyOutcome { name?: string; price?: number; }
interface PolyMarket { question?: string; question_zh?: string; outcomes?: PolyOutcome[]; volume?: number; }
interface PolyEvent {
  id?: string;
  title?: string;
  title_zh?: string;
  markets?: PolyMarket[];
  volume_24h?: number;
  volume?: number;
  category?: string;
  category_zh?: string;
  market_count?: number;
}
interface PolyResponse { events?: PolyEvent[]; count?: number; }

const EVENT_CATEGORIES = [
  { value: "", label: "全部" },
  { value: "crypto", label: "加密" },
  { value: "politics", label: "政治" },
  { value: "sports", label: "体育" },
  { value: "finance", label: "金融" },
  { value: "geopolitics", label: "地缘" },
  { value: "tech", label: "科技" },
  { value: "science", label: "科学" },
];

// 设计稿 L4525 renderPagePoly — 预测市场 (Polymarket)
export function PolyPage() {
  const tr = useUiText()
  const locale = useLocale()

  const [category, setCategory] = useState("");
  const [limit, setLimit] = useState(10);
  const [force, setForce] = useState(0);
  const path = `/api/v1/polymarket/events?limit=${limit}${category ? `&category=${category}` : ""}${force > 0 ? `&force=${force}` : ""}`;
  const { data: polyReal, isLoading } = useApi<PolyResponse>(path);

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailEvent, setDetailEvent] = useState<PolyEventDetail | null>(null);

  async function openEventDetail(eventId: string) {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetailEvent(null);
    try {
      const data = await getPolyEvent(eventId);
      setDetailEvent(data);
    } catch {
      setDetailEvent(null);
    } finally {
      setDetailLoading(false);
    }
  }

  const markets = (() => {
    const events = polyReal?.events;
    if (!events || events.length === 0) return [];
    const out: { id: string; q: string; p: number; vol: string }[] = [];
    events.forEach((ev) => {
      const evMarkets = ev.markets || [];
      evMarkets.forEach((m) => {
        if (out.length >= 10) return;
        const outcomes = m.outcomes || [];
        const yesOutcome = outcomes.find((o) => (o.name || "").toLowerCase() === "yes") || outcomes[0];
        const p = yesOutcome?.price != null ? Number(yesOutcome.price) : 0.5;
        const volNum = Number(m.volume ?? ev.volume_24h ?? 0);
        const vol = volNum >= 1e6 ? `${(volNum / 1e6).toFixed(1)}M` : `${Math.round(volNum / 1e3)}K`;
        const q =
          pickZh(m.question_zh, m.question, locale) ??
          pickZh(ev.title_zh, ev.title, locale) ??
          "?";
        out.push({ id: ev.id ?? "", q, p, vol });
      });
    });
    return out;
  })();

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("活跃市场")}
        right={
          <div className="flex items-center gap-1">
            {EVENT_CATEGORIES.map((c) => (
              <button
                key={c.value}
                onClick={() => setCategory(c.value)}
                className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${
                  category === c.value
                    ? "bg-gold/20 text-gold"
                    : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                }`}
              >
                {tr(c.label)}
              </button>
            ))}
            <span className="text-[9px] text-ink-4">|</span>
            {[10, 20, 50].map((l) => (
              <button
                key={l}
                onClick={() => setLimit(l)}
                className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${
                  limit === l ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"
                }`}
              >
                {l}
              </button>
            ))}
            <button
              onClick={() => setForce((f) => f + 1)}
              className="flex items-center gap-0.5 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-sky-500/15 hover:text-sky-400"
              title={tr("刷新")}
            >
              <RefreshCw className="h-2.5 w-2.5" />
            </button>
          </div>
        }
      >
        <div className="poly-list flex flex-col gap-1.5">
          {markets.length === 0 && !isLoading && (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          )}
          {markets.map((m, i) => {
            const yes = (m.p * 100).toFixed(0);
            const no = (100 - m.p * 100).toFixed(0);
            return (
              <div
                key={i}
                className="poly-row flex items-center gap-2 text-[10px] py-1 border-b border-border-soft"
              >
                <div className="poly-q flex-1 text-ink">{m.q}</div>
                <div className="poly-bar flex h-2.5 w-40 overflow-hidden rounded-sm bg-surface-2">
                  <div className="poly-yes bg-pos" style={{ width: `${yes}%` }} />
                  <div className="poly-no bg-neg" style={{ width: `${no}%` }} />
                </div>
                <div className="poly-prices flex gap-2 font-mono">
                  <span className="text-pos">YES {yes}¢</span>
                  <span className="text-neg">NO {no}¢</span>
                </div>
                <div className="poly-vol w-12 text-right text-ink-4 font-mono">{m.vol}</div>
                {m.id && (
                  <button
                    onClick={() => openEventDetail(m.id)}
                    className="rounded bg-surface-3 text-ink-3 px-1.5 py-0.5 text-[9px] hover:bg-sky-500/15 hover:text-sky-400 flex items-center gap-0.5"
                    title={tr("事件详情")}
                  >
                    <Info className="h-2.5 w-2.5" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </V17Card>
      <V17Card
        title={tr("事件列表")}
        right={<span className="text-[9px] text-ink-4">{polyReal?.count ?? 0} {tr("个")}</span>}
      >
        <EventsListCard
          events={polyReal?.events ?? []}
          isLoading={isLoading}
          onOpenDetail={(id) => openEventDetail(id)}
        />
      </V17Card>
      <V17Card title={tr("说明")}>
        <div className="text-[10px] text-ink-4 leading-relaxed">
          {tr("数据源: Polymarket API")}<br />
          {tr("价格 = 市场隐含概率")}<br />
          {tr("可用于: 宏观对冲 / 事件驱动策略")}
        </div>
      </V17Card>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Info className="h-4 w-4 text-sky-400" />
              {pickZh(detailEvent?.title_zh, detailEvent?.title, locale) || tr("事件详情")}
            </DialogTitle>
            <DialogDescription>
              {tr(detailEvent?.category_zh || detailEvent?.category || "—")} |{" "}
              {detailEvent?.market_count ?? detailEvent?.markets?.length ?? 0} {tr("个市场 |")}{" "}
              {tr("24h 量:")} {Number(detailEvent?.volume_24h ?? 0).toLocaleString()}
            </DialogDescription>
          </DialogHeader>
          {detailLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : detailEvent ? (
            <div className="space-y-3">
              {(detailEvent.description_zh || detailEvent.description) && (
                <div className="text-xs text-ink-3">
                  {detailEvent.description_zh || detailEvent.description}
                </div>
              )}
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div>
                  <div className="text-muted-foreground">{tr("流动性")}</div>
                  <div className="font-mono">{Number(detailEvent.liquidity ?? 0).toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">{tr("总成交量")}</div>
                  <div className="font-mono">{Number(detailEvent.volume ?? 0).toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">{tr("结束日期")}</div>
                  <div className="font-mono">{detailEvent.end_date?.slice(0, 10) ?? "—"}</div>
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">{tr("市场列表")}</div>
                <div className="space-y-1.5">
                  {(detailEvent.markets ?? []).map((m, idx) => (
                    <div key={m.id ?? idx} className="p-2 border border-border-soft rounded text-[11px]">
                      <div className="font-medium text-ink">
                        {pickZh(m.question_zh, m.question, locale) || "—"}
                      </div>
                      <div className="flex gap-3 mt-1 text-[10px] text-ink-3 font-mono">
                        {(m.outcomes ?? []).map((o, oi) => (
                          <span key={oi}>
                            {o.name}: {(Number(o.price ?? 0) * 100).toFixed(0)}¢
                          </span>
                        ))}
                        {m.volume_24h != null && <span>24h: {Number(m.volume_24h).toLocaleString()}</span>}
                        {m.best_bid != null && <span>Bid: {Number(m.best_bid).toFixed(2)}</span>}
                        {m.best_ask != null && <span>Ask: {Number(m.best_ask).toFixed(2)}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground text-sm">{tr("加载失败")}</div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
