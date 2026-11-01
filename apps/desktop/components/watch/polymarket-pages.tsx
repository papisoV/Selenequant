"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { RefreshCw, Info, Trash2, Plus, Shield } from "lucide-react";
import { useUiText, useLocale } from "@/components/i18n/use-ui-text"

// ===========================================================================
// Polymarket 13 页专属组件 — fork/r4-polymarket
//
// 每页自解自己的 API shape(不碰全局 lib/api/*),WIDGET_REGISTRY 命中后不再走
// 通用渲染器。参数化路由(self 解参):页面内用输入框让用户填 {token_id}/
// {condition_id}/{event_id}/{address},默认值兜底可直接打开有数。
// ===========================================================================

// --------------------------- 通用工具 ------------------------------------

function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function fmtNum(v: unknown, digits = 0): string {
  const n = num(v);
  if (n === 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtCents(p: unknown): string {
  const n = num(p);
  return `${(n * 100).toFixed(0)}¢`;
}

function addrShort(a: string | undefined, len = 10): string {
  if (!a) return "—";
  return a.length > len * 2 ? `${a.slice(0, len)}…${a.slice(-6)}` : a;
}

function Pct({ value }: { value: unknown }) {
  const n = num(value);
  return <span className={n >= 0 ? "text-pos" : "text-neg"}>{n.toFixed(1)}%</span>;
}

// 通用刷新按钮(真接线:force 重新挂载 useApi path → SWR mutate)
function RefreshBtn({ onClick }: { onClick: () => void }) {
  const tr = useUiText()

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-0.5 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-sky-500/15 hover:text-sky-400"
      title={tr("刷新")}
    >
      <RefreshCw className="h-2.5 w-2.5" />
    </button>
  );
}

// 输入框(参数化页自解参数用)
function ParamInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="flex items-center gap-1 text-[9px] text-ink-4">
      <span>{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-5 w-40 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] font-mono text-ink-2"
      />
    </label>
  );
}

function EmptyState({ text }: { text?: string }) {
  const tr = useUiText()

  return (
    <div className="text-center text-[11px] text-ink-4 py-4">{text ?? tr("暂无数据")}</div>
  );
}

// --------------------------- 1. poly / events -----------------------------

interface PolyOutcome { name?: string; price?: number; winner?: boolean }
interface PolyMarket { id?: string; question?: string; question_zh?: string; condition_id?: string; volume?: number; volume_24h?: number; outcomes?: PolyOutcome[]; best_bid?: number; best_ask?: number }
interface PolyEvent { id?: string; title?: string; title_zh?: string; category?: string; category_zh?: string; volume?: number; volume_24h?: number; liquidity?: number; market_count?: number; end_date?: string }
interface PolyEventsResponse { events?: PolyEvent[]; count?: number; categories?: string[]; categories_zh?: Record<string, string> }

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

const eventPath = (category: string, limit: number) =>
  `/api/v1/polymarket/events?limit=${limit}${category ? `&category=${category}` : ""}`;

/**
 * `x_zh` 与 `x` 是后端成对返回的中英两个字段,不是同一文案的两种译法。
 * en 界面取英文原字段;只有单边缺失时才回落到另一边(总比空白好)。
 */
const pickZh = (zh: string | undefined, en: string | undefined, locale: string): string | undefined =>
  locale === "en" ? en || zh : zh || en;

function eventVol(ev: PolyEvent): string {
  return fmtNum(ev.volume_24h ?? ev.volume ?? 0);
}

// 事件列表(供 poly-page 与 events 页复用)
export function EventsListCard({
  events,
  isLoading,
  onOpenDetail,
}: {
  events: PolyEvent[];
  isLoading: boolean;
  onOpenDetail?: (id: string) => void;
}) {
  const tr = useUiText()
  const locale = useLocale()

  if (events.length === 0) return isLoading ? <EmptyState text={tr("加载中…")} /> : <EmptyState />;
  return (
    <div className="flex flex-col">
      {events.map((ev, i) => {
        const vol = eventVol(ev);
        return (
          <div
            key={ev.id ?? i}
            className="flex items-center gap-2 text-[10px] py-1.5 border-b border-border-soft"
          >
            <div className="flex-1 text-ink truncate">
              {pickZh(ev.title_zh, ev.title, locale) || "?"}
              {ev.category_zh ? (
                <span className="ml-1.5 rounded bg-surface-3 px-1 py-0.5 text-[8px] text-ink-3">
                  {tr(ev.category_zh)}
                </span>
              ) : null}
            </div>
            <div className="w-10 text-right font-mono text-ink-3">{vol}</div>
            <div className="w-8 text-right font-mono text-ink-4">{ev.market_count ?? 0}{tr("市")}</div>
            {onOpenDetail && ev.id && (
              <button
                onClick={() => onOpenDetail(ev.id!)}
                className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-sky-500/15 hover:text-sky-400 flex items-center gap-0.5"
                title={tr("事件详情")}
              >
                <Info className="h-2.5 w-2.5" />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

// --------------------------- 2. events 列表页 -----------------------------

export function PolymarketEventsPage() {
  const tr = useUiText()

  const [category, setCategory] = useState("");
  const [force, setForce] = useState(0);
  const path = eventPath(category, 20);
  const { data, isLoading } = useApi<PolyEventsResponse>(
    force > 0 ? `${path}&force=${force}` : path
  );
  const events = data?.events ?? [];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 事件")}
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
            <RefreshBtn onClick={() => setForce((f) => f + 1)} />
          </div>
        }
      >
        <EventsListCard events={events} isLoading={isLoading} />
      </V17Card>
    </div>
  );
}

// --------------------------- 3. event detail -----------------------------

interface EventDetailMarket {
  id?: string;
  question?: string;
  question_zh?: string;
  outcomes?: PolyOutcome[];
  volume_24h?: number;
  best_bid?: number;
  best_ask?: number;
  last_trade_price?: number;
}
interface EventDetail {
  id?: string;
  title?: string;
  title_zh?: string;
  category?: string;
  category_zh?: string;
  volume?: number;
  volume_24h?: number;
  liquidity?: number;
  start_date?: string;
  end_date?: string;
  market_count?: number;
  markets?: EventDetailMarket[];
}

export function PolymarketEventDetailPage() {
  const tr = useUiText()
  const locale = useLocale()

  const [eventId, setEventId] = useState("btc-updown-5m");
  const path = `/api/v1/polymarket/events/${encodeURIComponent(eventId)}`;
  const { data: d, isLoading } = useApi<EventDetail>(path);

  const markets = d?.markets ?? [];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("PM 事件详情")}>
        <div className="mb-2 flex items-center gap-2">
          <ParamInput label="event_id" value={eventId} onChange={setEventId} placeholder="btc-updown-5m" />
        </div>
        {isLoading && markets.length === 0 ? (
          <EmptyState text={tr("加载中…")} />
        ) : !d ? (
          <EmptyState />
        ) : (
          <div className="space-y-2">
            <div className="text-[12px] text-ink font-medium">
              {d.title_zh || d.title || "?"}
            </div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
              <StatBlock label={tr("24h 量")} value={fmtNum(d.volume_24h ?? d.volume ?? 0)} />
              <StatBlock label={tr("流动性")} value={fmtNum(d.liquidity)} />
              <StatBlock label={tr("市场数")} value={d.market_count ?? markets.length} />
              <StatBlock label={tr("分类")} value={tr(pickZh(d.category_zh, d.category, locale) || "—")} />
            </div>
            {(d.start_date || d.end_date) && (
              <div className="text-[9px] text-ink-4 font-mono">
                {(d.start_date || "").slice(0, 10)} → {(d.end_date || "").slice(0, 10)}
              </div>
            )}
            <div className="space-y-1.5">
              {markets.map((m, idx) => (
                <div key={m.id ?? idx} className="rounded border border-border-soft p-2 text-[10px]">
                  <div className="text-ink">{pickZh(m.question_zh, m.question, locale) || "—"}</div>
                  <div className="mt-1 flex flex-wrap gap-2 text-[9px] text-ink-3 font-mono">
                    {(m.outcomes ?? []).map((o, oi) => (
                      <span key={oi}>
                        {o.name}: {fmtCents(o.price)}
                      </span>
                    ))}
                    {m.last_trade_price != null && <span>Last: {num(m.last_trade_price).toFixed(2)}</span>}
                    {m.best_bid != null && <span>Bid: {num(m.best_bid).toFixed(2)}</span>}
                    {m.best_ask != null && <span>Ask: {num(m.best_ask).toFixed(2)}</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </V17Card>
    </div>
  );
}

// --------------------------- 4. markets 列表 ------------------------------

interface MarketItem {
  condition_id?: string;
  question?: string;
  question_zh?: string;
  category_zh?: string;
  volume?: number;
  volume_24h?: number;
  liquidity?: number;
  active?: boolean;
  closed?: boolean;
  end_date_iso?: string;
  tokens?: { outcome?: string; price?: number }[];
  best_bid?: number;
  best_ask?: number;
}
interface MarketsResponse { markets?: MarketItem[]; count?: number }

export function PolymarketMarketsPage() {
  const tr = useUiText()

  const [limit, setLimit] = useState(20);
  const [force, setForce] = useState(0);
  const path = `/api/v1/polymarket/markets?limit=${limit}${force > 0 ? `&force=${force}` : ""}`;
  const { data, isLoading } = useApi<MarketsResponse>(path);
  const markets = data?.markets ?? [];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 市场")}
        right={<RefreshBtn onClick={() => setForce((f) => f + 1)} />}
      >
        <div className="flex items-center justify-between text-[9px] text-ink-4 mb-1">
          <span>{data?.count ?? markets.length} {tr("个市场")}</span>
          <div className="flex items-center gap-1">
            {[10, 20, 50].map((l) => (
              <button
                key={l}
                onClick={() => setLimit(l)}
                className={`rounded px-1.5 py-0.5 transition-colors ${
                  limit === l ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"
                }`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col">
          {markets.length === 0 ? (
            isLoading ? <EmptyState text={tr("加载中…")} /> : <EmptyState />
          ) : (
            markets.map((m, i) => {
              const yesTok = (m.tokens ?? []).find((t) => (t.outcome || "").toLowerCase() === "yes");
              const price = yesTok?.price ?? 0.5;
              return (
                <div
                  key={m.condition_id ?? i}
                  className="flex items-center gap-2 text-[10px] py-1.5 border-b border-border-soft"
                >
                  <div className="flex-1 text-ink truncate">{m.question_zh || m.question || "?"}</div>
                  <div className="w-12 text-right font-mono text-pos">{fmtCents(price)}</div>
                  <div className="w-10 text-right font-mono text-ink-3">{fmtNum(m.volume_24h ?? m.volume)}</div>
                </div>
              );
            })
          )}
        </div>
      </V17Card>
    </div>
  );
}

// --------------------------- 5. market detail ----------------------------

interface MarketDetail extends MarketItem {
  description?: string;
  end_date_iso?: string;
}

export function PolymarketMarketDetailPage() {
  const tr = useUiText()
  const locale = useLocale()

  const [condId, setCondId] = useState("");
  const path = condId
    ? `/api/v1/polymarket/markets/${encodeURIComponent(condId)}`
    : null;
  const { data: d, isLoading } = useApi<MarketDetail>(path);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("PM 市场详情")}>
        <div className="mb-2 flex items-center gap-2">
          <ParamInput label="condition_id" value={condId} onChange={setCondId} placeholder={tr("输入 condition id")} />
        </div>
        {!condId ? (
          <EmptyState text={tr("输入 condition_id 后查询")} />
        ) : isLoading && !d ? (
          <EmptyState text={tr("加载中…")} />
        ) : !d ? (
          <EmptyState text={tr("未找到该市场")} />
        ) : (
          <div className="space-y-2">
            <div className="text-[12px] text-ink font-medium">
              {pickZh(d.question_zh, d.question, locale) || "?"}
            </div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
              <StatBlock label={tr("24h 量")} value={fmtNum(d.volume_24h ?? d.volume)} />
              <StatBlock label={tr("流动性")} value={fmtNum(d.liquidity)} />
              <StatBlock label={tr("状态")} value={d.closed ? tr("已结束") : d.active ? tr("活跃") : "—"} cls={d.closed ? "text-ink-4" : "text-pos"} />
              <StatBlock label={tr("分类")} value={tr(d.category_zh || "—")} />
            </div>
            <div className="flex flex-wrap gap-2">
              {(d.tokens ?? []).map((t, i) => (
                <div key={i} className="rounded border border-border-soft px-2 py-1 text-[10px]">
                  <span className="text-ink-3">{t.outcome}</span>
                  <span className="ml-1 font-mono text-pos">{fmtCents(t.price)}</span>
                </div>
              ))}
            </div>
            {d.description && <div className="text-[10px] text-ink-3 leading-relaxed">{d.description}</div>}
          </div>
        )}
      </V17Card>
    </div>
  );
}

// --------------------------- 6. orderbook --------------------------------

interface BookLevel { price: string; size: string }
interface OrderBook {
  market?: string;
  asset_id?: string;
  timestamp?: string;
  bids?: BookLevel[];
  asks?: BookLevel[];
  last_trade_price?: string;
}

const TOKEN_PRESETS = [
  "71321045675252202512913817162098575808806232720448446323016202253858462551210",
];

function BookSide({ levels, side }: { levels?: BookLevel[]; side: "bids" | "asks" }) {
  const tr = useUiText()

  const rows = (levels ?? []).slice(0, 10);
  const isBid = side === "bids";
  if (rows.length === 0) return <EmptyState text={tr("无挂单")} />;
  return (
    <div className="flex flex-col">
      {rows.map((lv, i) => (
        <div key={i} className="flex items-center gap-2 text-[10px] font-mono py-0.5">
          <span className={`w-16 ${isBid ? "text-pos" : "text-neg"}`}>
            {num(lv.price).toFixed(2)}
          </span>
          <span className="w-16 text-right text-ink-3">{num(lv.size).toFixed(2)}</span>
          <div
            className={`h-2 rounded-sm ${isBid ? "bg-pos/25" : "bg-neg/25"}`}
            style={{ width: `${Math.min(100, num(lv.size) * 8)}%` }}
          />
        </div>
      ))}
    </div>
  );
}

export function PolymarketOrderbookPage() {
  const tr = useUiText()

  const [tokenId, setTokenId] = useState(TOKEN_PRESETS[0]);
  const path = tokenId
    ? `/api/v1/polymarket/orderbook/${encodeURIComponent(tokenId)}`
    : null;
  const { data: book, isLoading } = useApi<OrderBook>(path);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("PM 盘口")}>
        <div className="mb-2 flex items-center gap-2">
          <ParamInput label="token_id" value={tokenId} onChange={setTokenId} placeholder="clob token id" />
          {TOKEN_PRESETS.map((t) => (
            <button
              key={t}
              onClick={() => setTokenId(t)}
              className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-surface-2"
              title={tr("填入示例 token")}
            >
              {tr("示例")}
            </button>
          ))}
        </div>
        {!tokenId ? (
          <EmptyState text={tr("输入 token_id 后查询")} />
        ) : isLoading && !book ? (
          <EmptyState text={tr("加载中…")} />
        ) : !book ? (
          <EmptyState text={tr("无盘口数据")} />
        ) : (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-[9px] text-ink-4 font-mono">
              <span>asset: {addrShort(book.asset_id, 8)}</span>
              <span>last: {book.last_trade_price ? num(book.last_trade_price).toFixed(2) : "—"}</span>
            </div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <div>
                <div className="text-[9px] text-pos mb-1">Bids</div>
                <BookSide levels={book.bids} side="bids" />
              </div>
              <div>
                <div className="text-[9px] text-neg mb-1">Asks</div>
                <BookSide levels={book.asks} side="asks" />
              </div>
            </div>
          </div>
        )}
      </V17Card>
    </div>
  );
}

// --------------------------- 7. price ------------------------------------

export function PolymarketPricePage() {
  const tr = useUiText()

  const [tokenId, setTokenId] = useState(TOKEN_PRESETS[0]);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const path = tokenId
    ? `/api/v1/polymarket/price/${encodeURIComponent(tokenId)}?side=${side}`
    : null;
  const { data: d, isLoading } = useApi<{ token_id?: string; price?: number; midpoint?: number; side?: string }>(path);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 价格")}
        right={
          <div className="flex items-center gap-1">
            {(["BUY", "SELL"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSide(s)}
                className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${
                  side === s ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        }
      >
        <div className="mb-2 flex items-center gap-2">
          <ParamInput label="token_id" value={tokenId} onChange={setTokenId} placeholder="clob token id" />
          {TOKEN_PRESETS.map((t) => (
            <button
              key={t}
              onClick={() => setTokenId(t)}
              className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-surface-2"
            >
              {tr("示例")}
            </button>
          ))}
        </div>
        {!tokenId ? (
          <EmptyState text={tr("输入 token_id 后查询")} />
        ) : isLoading && !d ? (
          <EmptyState text={tr("加载中…")} />
        ) : !d ? (
          <EmptyState text={tr("无价格数据")} />
        ) : (
          <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            <StatBlock label={tr("{side} 价", { side: side })} value={num(d.price).toFixed(3)} cls="text-gold" />
            <StatBlock label={tr("中点价")} value={num(d.midpoint).toFixed(3)} cls="text-pos" />
            <StatBlock label={tr("隐含概率")} value={fmtCents(num(d.midpoint || d.price))} />
          </div>
        )}
      </V17Card>
    </div>
  );
}

// --------------------------- 8. crypto predictions -----------------------

interface Prediction {
  id?: string;
  title?: string;
  title_zh?: string;
  asset?: string;
  asset_zh?: string;
  timeframe?: string;
  timeframe_label?: { en?: string; zh?: string };
  volume?: number;
  volume_24h?: number;
  liquidity?: number;
  outcomes?: PolyOutcome[];
  polymarket_url?: string;
}
interface PredictionsResponse {
  predictions?: Prediction[];
  by_asset?: Record<string, Prediction[]>;
  available_assets?: string[];
  timeframe_labels?: Record<string, { en?: string; zh?: string }>;
}

function predPrice(p: Prediction): number {
  const yes = (p.outcomes ?? []).find((o) => (o.name || "").toLowerCase() === "yes");
  return yes?.price ?? 0.5;
}

export function PolymarketCryptoPredictionsPage() {
  const tr = useUiText()
  const locale = useLocale()

  const [asset, setAsset] = useState("");
  const [tf, setTf] = useState("");
  const [force, setForce] = useState(0);
  const params = new URLSearchParams();
  if (asset) params.set("asset", asset);
  if (tf) params.set("timeframe", tf);
  const qs = params.toString();
  const path = `/api/v1/polymarket/crypto-predictions?limit=30${qs ? `&${qs}` : ""}${force > 0 ? `&force=${force}` : ""}`;
  const { data, isLoading } = useApi<PredictionsResponse>(path);
  const preds = data?.predictions ?? [];
  const assets = data?.available_assets ?? [];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 加密预测")}
        right={<RefreshBtn onClick={() => setForce((f) => f + 1)} />}
      >
        <div className="mb-2 flex flex-wrap items-center gap-1">
          <span className="text-[9px] text-ink-4">ASSET</span>
          <button
            onClick={() => setAsset("")}
            className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${!asset ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
          >
            {tr("全部")}
          </button>
          {assets.map((a) => (
            <button
              key={a}
              onClick={() => setAsset(a)}
              className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${asset === a ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
            >
              {a}
            </button>
          ))}
          <span className="ml-2 text-[9px] text-ink-4">TF</span>
          {["5m", "15m", "1h", "daily"].map((t) => (
            <button
              key={t}
              onClick={() => setTf(t === tf ? "" : t)}
              className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${tf === t ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="flex flex-col">
          {preds.length === 0 ? (
            isLoading ? <EmptyState text={tr("加载中…")} /> : <EmptyState />
          ) : (
            preds.map((p, i) => {
              const price = predPrice(p);
              return (
                <div key={p.id ?? i} className="flex items-center gap-2 text-[10px] py-1.5 border-b border-border-soft">
                  <div className="flex-1 text-ink truncate">
                    {pickZh(p.title_zh, p.title, locale) || "?"}
                  </div>
                  <span className="rounded bg-surface-3 px-1 py-0.5 text-[8px] text-ink-3">{p.asset}</span>
                  <span className="rounded bg-surface-3 px-1 py-0.5 text-[8px] text-ink-3">
                    {pickZh(p.timeframe_label?.zh, p.timeframe_label?.en, locale) || p.timeframe}
                  </span>
                  <div className="w-12 text-right font-mono text-pos">{fmtCents(price)}</div>
                  {p.polymarket_url && (
                    <a
                      href={p.polymarket_url}
                      target="_blank"
                      rel="noreferrer"
                      className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-sky-400 hover:bg-sky-500/15"
                    >
                      ↗
                    </a>
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

// --------------------------- 9. address details --------------------------

interface AddressDetail {
  address?: string;
  classification?: string;
  win_rate?: number;
  roi?: number;
  trade_count?: number;
  total_volume?: number;
  trading_style?: string | null;
  llm_enhanced?: boolean;
}

const ADDRESS_PRESET = "0x4b6e5d2c0a1f3e9d8c7b6a5f4e3d2c1b0a9f8e7d";

const CLASS_LABEL: Record<string, { zh: string; cls: string }> = {
  elite: { zh: "精英", cls: "text-gold" },
  proficient: { zh: "高手", cls: "text-pos" },
  retail: { zh: "散户", cls: "text-ink-3" },
  unknown: { zh: "未知", cls: "text-ink-4" },
};

export function PolymarketAddressDetailsPage() {
  const tr = useUiText()

  const [address, setAddress] = useState(ADDRESS_PRESET);
  const [force, setForce] = useState(0);
  const path = address
    ? `/api/v1/polymarket/addresses/${encodeURIComponent(address)}/details${force > 0 ? `?force=${force}` : ""}`
    : null;
  const { data: d, isLoading } = useApi<AddressDetail>(path);

  const cls = CLASS_LABEL[d?.classification ?? ""] ?? CLASS_LABEL.unknown;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("PM 地址详情")}>
        <div className="mb-2 flex items-center gap-2">
          <ParamInput label="address" value={address} onChange={setAddress} placeholder="0x…" />
          <button
            onClick={() => setAddress(ADDRESS_PRESET)}
            className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-surface-2"
          >
            {tr("示例")}
          </button>
          <RefreshBtn onClick={() => setForce((f) => f + 1)} />
        </div>
        {!address ? (
          <EmptyState text={tr("输入地址后查询")} />
        ) : isLoading && !d ? (
          <EmptyState text={tr("加载中…(地址分析可能较慢)")} />
        ) : !d ? (
          <EmptyState text={tr("无地址数据")} />
        ) : (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-ink">{addrShort(d.address, 12)}</span>
              <span className={`rounded px-1.5 py-0.5 text-[9px] ${cls.cls} bg-surface-3`}>
                {tr(cls.zh)}
              </span>
              {d.llm_enhanced && (
                <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[8px] text-ink-3" title={tr("LLM 增强")}>
                  <Shield className="inline h-2 w-2" /> LLM
                </span>
              )}
            </div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
              <StatBlock label={tr("胜率")} value={`${num(d.win_rate) * 100}%`} cls={num(d.win_rate) >= 0.5 ? "text-pos" : "text-ink-3"} />
              <StatBlock label="ROI" value={`${(num(d.roi) * 100).toFixed(1)}%`} cls={num(d.roi) >= 0 ? "text-pos" : "text-neg"} />
              <StatBlock label={tr("成交数")} value={fmtNum(d.trade_count)} />
              <StatBlock label={tr("总成交量")} value={fmtNum(d.total_volume)} />
            </div>
            {d.trading_style && (
              <div className="rounded bg-surface-2 p-2 text-[10px] text-ink-3 leading-relaxed">
                {d.trading_style}
              </div>
            )}
          </div>
        )}
      </V17Card>
    </div>
  );
}

// --------------------------- 10. alerts ----------------------------------

interface AlertRule {
  rule_id?: string;
  kind?: string;
  target?: string;
  address?: string;
  market_keyword?: string;
  min_notional?: number;
  price_min?: number;
  price_max?: number;
  enabled?: boolean;
  created_at?: number;
}
interface AlertsResponse { rules?: AlertRule[]; total?: number }

const KIND_LABEL: Record<string, string> = {
  trade: "成交",
  position: "持仓",
  price: "价格",
};

export function PolymarketAlertsPage() {
  const tr = useUiText()

  const [force, setForce] = useState(0);
  const path = `/api/v1/polymarket/addresses/alerts${force > 0 ? `?force=${force}` : ""}`;
  const { data, isLoading, mutate } = useApi<AlertsResponse>(path);
  const rules = data?.rules ?? [];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 预警")}
        right={<RefreshBtn onClick={() => setForce((f) => f + 1)} />}
      >
        <div className="flex flex-col">
          {rules.length === 0 ? (
            isLoading ? <EmptyState text={tr("加载中…")} /> : <EmptyState text={tr("暂无预警规则")} />
          ) : (
            rules.map((r, i) => (
              <div key={r.rule_id ?? i} className="flex items-center gap-2 text-[10px] py-1.5 border-b border-border-soft">
                <span className="rounded bg-surface-3 px-1 py-0.5 text-[8px] text-ink-3">
                  {tr(KIND_LABEL[r.kind ?? ""] ?? r.kind ?? "")}
                </span>
                <div className="flex-1 text-ink truncate">
                  {r.market_keyword || r.address || r.target || "?"}
                </div>
                <div className="text-[9px] text-ink-4 font-mono">
                  {r.price_min != null && `≥${num(r.price_min).toFixed(2)}`}
                  {r.price_min != null && r.price_max != null && " "}
                  {r.price_max != null && `≤${num(r.price_max).toFixed(2)}`}
                  {r.min_notional != null && r.min_notional > 0 && ` ${fmtNum(r.min_notional)}`}
                </div>
                <span className={`rounded px-1.5 py-0.5 text-[8px] ${r.enabled ? "bg-pos/15 text-pos" : "bg-surface-3 text-ink-4"}`}>
                  {r.enabled ? tr("开") : tr("关")}
                </span>
                <button
                  onClick={async () => {
                    if (!r.rule_id) return;
                    try {
                      await fetch(`/api/v1/polymarket/addresses/alerts/${encodeURIComponent(r.rule_id)}`, {
                        method: "DELETE",
                      });
                      mutate();
                    } catch {
                      /* noop */
                    }
                  }}
                  className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-red-500/15 hover:text-red-400"
                  title={tr("删除规则")}
                >
                  <Trash2 className="h-2.5 w-2.5" />
                </button>
              </div>
            ))
          )}
        </div>
      </V17Card>
    </div>
  );
}

// --------------------------- 11. alert matches ---------------------------

interface AlertMatch {
  rule_id?: string;
  address?: string;
  market?: string;
  outcome?: string;
  price?: number;
  notional?: number;
  ts?: number;
}
interface MatchesResponse { matches?: AlertMatch[]; total?: number }

export function PolymarketAlertMatchesPage() {
  const tr = useUiText()

  const [force, setForce] = useState(0);
  const path = `/api/v1/polymarket/addresses/alerts/matches${force > 0 ? `?force=${force}` : ""}`;
  const { data, isLoading } = useApi<MatchesResponse>(path);
  const matches = data?.matches ?? [];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 预警匹配")}
        right={<RefreshBtn onClick={() => setForce((f) => f + 1)} />}
      >
        <div className="flex flex-col">
          {matches.length === 0 ? (
            isLoading ? <EmptyState text={tr("加载中…")} /> : <EmptyState text={tr("暂无匹配")} />
          ) : (
            matches.map((m, i) => (
              <div key={i} className="flex items-center gap-2 text-[10px] py-1.5 border-b border-border-soft">
                <div className="flex-1 text-ink truncate">{m.market || m.address || "?"}</div>
                {m.outcome && <span className="text-[9px] text-ink-3">{m.outcome}</span>}
                {m.price != null && <span className="font-mono text-pos">{fmtCents(m.price)}</span>}
                {m.notional != null && <span className="text-[9px] text-ink-4">{fmtNum(m.notional)}</span>}
                {m.ts != null && <span className="text-[9px] text-ink-4 font-mono">{new Date(m.ts).toLocaleTimeString()}</span>}
              </div>
            ))
          )}
        </div>
      </V17Card>
    </div>
  );
}

// --------------------------- 12. pipeline --------------------------------

export function PolymarketPipelinePage() {
  const tr = useUiText()

  const [force, setForce] = useState(0);
  const path = `/api/v1/polymarket/addresses/pipeline${force > 0 ? `?force=${force}` : ""}`;
  const { data, isLoading } = useApi<{ status?: string; stages?: string[] }>(path);
  const stages = data?.stages ?? [];

  const STAGE_LABEL: Record<string, string> = {
    fetch: "拉取",
    score: "评分",
    classify: "分类",
    enrich: "增强",
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 流水线")}
        right={<RefreshBtn onClick={() => setForce((f) => f + 1)} />}
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={tr("状态")} value={data?.status ?? "—"} cls={data?.status === "available" ? "text-pos" : "text-ink-3"} />
          <StatBlock label={tr("阶段数")} value={stages.length} />
        </div>
        <div className="mt-2 flex items-center gap-1">
          {stages.length === 0 && isLoading ? (
            <EmptyState text={tr("加载中…")} />
          ) : (
            stages.map((s, i) => (
              <div key={s} className="flex items-center gap-1">
                <div className="rounded bg-surface-3 px-2 py-1 text-[9px] text-ink-2 flex items-center gap-1">
                  <span className={`h-1.5 w-1.5 rounded-full ${data?.status === "available" ? "bg-pos" : "bg-ink-4"}`} />
                  {tr(STAGE_LABEL[s] ?? s)}
                </div>
                {i < stages.length - 1 && <span className="text-[9px] text-ink-4">→</span>}
              </div>
            ))
          )}
        </div>
      </V17Card>
    </div>
  );
}

// --------------------------- 13. screen --------------------------------

interface ScreenAddress extends AddressDetail {}

export function PolymarketScreenPage() {
  const tr = useUiText()

  const [classification, setClassification] = useState("");
  const [limit, setLimit] = useState(30);
  const [force, setForce] = useState(0);
  const params = new URLSearchParams();
  if (classification) params.set("classification", classification);
  params.set("limit", String(limit));
  const qs = params.toString();
  const path = `/api/v1/polymarket/addresses/screen?${qs}${force > 0 ? `&force=${force}` : ""}`;
  const { data, isLoading } = useApi<{ addresses?: ScreenAddress[]; total?: number }>(path);
  const addresses = data?.addresses ?? [];

  const CLASS_OPTIONS = [
    { value: "", label: "全部" },
    { value: "elite", label: "精英" },
    { value: "proficient", label: "高手" },
    { value: "retail", label: "散户" },
    { value: "unknown", label: "未知" },
  ];

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 筛选")}
        right={<RefreshBtn onClick={() => setForce((f) => f + 1)} />}
      >
        <div className="mb-2 flex flex-wrap items-center gap-1">
          <span className="text-[9px] text-ink-4">CLASS</span>
          {CLASS_OPTIONS.map((c) => (
            <button
              key={c.value}
              onClick={() => setClassification(c.value)}
              className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${
                classification === c.value ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"
              }`}
            >
              {tr(c.label)}
            </button>
          ))}
        </div>
        <div className="flex flex-col">
          {addresses.length === 0 ? (
            isLoading ? <EmptyState text={tr("加载中…")} /> : <EmptyState text={tr("暂无地址(需先分析)")} />
          ) : (
            addresses.map((a, i) => {
              const cls = CLASS_LABEL[a.classification ?? ""] ?? CLASS_LABEL.unknown;
              return (
                <div key={a.address ?? i} className="flex items-center gap-2 text-[10px] py-1.5 border-b border-border-soft">
                  <div className="flex-1 font-mono text-ink truncate">{addrShort(a.address, 12)}</div>
                  <span className={`w-10 rounded px-1 py-0.5 text-center text-[8px] ${cls.cls} bg-surface-3`}>
                    {tr(cls.zh)}
                  </span>
                  <div className="w-14 text-right font-mono text-pos">{(num(a.win_rate) * 100).toFixed(0)}%</div>
                  <div className="w-14 text-right font-mono"><Pct value={num(a.roi) * 100} /></div>
                  <div className="w-10 text-right text-ink-3">{fmtNum(a.trade_count)}</div>
                </div>
              );
            })
          )}
        </div>
      </V17Card>
    </div>
  );
}

// --------------------------- 14. watchlist -------------------------------

interface WatchEntry {
  address?: string;
  label?: string;
  note?: string;
  tags?: string[];
  source?: string;
  added_at?: number;
}
interface WatchlistResponse { entries?: WatchEntry[]; total?: number }

export function PolymarketWatchlistPage() {
  const tr = useUiText()

  const [address, setAddress] = useState("");
  const [force, setForce] = useState(0);
  const path = `/api/v1/polymarket/addresses/watchlist${force > 0 ? `?force=${force}` : ""}`;
  const { data, isLoading, mutate } = useApi<WatchlistResponse>(path);
  const entries = data?.entries ?? [];

  async function addAddress() {
    const trimmed = address.trim();
    if (!trimmed) return;
    try {
      await fetch("/api/v1/polymarket/addresses/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ addresses: [trimmed], source: "manual" }),
      });
      setAddress("");
      mutate();
    } catch {
      /* noop */
    }
  }

  async function removeAddress(addr: string) {
    try {
      await fetch(`/api/v1/polymarket/addresses/watchlist/${encodeURIComponent(addr)}`, {
        method: "DELETE",
      });
      mutate();
    } catch {
      /* noop */
    }
  }

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("PM 关注")}
        right={<RefreshBtn onClick={() => setForce((f) => f + 1)} />}
      >
        <div className="mb-2 flex items-center gap-1.5">
          <ParamInput label="address" value={address} onChange={setAddress} placeholder={tr("0x… 添加到关注")} />
          <button
            onClick={addAddress}
            className="flex items-center gap-0.5 rounded bg-surface-3 px-1.5 py-1 text-[9px] text-ink-2 hover:bg-gold/15 hover:text-gold"
          >
            <Plus className="h-2.5 w-2.5" /> {tr("添加")}
          </button>
        </div>
        <div className="flex flex-col">
          {entries.length === 0 ? (
            isLoading ? <EmptyState text={tr("加载中…")} /> : <EmptyState text={tr("关注列表为空")} />
          ) : (
            entries.map((e, i) => (
              <div key={e.address ?? i} className="flex items-center gap-2 text-[10px] py-1.5 border-b border-border-soft">
                <div className="flex-1">
                  <div className="font-mono text-ink truncate">{addrShort(e.address, 14)}</div>
                  {(e.label || e.note) && (
                    <div className="text-[9px] text-ink-4 truncate">
                      {[e.label, e.note, ...(e.tags ?? [])].filter(Boolean).join(" · ")}
                    </div>
                  )}
                </div>
                <span className="rounded bg-surface-3 px-1 py-0.5 text-[8px] text-ink-4">{e.source || "manual"}</span>
                {e.added_at != null && (
                  <span className="text-[9px] text-ink-4 font-mono">{new Date(e.added_at).toLocaleDateString()}</span>
                )}
                <button
                  onClick={() => e.address && removeAddress(e.address)}
                  className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-red-500/15 hover:text-red-400"
                  title={tr("移除")}
                >
                  <Trash2 className="h-2.5 w-2.5" />
                </button>
              </div>
            ))
          )}
        </div>
      </V17Card>
    </div>
  );
}
