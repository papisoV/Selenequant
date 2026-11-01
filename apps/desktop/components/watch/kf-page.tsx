"use client";

import { useContextStore } from "@/lib/store/context";
import { useMarketStore, type WsKline } from "@/lib/store/market";
import { preload } from "swr";
import { useApi, fetcher } from "@/lib/hooks/use-api";
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { DrawingTools } from "@/components/watch/drawing-tools";
import { ObjectTreeDock } from "@/components/watch/object-tree-dock";
import { useDrawingsStore } from "@/lib/store/drawings";
import { useIctDisplayStore } from "@/lib/store/ict-display";
import Link from "next/link";
import { KfChart } from "@/components/watch/kf-chart";
import { useKTTracks, AI_COLOR, AI_LABEL } from "@/lib/hooks/use-kt-tracks";
import { useMarketsWs, type MarketMessage } from "@/lib/ws/use-markets-ws";
import { WorkspaceLink } from "@/components/shared/workspace-link";
import { KlineChart } from "@/components/chart/kline-chart";
import { useMultiMarketBars } from "@/lib/hooks/use-multi-market-bars";
import { useSymbolFavorites } from "@/lib/hooks/use-symbol-favorites";
import type { SymbolMarket } from "@/lib/market/route-symbol";
import { supportedIntervals, TF_LABELS, type ChartCapabilities } from "@/lib/market/chart-capabilities";
import { sourceLabel } from "@/lib/market/source-registry";
import { useUiText } from "@/components/i18n/use-ui-text"
import { toSymbolPair } from "@/lib/symbol-utils";
import {
  createPriceAlert,
  deletePriceAlert,
  listPriceAlerts,
  httpErrorMessage,
  type PriceAlertDto,
} from "@/lib/api/decision-workflow";
import { alertLinesFor, type AlertLine } from "@/components/watch/kf-chart";
import { toastError, toastSuccess } from "@/lib/toast";

/** 无线可画时共用的空数组：保持引用稳定，省掉 KfChart effect 的无效重建。 */
const EMPTY_ALERT_LINES: AlertLine[] = [];

// ── WebSocket 实时数据接口（WS push → 本地 state）──────────────────────────
interface WsTicker {
  last_price?: number;
  change_24h_pct?: number;
  volume_24h?: number;
  quote_volume_24h?: number;
  bid?: number;
  ask?: number;
  high_24h?: number;
  low_24h?: number;
  /** 这份 ticker 属于哪个标的(裸符号,如 "BTC")。用于换标的那几帧拒绝把
   *  旧标的的价喂给新图 —— 父组件的清理 effect 跑在子组件 effect 之后,
   *  仅靠"onSymbolsChange 清空"救不了同一提交里的先后顺序。 */
  sym?: string;
}
interface WsDepthLevel { price?: number; quantity?: number; }
interface WsDepth {
  bids?: WsDepthLevel[];
  asks?: WsDepthLevel[];
}
interface WsLiqStats {
  long_liquidated_usd?: number;
  short_liquidated_usd?: number;
  total_liquidated_usd?: number;
}
interface WsFundingRate {
  funding_rate?: number;
  next_funding_time?: number;
}
interface WsLargeTrade {
  side?: string;
  price?: number;
  notional_usd?: number;
  timestamp?: number;
  symbol?: string;
  quantity?: number;
}

/** /api/v1/intelligence/social/sentiment — {overall, by_subreddit{name:ratio}, timestamp} */
interface SocialSentiment {
  overall?: number;
  by_subreddit?: Record<string, number>;
  timestamp?: number;
}

// REST fallback 接口（HTTP 初始值，WS 到达后覆盖）
interface LiqCluster {
  price: number;
  density: number;
  intensity: number;
  dominant_side: string;
  long_liquidated_usd?: number;
  short_liquidated_usd?: number;
  total_liquidated_usd?: number;
}
interface LiqData {
  long?: number;
  short?: number;
  total?: number;
  mark_price?: number;
  clusters?: LiqCluster[];
}
interface TacData { rate?: number; next_rate?: number; }

// TickerEvent (后端 /api/v1/markets/{symbol}/ticker → APIResponse.data)
interface TickerData {
  last_price?: number;
  bid?: number;
  ask?: number;
  volume_24h?: number;
  quote_volume_24h?: number | null;
  high_24h?: number | null;
  low_24h?: number | null;
  change_24h_pct?: number;
}

// OpenInterestSnapshot (后端 /api/v1/derivatives/open-interest → {snapshots: {symbol: {...}}})
interface OISnapshot {
  open_interest?: number;
  open_interest_usd?: number;
}
interface OIData { snapshots?: Record<string, OISnapshot>; }

// GEXResult (后端 /api/v1/gex/current?currency=BTC → APIResponse.data)
interface GexLevel { price?: number; net_gex?: number; label?: string; }
interface GexData {
  symbol?: string;
  underlying_price?: number;
  net_gex?: number;
  total_call_gex?: number;
  total_put_gex?: number;
  dealer_positioning?: string;
  key_levels?: GexLevel[];
  put_call_ratio?: number;
  max_pain?: number;
}

interface ObRow { px: number; sz: number; sum: number; }

// 后端 /api/v1/markets/{symbol}/orderbook 返回 {bids: [{price, quantity, ...}], asks: [...]}
interface BackendObLevel { price?: number; quantity?: number; size?: number; amount?: number; }
interface BackendOrderbook { bids?: BackendObLevel[]; asks?: BackendObLevel[]; }

function adaptOb(levels: BackendObLevel[] | undefined, side: "ask" | "bid"): ObRow[] {
  if (!levels || !Array.isArray(levels) || levels.length === 0) return [];
  let cum = 0;
  const rows = levels.slice(0, 5).map((l) => {
    const px = Number(l.price ?? 0);
    const sz = Number(l.quantity ?? l.size ?? l.amount ?? 0);
    cum += sz;
    return { px, sz, sum: cum };
  });
  // asks 显示时反向（高价在上），bids 正序（高价在上）
  return side === "ask" ? rows.reverse() : rows;
}

// 后端 /api/v1/intelligence/whale-flow 返回 {items: [{ts, direction, value_usd, source, headline, ...}]}
interface BackendWhaleItem {
  ts?: number;
  direction?: string;
  value_usd?: number | null;
  source?: string;
  headline?: string | null;
  venue?: string;
}
interface BackendWhaleFlow { items?: BackendWhaleItem[]; }

function adaptWhaleFlow(items: BackendWhaleItem[] | undefined) {
  if (!items || !Array.isArray(items) || items.length === 0) return [];
  return items.slice(0, 10).map((it) => {
    const ts = Number(it.ts ?? 0);
    const time = ts > 0
      ? new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
      : "--:--";
    const dir = (it.direction || "").toLowerCase();
    const side = dir === "inflow" || dir === "bullish" || dir === "buy" ? "buy" : "sell";
    const usd = Number(it.value_usd ?? 0);
    const size = usd >= 1e6 ? `${(usd / 1e6).toFixed(1)}M` : `${Math.round(usd / 1e3)}K`;
    const venue = it.venue || it.source || "—";
    return { time, side, size, large: usd >= 1_000_000, venue, qtyUsd: usd };
  });
}

// USD 紧凑格式:1234 -> $1.2K, 1.2M, 1.2B
function fmtUsdCompact(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

const ALL_CARD_IDS = [
  "info", "trade", "orderbook", "whaleflow", "liquidation", "gex", "funding",
] as const;
// 加密专属卡片：数据源（订单簿/大单流/爆仓/GEX/资金费）只在加密永续存在。
// 选 A股/美股/大宗时这些卡片会恒空（"暂无数据"/"—"），是 1013"切换交易对残留"的一部分，
// 故非加密市场整卡隐藏（cardOrder 保留，切回加密自动恢复）。
const CRYPTO_ONLY_CARDS = new Set<string>([
  "orderbook", "whaleflow", "liquidation", "gex", "funding",
]);
const CARD_TITLES: Record<string, string> = {
  info: "品种信息",
  trade: "下单",
  orderbook: "订单簿",
  whaleflow: "大单流",
  liquidation: "爆仓热力",
  gex: "GEX",
  funding: "费率",
};

// 多市场超级图表（super-chart 能力并入 kf）——加密走 WS 实时，其余走 /chart/bars（入口=⌘K）
const MARKET_LABEL: Record<string, string> = {
  crypto: "加密",
  us: "美股",
  commodities: "大宗",
  ashare: "A股",
  taiwan: "台股",
};

function Card({
  id,
  title,
  count,
  kind,
  folded: initFolded = false,
  onRemove,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  dragState,
  children,
}: {
  id: string;
  title: string;
  count?: string;
  kind?: "gold" | "pos" | "neg";
  folded?: boolean;
  onRemove?: () => void;
  onDragStart?: (e: React.DragEvent) => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
  onDragEnd?: (e: React.DragEvent) => void;
  dragState?: { dragging: boolean; dragOver: boolean };
  children: React.ReactNode;
}) {
  const tr = useUiText()

  const [folded, setFolded] = useState(initFolded);
  return (
    <div
      className={`rp-card ${folded ? "folded" : ""} ${dragState?.dragging ? "dragging" : ""} ${dragState?.dragOver ? "drag-over" : ""}`}
      data-card={id}
      draggable={!!onDragStart}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
    >
      <div
        className={`rp-card-head ${kind ?? ""}`}
        onClick={() => setFolded((v) => !v)}
      >
        <div className="rp-card-title">
          <span className="drag-handle">⠿</span>
          <span className="arrow">▼</span>
          {title}
          {count && <span className="cnt">{count}</span>}
        </div>
        <div className="rp-card-actions">
          <div className="rpa-btn" title={tr("折叠")}>
            <svg width="10" height="10" viewBox="0 0 10 10">
              <path d="M2 5 L8 5" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          </div>
        </div>
      </div>
      <div className="rp-card-body">{children}</div>
    </div>
  );
}

// hover 预载:把页面 7 个 REST 端点预热进 SWR global cache,切页后立即渲染
export function preloadData() {
  const symBase = useMarketStore.getState().pageFilter.symbol || "BTC";
  // fetcher 失败会 throw(SWR 重试接管),预载是 fire-and-forget 须吞 rejection
  preload(`/api/v1/liqmap/current?symbol=${encodeURIComponent(`${symBase}/USDT`)}`, fetcher).catch(() => {});
  preload("/api/v1/derivatives/funding-rates", fetcher).catch(() => {});
  preload(`/api/v1/markets/${encodeURIComponent(symBase)}/USDT/orderbook?depth=5`, fetcher).catch(() => {});
  preload(`/api/v1/intelligence/whale-flow?symbol=${encodeURIComponent(symBase)}&limit=10`, fetcher).catch(() => {});
  preload(`/api/v1/markets/${encodeURIComponent(symBase)}/USDT/ticker`, fetcher).catch(() => {});
  preload("/api/v1/derivatives/open-interest", fetcher).catch(() => {});
  const gexCurrency = symBase === "BTC" || symBase === "ETH" ? symBase : "BTC";
  preload(`/api/v1/gex/current?currency=${gexCurrency}`, fetcher).catch(() => {});
}

// 指标 picker 目录（0923）：两组 4+5。key 对应 tools 字段与 KfChart props。
// params 仅展示不可调（QD 精细活缓建）。
const KF_INDICATOR_OVERLAYS = [
  { key: "ma", label: "MA", params: "20" },
  { key: "ema20", label: "EMA", params: "20" },
  { key: "ema50", label: "EMA", params: "50" },
  { key: "boll", label: "BOLL", params: "20, 2" },
] as const;
const KF_INDICATOR_PANES = [
  { key: "vol", label: "VOL", params: "" },
  { key: "macd", label: "MACD", params: "12, 26, 9" },
  { key: "rsi", label: "RSI", params: "14" },
  { key: "kdj", label: "KDJ", params: "9, 3, 3" },
  { key: "atr", label: "ATR", params: "14" },
] as const;
// 结构层（yanjiu 合并时迁入，2026-10-30 由单一总开关拆成四层）：一次叠加请求
// 返回四份数据，但「摆动点 / 破位线 / 时段带 / 溢价折价」是四种独立的读图目的，
// 绑在一个开关上时一开全开、图面糊死。拆开后各层独立开关，请求仍是同一次
// （任一层开即拉，四层共享同一 bar 快照与 SWR 缓存）。
// `ictLayer` 指向参数抽屉（components/watch/ict-layer-params.tsx）；
// 行内 params 文案由当前设置动态生成，见下面 treeIndicators。
const KF_INDICATOR_STRUCTURE = [
  { key: "ictSwings", label: "ICT 摆动点", ictLayer: "swings" },
  { key: "ictEvents", label: "ICT 破位线", ictLayer: "events" },
  { key: "ictKillzone", label: "ICT 时段带", ictLayer: "killzone" },
  { key: "ictDealRange", label: "ICT 溢价折价", ictLayer: "dealingRange" },
] as const;

export function KfPage() {
  const tr = useUiText()

  const { pageFilter, symBase, layoutPreset, setSymBase, setLayoutPreset } = useContextStore();
  const { tf } = pageFilter;
  // 指标配置（页面级：应用到全部图格，双入口——快捷三开关 + 对象树指标区同源）
  const [tools, setTools] = useState({ ma: true, vol: false, macd: false, ema20: false, ema50: false, boll: false, rsi: false, kdj: false, atr: false, ictSwings: false, ictEvents: false, ictKillzone: false, ictDealRange: false });
  // ICT 四层的显示参数（上限/档位/时段/分位阈值）。落 localStorage，
  // 与开关本身分开：开关是页面级临时态，参数是跨会话的使用习惯。
  const ictDisplay = useIctDisplayStore((s) => s.settings);
  const hydrateIctDisplay = useIctDisplayStore((s) => s.hydrate);
  useEffect(() => {
    hydrateIctDisplay();
  }, [hydrateIctDisplay]);
  // 行内参数摘要：由当前设置生成，改参数后树上立刻能看到「现在是什么口径」，
  // 不必展开才想起来。四层的文案各自对应后端真实可调的旋钮。
  const ictParamsSummary = useMemo(() => {
    const s = ictDisplay.swings;
    const tierNames = [s.tiers.st && "ST", s.tiers.it && "IT", s.tiers.lt && "LT"].filter(Boolean);
    const e = ictDisplay.events;
    const kz = ictDisplay.killzone;
    const sessionNames = [
      kz.sessions.london && "伦敦",
      kz.sessions.new_york && "纽约",
      kz.sessions.asia && "亚洲",
    ].filter(Boolean);
    const dr = ictDisplay.dealingRange;
    return {
      ictSwings: `${tierNames.join("/") || "—"} · ≤${s.maxCount}`,
      ictEvents: `${e.breakOn === "close" ? "收盘" : "影线"}破 · ≤${e.maxCount}`,
      ictKillzone: `${sessionNames.join("/") || "—"} · UTC${kz.tzOffsetHours >= 0 ? "+" : ""}${kz.tzOffsetHours}`,
      ictDealRange: `${(dr.premiumThreshold * 100).toFixed(0)}/50/${(dr.discountThreshold * 100).toFixed(0)} · r${dr.period}`,
    } as Record<string, string>;
  }, [ictDisplay]);
  // 对象树的指标区：把 KF_INDICATOR_* 目录与 tools 合成树行。
  // 指标是页面级布尔、绘图是按标的的实例，两者存储形状不同但都"有名字、能隐藏"，
  // 所以合流发生在**树里**而不是数据层 —— KfChart 的 showXxx props 一行不改
  // （ICT 四层亦然：showIctSwings/Events/Killzone/DealingRange 各自独立）。
  const treeIndicators = useMemo(
    () => [
      ...KF_INDICATOR_OVERLAYS.map((i) => ({ ...i, on: tools[i.key], group: "overlay" as const })),
      ...KF_INDICATOR_PANES.map((i) => ({ ...i, on: tools[i.key], group: "pane" as const })),
      ...KF_INDICATOR_STRUCTURE.map((i) => ({
        ...i,
        on: tools[i.key],
        group: "structure" as const,
        params: ictParamsSummary[i.key],
      })),
    ],
    [tools, ictParamsSummary],
  );
  const toggleIndicator = useCallback(
    (key: string) => setTools((t) => ({ ...t, [key]: !t[key as keyof typeof t] })),
    [],
  );
  // 对象树是否展开决定 `.chart-area` 的列数(收起时不占宽度)。
  const dockOpen = useDrawingsStore((s) => s.dockOpen);
  // ── 多市场超级图表状态（super-chart 能力并入 kf）────────────────────────
  // superMarket 强制市场选择：'crypto' = 现有 KfChart 实时分支（默认，现状不变）；
  // 非 crypto = 走 /chart/bars 多市场 REST 网关（KlineChart 渲染）。
  // 入口 = ⌘K 面板（symbolPick 交接，带市场），页面内不再提供输入框。
  // ⌘K 选中符号 → 消费 symbolPick（带市场，跨页/同页都覆盖）
  const symbolPick = useMarketStore((s) => s.symbolPick);
  const setSymbolPick = useMarketStore((s) => s.setSymbolPick);
  // 首帧就用 pending symbolPick 初始化，避免"从 ⌘K 选 A股落到本页"时先用默认
  // crypto 渲染一帧（会白打一轮 BTC 加密端点请求）。lazy initializer 只在挂载跑一次。
  const [superMarket, setSuperMarket] = useState<SymbolMarket>(
    () => useMarketStore.getState().symbolPick?.market ?? "crypto"
  );
  const [superSymbol, setSuperSymbol] = useState(
    () => useMarketStore.getState().symbolPick?.symbol ?? ""
  );
  // REST 分支独立周期：不与全局 context tf 混用，避免 ashare(仅 1d) 请求 1h。
  // 同样按 pending pick 懒初始化：ashare 只支持 1d，用全局 tf(1h) 会白打一个 422。
  const [superTf, setSuperTf] = useState(() => {
    const p = useMarketStore.getState().symbolPick;
    if (!p) return tf;
    const supported = supportedIntervals(p.market, null);
    return supported.includes(tf) ? tf : (supported[supported.length - 1] ?? "1d");
  });
  const caps = useApi<ChartCapabilities>("/api/v1/chart/capabilities");
  // 工具栏「情绪」：后端 Reddit 抓取失败时会回落成硬编码中性值 0.5，
  // 直接显示就把一个假数字呈现成了真实读数。因此只在真有 per-subreddit
  // 样本(by_subreddit 非空)时才显示数值,否则保持 "—"。
  const { data: sentiment } = useApi<SocialSentiment>("/api/v1/intelligence/social/sentiment", {
    refreshInterval: 300000,
  });
  const sentimentSamples = Object.keys(sentiment?.by_subreddit ?? {}).length;
  const sentimentValue =
    sentimentSamples > 0 && typeof sentiment?.overall === "number"
      ? sentiment.overall
      : null;
  const intervals = supportedIntervals(superMarket, caps.data);
  useEffect(() => {
    if (!symbolPick) return;
    setSuperSymbol(symbolPick.symbol);
    setSuperMarket(symbolPick.market);
    // crypto pick 回实时分支时同步驱动全局品种（kf 验收版行为：⌘K 全包）
    if (symbolPick.market === "crypto") setSymBase(symbolPick.symbol);
    const supported = supportedIntervals(symbolPick.market, caps.data);
    if (!supported.includes(tf)) setSuperTf(supported[supported.length - 1] ?? "1d");
    setSymbolPick(null);
  }, [symbolPick, setSymbolPick, tf, caps.data, setSymBase]);
  const multiMarketBars = useMultiMarketBars(superMarket, superSymbol, superTf);
  const isSuperRest = superMarket !== "crypto" && !!superSymbol;
  // 当前实际展示的市场：非加密走超级图表(superMarket)，否则是加密实时分支。
  // 右侧面板的加密专属数据源（订单簿/资金费/大单/爆仓/GEX，均走 /markets/<sym>/USDT/*
  // 与 /intelligence/whale-flow）必须按此门控 —— 否则选 A股/美股时仍以 symBase 去
  // 请求加密端点 → 404、卡片恒空"暂无数据"（1013 切换交易对"残留"根因）。
  const activeMarket: SymbolMarket = isSuperRest ? superMarket : "crypto";
  const isCrypto = activeMarket === "crypto";
  // 当前图表的展示符号：非加密用 superSymbol，加密用 symBase
  const activeSymbol = isSuperRest ? superSymbol : symBase;
  // 自选开关：对"正在看的交易对"添加/取消自选（kf 原先只在 ⌘K 里能加，无法取消）
  const { toggleFavorite, isFavorite } = useSymbolFavorites();
  const isFavActive = !!activeSymbol && isFavorite(activeSymbol);

  // ── 批3 手画价格预警 ───────────────────────────────────────────────────
  // 后端轮询 loop 的价格源写死 binance/futures（main.py:2861），所以只有
  // 加密分支能真正触发。非加密不给开（下面的按钮显式 disabled + 说明），
  // 而不是让它静默空转 —— 画得出来却永远不会响是最坏的形态。
  const [drawMode, setDrawMode] = useState(false);
  const [alertRules, setAlertRules] = useState<PriceAlertDto[]>([]);
  const canUseAlerts = isCrypto;
  const refreshAlerts = useCallback(async () => {
    try {
      setAlertRules(await listPriceAlerts());
    } catch {
      // 列表拉不到就当没有已挂规则（不阻塞看图）；POST 失败会单独报错。
      setAlertRules([]);
    }
  }, []);
  // 首次挂载拉一次：刷新页面后把手画线重建回图上。
  useEffect(() => {
    void refreshAlerts();
  }, [refreshAlerts]);
  // 退出加密分支时收起画线模式，否则切到 A股后按钮是禁用的但模式还开着
  useEffect(() => {
    if (!canUseAlerts) setDrawMode(false);
  }, [canUseAlerts]);
  const handleDrawLevel = useCallback(
    async (price: number, cellSymbol: string) => {
      const sym = toSymbolPair(cellSymbol);
      try {
        await createPriceAlert({
          symbol: sym,
          interval: tf,
          levels: { price },
        });
        await refreshAlerts();
        toastSuccess(`已挂预警：${sym} 触及 ${price} 提醒`);
      } catch (err) {
        toastError(`挂预警失败：${httpErrorMessage(err)}`);
      }
    },
    [tf, refreshAlerts],
  );
  const handleDeleteAlert = useCallback(
    async (id: number) => {
      const line = alertRules.find((a) => a.id === id);
      const where = line ? `${line.symbol} ${line.levels?.price ?? ""}` : `#${id}`;
      if (!window.confirm(`删除预警 ${where}？`)) return;
      try {
        await deletePriceAlert(id);
        await refreshAlerts();
        toastSuccess(`已删除预警 ${where}`);
      } catch (err) {
        toastError(`删除预警失败：${httpErrorMessage(err)}`);
      }
    },
    [alertRules, refreshAlerts],
  );
  /** 每个图格按自己的品种取线：多图模式下 9 个格子是 9 个品种，
   *  不能把 BTC 的线画到 ETH 的格子上。
   *
   *  线按 symbol 预先算好放进 Map，取用时返回同一个数组引用 —— 否则每次
   *  render 都是新数组，KfChart 里"删旧线+建新线"的 effect 会跟着 WS 每秒
   *  tick 抖一次（线闪）。用 useMemo 而非 ref+effect：effect 在 render 之后
   *  才清缓存，会把刚填好的条目清掉，反而每轮都重建。 */
  const alertLinesBySym = useMemo(() => {
    const wanted = new Set<string>();
    for (const a of alertRules) {
      const s = (a.symbol ?? "").trim().toUpperCase();
      if (s) wanted.add(s);
    }
    const map = new Map<string, AlertLine[]>();
    for (const s of wanted) map.set(s, alertLinesFor(alertRules, s, tf));
    return map;
  }, [alertRules, tf]);
  const alertLinesForCell = useCallback(
    (cellSymbol: string) =>
      alertLinesBySym.get(toSymbolPair(cellSymbol).trim().toUpperCase()) ?? EMPTY_ALERT_LINES,
    [alertLinesBySym],
  );

  // P0-1 三轨动态数据（设计稿 L8497-8540 genKTData + renderKlineTimeline）
  const tracks = useKTTracks();
  const N = 90, CW = 100 / N;
  const xScale = (i: number) => i * CW + CW / 2;
  // P0-2 agent 轨道 hover/click 交互
  const [hoverAi, setHoverAi] = useState<number | null>(null);
  // P1-4 费率倒计时（下次 8h 结算）
  const [fundCountdown, setFundCountdown] = useState("");
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const h = now.getUTCHours();
      const nextH = Math.floor(h / 8) * 8 + 8;
      const next = new Date(now);
      next.setUTCHours(nextH, 0, 0, 0);
      if (next.getUTCHours() <= h) next.setUTCDate(next.getUTCDate() + 1);
      const diff = next.getTime() - now.getTime();
      const hh = Math.floor(diff / 3_600_000);
      const mm = Math.floor((diff % 3_600_000) / 60_000);
      const ss = Math.floor((diff % 60_000) / 1000);
      setFundCountdown(`${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  // P1-4 right-panel 拖拽换序 + 移除（设计稿 L6157-6222）
  const DEFAULT_CARD_ORDER = ["info", "trade", "orderbook", "whaleflow", "liquidation", "gex", "funding"];
  const [cardOrder, setCardOrder] = useState<string[]>([...DEFAULT_CARD_ORDER]);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [manageMenuOpen, setManageMenuOpen] = useState(false);

  // ── WebSocket 实时数据 state + refs（WS 推送覆盖 REST 初始值）────────────
  const [wsTicker, setWsTicker] = useState<WsTicker | null>(null);
  const [wsDepth, setWsDepth] = useState<WsDepth | null>(null);
  const [wsLiqStats, setWsLiqStats] = useState<WsLiqStats | null>(null);
  const [wsFundingRate, setWsFundingRate] = useState<WsFundingRate | null>(null);
  const [wsLargeTrades, setWsLargeTrades] = useState<WsLargeTrade[]>([]);
  const wsTickerRef = useRef<WsTicker | null>(null);
  const wsDepthRef = useRef<WsDepth | null>(null);
  const wsLiqStatsRef = useRef<WsLiqStats | null>(null);
  const wsFundingRateRef = useRef<WsFundingRate | null>(null);
  const wsLargeTradesRef = useRef<WsLargeTrade[]>([]);
  // WS kline 消息计数器：单调递增 seq，供 market store 防乱序覆盖
  const wsKlineSeqRef = useRef(0);
  const setLastWsKline = useMarketStore((s) => s.setLastWsKline);

  // 品种/市场切换 → 清空 WS 派生值。crypto→非crypto 时 useMarketsWs 的 symbols 不变
  // （symBase 仍是 BTC），onSymbolsChange 不触发，必须按 activeSymbol 兜底，
  // 否则上一品种的 ticker/订单簿/大单会残留在右侧面板（1013 切换"残留"根因）。
  useEffect(() => {
    wsTickerRef.current = null;
    wsDepthRef.current = null;
    wsLiqStatsRef.current = null;
    wsFundingRateRef.current = null;
    wsLargeTradesRef.current = [];
    setWsTicker(null);
    setWsDepth(null);
    setWsLiqStats(null);
    setWsFundingRate(null);
    setWsLargeTrades([]);
  }, [activeSymbol, activeMarket]);

  // RAF 批处理：同帧内多条消息只触发一次 setState
  const onMessageBatch = useCallback((msgs: MarketMessage[]) => {
    const dirty = { ticker: false, depth: false, liq: false, funding: false, trades: false };
    for (const msg of msgs) {
      const d = msg.data as Record<string, unknown>;
      switch (msg.type) {
        case "ticker": {
          const sym = String(d.symbol ?? "").split("/")[0].toUpperCase();
          if (sym !== symBase) break;
          wsTickerRef.current = {
            last_price: d.last_price as number | undefined,
            change_24h_pct: d.change_24h_pct as number | undefined,
            volume_24h: d.volume_24h as number | undefined,
            quote_volume_24h: d.quote_volume_24h as number | undefined,
            bid: d.bid as number | undefined,
            ask: d.ask as number | undefined,
            high_24h: d.high_24h as number | undefined,
            low_24h: d.low_24h as number | undefined,
            sym,
          };
          dirty.ticker = true;
          break;
        }
        case "depth": {
          const sym = String(d.symbol ?? "").split("/")[0].toUpperCase();
          if (sym !== symBase) break;
          wsDepthRef.current = {
            bids: (d.bids as WsDepthLevel[] | undefined)?.slice(0, 5),
            asks: (d.asks as WsDepthLevel[] | undefined)?.slice(0, 5),
          };
          dirty.depth = true;
          break;
        }
        case "liquidation_stats": {
          wsLiqStatsRef.current = {
            long_liquidated_usd: d.long_liquidated_usd as number | undefined,
            short_liquidated_usd: d.short_liquidated_usd as number | undefined,
            total_liquidated_usd: d.total_liquidated_usd as number | undefined,
          };
          dirty.liq = true;
          break;
        }
        case "funding_rate": {
          const sym = String(d.symbol ?? "").split("/")[0].toUpperCase();
          if (sym !== symBase) break;
          wsFundingRateRef.current = {
            funding_rate: d.funding_rate as number | undefined,
            next_funding_time: d.next_funding_time as number | undefined,
          };
          dirty.funding = true;
          break;
        }
        case "large_trade": {
          const sym = String(d.symbol ?? "").split("/")[0].toUpperCase();
          if (sym !== symBase) break;
          const trade: WsLargeTrade = {
            side: d.side as string | undefined,
            price: d.price as number | undefined,
            notional_usd: d.notional_usd as number | undefined,
            timestamp: d.timestamp as number | undefined,
            symbol: d.symbol as string | undefined,
            quantity: d.quantity as number | undefined,
          };
          wsLargeTradesRef.current = [trade, ...wsLargeTradesRef.current].slice(0, 20);
          dirty.trades = true;
          break;
        }
        // Binance futures @aggTrade 经代理不推,large_trade 永远 0 条。
        // 接 L2 Manager 推的 large_order(大挂单)作为替代:
        // - 只接 event_type==="appeared"(新出现),过滤 increased/decreased/disappeared 避免刷屏
        // - side: bid→buy(买盘挂单), ask→sell(卖盘挂单)
        // - 阈值 $100K,避免小挂单刷屏(后端推 ~866/s,不过滤会卡)
        case "large_order": {
          const sym = String(d.symbol ?? "").split("/")[0].toUpperCase();
          if (sym !== symBase) break;
          if (d.event_type !== "appeared") break;
          const usd = Number(d.notional_usd ?? 0);
          if (usd < 100_000) break;
          const sideRaw = String(d.side ?? "").toLowerCase();
          const trade: WsLargeTrade = {
            side: sideRaw === "bid" ? "buy" : sideRaw === "ask" ? "sell" : sideRaw,
            price: d.price as number | undefined,
            notional_usd: usd,
            timestamp: d.timestamp as number | undefined,
            symbol: d.symbol as string | undefined,
            quantity: d.quantity as number | undefined,
          };
          wsLargeTradesRef.current = [trade, ...wsLargeTradesRef.current].slice(0, 20);
          dirty.trades = true;
          break;
        }
        case "kline": {
          // 后端 _on_kline 推 KlineEvent：{exchange, market_type, symbol, timestamp(ms open_time),
          //   interval, open, high, low, close, volume, closed}
          // 只接受匹配当前 symbol + 当前 tf 的 kline，避免污染别的 interval cache
          const sym = String(d.symbol ?? "").split("/")[0].toUpperCase();
          if (sym !== symBase) break;
          const kInterval = String(d.interval ?? "");
          if (kInterval !== tf) break;
          const kline: WsKline = {
            open_time: d.timestamp as number | undefined,
            timestamp: d.timestamp as number | undefined,
            open: Number(d.open ?? 0),
            high: Number(d.high ?? 0),
            low: Number(d.low ?? 0),
            close: Number(d.close ?? 0),
            volume: Number(d.volume ?? 0),
            symbol: String(d.symbol ?? ""),
            interval: kInterval,
            closed: Boolean(d.closed),
          };
          wsKlineSeqRef.current += 1;
          setLastWsKline(wsKlineSeqRef.current, kline);
          break;
        }
      }
    }
    // 批量 setState：只在有变化的数据类型上触发
    if (dirty.ticker) setWsTicker({ ...wsTickerRef.current });
    if (dirty.depth) setWsDepth(wsDepthRef.current ? { ...wsDepthRef.current } : null);
    if (dirty.liq) setWsLiqStats(wsLiqStatsRef.current ? { ...wsLiqStatsRef.current } : null);
    if (dirty.funding) setWsFundingRate(wsFundingRateRef.current ? { ...wsFundingRateRef.current } : null);
    if (dirty.trades) setWsLargeTrades([...wsLargeTradesRef.current]);
  }, [symBase, tf, setLastWsKline]);

  useMarketsWs({
    symbols: [`${symBase}/USDT`],
    exchange: "binance",
    marketType: "futures",
    interval: tf,
    onMessageBatch,
    // 非加密品种（A股/美股/大宗）没有加密 WS 频道，选它们时停订阅，
    // 避免继续推旧品种的 ticker/depth（1013 切换"残留"根因之一）
    enabled: isCrypto,
    // 品种切换瞬间清空 WS 派生的旧值，否则上一品种的 ticker/订单簿会留在面板上
    onSymbolsChange: () => {
      wsTickerRef.current = null;
      wsDepthRef.current = null;
      wsLiqStatsRef.current = null;
      wsFundingRateRef.current = null;
      wsLargeTradesRef.current = [];
      setWsTicker(null);
      setWsDepth(null);
      setWsLiqStats(null);
      setWsFundingRate(null);
      setWsLargeTrades([]);
    },
  });

  const { data: liqReal } = useApi<LiqData>(
    isCrypto ? `/api/v1/liqmap/current?symbol=${encodeURIComponent(`${symBase}/USDT`)}` : null
  );
  const { data: tacReal } = useApi<TacData | { rates?: Record<string, number> } | null>(
    isCrypto ? "/api/v1/derivatives/funding-rates" : null
  );
  // 订单簿：后端要 BTC/USDT 格式（symBase 是 "BTC"），depth=5 取前 5 档
  const { data: obReal } = useApi<BackendOrderbook | null>(
    isCrypto ? `/api/v1/markets/${encodeURIComponent(symBase)}/USDT/orderbook?depth=5` : null
  );
  // 大单流
  const { data: wfReal } = useApi<BackendWhaleFlow | null>(
    isCrypto ? `/api/v1/intelligence/whale-flow?symbol=${encodeURIComponent(symBase)}&limit=10` : null
  );

  // Ticker：最新价/24h涨跌/24h成交 (后端真数据,无 mock fallback)
  const { data: tickerReal } = useApi<TickerData>(
    isCrypto ? `/api/v1/markets/${encodeURIComponent(symBase)}/USDT/ticker` : null
  );
  // 持仓量 (open-interest 返回 {snapshots: {symbol: {...}}})
  const { data: oiReal } = useApi<OIData>(
    isCrypto ? "/api/v1/derivatives/open-interest" : null
  );
  // GEX (后端 /api/v1/gex/current?currency=BTC,无 mock fallback)
  // symBase 形如 "BTC",GEX 端点 currency 参数只接受 BTC/ETH
  const gexCurrency = symBase === "BTC" || symBase === "ETH" ? symBase : "BTC";
  const { data: gexReal } = useApi<GexData>(
    isCrypto ? `/api/v1/gex/current?currency=${gexCurrency}` : null
  );

  const liqLong = wsLiqStats?.long_liquidated_usd ?? liqReal?.long;
  const liqShort = wsLiqStats?.short_liquidated_usd ?? liqReal?.short;
  const liqTotal = wsLiqStats?.total_liquidated_usd ?? liqReal?.total;
  // 爆仓热力图:取 liqReal.clusters 前 24 条,按 density 归一化映射 lvl-1..lvl-5
  const liqClusters = liqReal?.clusters ?? [];
  const liqMaxDensity = liqClusters.reduce((m, c) => Math.max(m, c.density ?? 0), 0) || 1;
  const liqCells = Array.from({ length: 24 }).map((_, i) => {
    const c = liqClusters[i];
    if (!c) return { lvl: 1, empty: true };
    const ratio = c.density / liqMaxDensity;
    const lvl = ratio > 0.8 ? 5 : ratio > 0.6 ? 4 : ratio > 0.4 ? 3 : ratio > 0.2 ? 2 : 1;
    return { lvl, empty: false, side: c.dominant_side, price: c.price };
  });
  const tacCur =
    wsFundingRate?.funding_rate ?? (
      tacReal && "rate" in tacReal && typeof tacReal.rate === "number"
        ? tacReal.rate
        : undefined
    );

  // 订单簿：WS 实时优先，REST 兜底
  const obBidsRaw = wsDepth?.bids ?? obReal?.bids;
  const obAsksRaw = wsDepth?.asks ?? obReal?.asks;
  const OB_ASKS = adaptOb(obAsksRaw, "ask");
  const OB_BIDS = adaptOb(obBidsRaw, "bid");
  // 大单流：WS 实时优先（前 10 条），REST 兜底
  const wfFromWs = wsLargeTrades.map((t) => {
    const ts = Number(t.timestamp ?? 0);
    const time = ts > 0
      ? new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
      : "--:--";
    const dir = (t.side || "").toLowerCase();
    const side = dir === "buy" ? "buy" : "sell";
    const usd = Number(t.notional_usd ?? 0);
    const size = usd >= 1e6 ? `${(usd / 1e6).toFixed(1)}M` : `${Math.round(usd / 1e3)}K`;
    return { time, side, size, large: usd >= 1_000_000, venue: "WS", qtyUsd: usd };
  });
  const WHALEFLOWS = wfFromWs.length > 0 ? wfFromWs : adaptWhaleFlow(wfReal?.items);

  // ticker 派生字段：WS 优先，REST 兜底。
  // WS 值必须校验它属于当前标的（wsTicker.sym）：换标时父组件清理 effect 与
  // 子组件 effect 在同一提交里的执行顺序不保证，wsTicker 可能还是旧标的的价，
  // 直接透传会把旧价喂给新图。tickerReal 走 useApi(key=symBase 路径)，换标时
  // SWR 立即返回 undefined，天然无残留，无需额外门控。
  const wsTickerForSym = wsTicker && wsTicker.sym === symBase ? wsTicker : null;
  const tickerLast = wsTickerForSym?.last_price ?? tickerReal?.last_price;
  const tickerChangePct = wsTickerForSym?.change_24h_pct ?? tickerReal?.change_24h_pct;
  const tickerQuoteVol = wsTickerForSym?.quote_volume_24h ?? wsTickerForSym?.volume_24h ?? tickerReal?.quote_volume_24h ?? tickerReal?.volume_24h;

  // 持仓量:open-interest 端点返回 {snapshots: {"BTC/USDT": {...}}},symBase 是 "BTC"
  // 尝试两种 key:"BTC" 和 "BTC/USDT"
  const oiSnap = oiReal?.snapshots;
  const oiEntry = oiSnap
    ? (oiSnap[symBase] ?? oiSnap[`${symBase}/USDT`] ?? oiSnap[`${symBase}-USDT`])
    : undefined;
  const openInterestUsd = oiEntry?.open_interest_usd;
  const openInterestBase = oiEntry?.open_interest;

  // GEX 派生字段
  const gexNet = gexReal?.net_gex;
  const gexKeyLevels = gexReal?.key_levels ?? [];
  // 找两个关键行权价:call_wall (正 gex 峰) + put_wall (负 gex 谷)
  const callWall = gexKeyLevels.find((l) => l.label === "call_wall");
  const putWall = gexKeyLevels.find((l) => l.label === "put_wall");
  const gammaFlip = gexKeyLevels.find((l) => l.label === "gamma_flip");
  // 如果没有 call_wall/put_wall label,退而求其次取最大正/负 gex
  const fallbackCallWall = gexKeyLevels.length > 0
    ? gexKeyLevels.reduce((max, l) => (Math.abs(l.net_gex ?? 0) > Math.abs(max.net_gex ?? 0) ? l : max), gexKeyLevels[0])
    : undefined;
  const gexUpperStrike = callWall?.price ?? fallbackCallWall?.price;
  const gexLowerStrike = putWall?.price ?? gammaFlip?.price;

  // 订单簿中间价:(best_ask + best_bid) / 2
  const obBestAsk = OB_ASKS.length > 0 ? OB_ASKS[OB_ASKS.length - 1]?.px : undefined;
  const obBestBid = OB_BIDS.length > 0 ? OB_BIDS[0]?.px : undefined;
  const obMidPx = (obBestAsk != null && obBestBid != null)
    ? (obBestAsk + obBestBid) / 2
    : (tickerLast ?? undefined);

  // P1-4 拖拽换序 + 移除
  const handleDragStart = (id: string) => (e: React.DragEvent) => {
    setDragId(id);
    e.dataTransfer.effectAllowed = "move";
  };
  const handleDragOver = (id: string) => (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (id !== dragId) setOverId(id);
  };
  const handleDrop = (id: string) => (e: React.DragEvent) => {
    e.preventDefault();
    if (!dragId || dragId === id) {
      setDragId(null);
      setOverId(null);
      return;
    }
    setCardOrder((order) => {
      const from = order.indexOf(dragId);
      const to = order.indexOf(id);
      if (from < 0 || to < 0) return order;
      const next = [...order];
      next.splice(from, 1);
      next.splice(to, 0, dragId);
      return next;
    });
    setDragId(null);
    setOverId(null);
  };
  const handleDragEnd = () => {
    setDragId(null);
    setOverId(null);
  };
  const removeCard = (id: string) => {
    setCardOrder((order) => order.filter((c) => c !== id));
  };
  // P1-8 添加模块菜单
  const addCard = (id: string) => {
    setCardOrder((order) => (order.includes(id) ? order : [...order, id]));
    setAddMenuOpen(false);
  };
  const toggleCard = (id: string) => {
    setCardOrder((order) =>
      order.includes(id) ? order.filter((c) => c !== id) : [...order, id]
    );
  };
  const resetCards = () => {
    setCardOrder([...DEFAULT_CARD_ORDER]);
    setManageMenuOpen(false);
  };
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const hiddenCards = ALL_CARD_IDS.filter(
    (id) => !cardOrder.includes(id) && (isCrypto || !CRYPTO_ONLY_CARDS.has(id))
  );

  // 卡片渲染表（id → JSX）
  const cardContent: Record<string, React.ReactNode> = {
    info: (
      <Card
        key="info"
        id="info"
        title={tr("交易对信息")}
        kind="gold"
        onRemove={() => removeCard("info")}
        onDragStart={handleDragStart("info")}
        onDragOver={handleDragOver("info")}
        onDrop={handleDrop("info")}
        onDragEnd={handleDragEnd}
        dragState={{ dragging: dragId === "info", dragOver: overId === "info" }}
      >
        <div className="info-row">
          <span className="info-lab">{tr("最新价")}</span>
          <span className={`info-val ${tickerChangePct != null ? (tickerChangePct >= 0 ? "pos" : "neg") : ""}`}>
            {tickerLast != null ? tickerLast.toLocaleString() : "—"}
          </span>
        </div>
        <div className="info-row">
          <span className="info-lab">{tr("24h 涨跌")}</span>
          <span className={`info-val ${tickerChangePct != null ? (tickerChangePct >= 0 ? "pos" : "neg") : ""}`}>
            {tickerChangePct != null ? `${tickerChangePct >= 0 ? "+" : ""}${tickerChangePct.toFixed(2)}%` : "—"}
          </span>
        </div>
        <div className="info-row">
          <span className="info-lab">{tr("24h 成交")}</span>
          <span className="info-val">
            {tickerQuoteVol != null ? fmtUsdCompact(tickerQuoteVol) : "—"}
          </span>
        </div>
        <div className="info-row">
          <span className="info-lab">{tr("资金费")}</span>
          <span className="info-val neg">{tacCur != null ? `${(tacCur * 100).toFixed(4)}%` : "—"}</span>
        </div>
        <div className="info-row">
          <span className="info-lab">{tr("持仓量")}</span>
          <span className="info-val gold">
            {openInterestUsd != null ? fmtUsdCompact(openInterestUsd)
              : openInterestBase != null ? `${openInterestBase.toLocaleString()}` : "—"}
          </span>
        </div>
        <div className="info-pyramid">
          <div className="info-pyramid-cell empty">{tr("暂无多空比数据")}</div>
        </div>
      </Card>
    ),
    trade: (
      <Card
        key="trade"
        id="trade"
        title={tr("交易面板")}
        kind="gold"
        onRemove={() => removeCard("trade")}
        onDragStart={handleDragStart("trade")}
        onDragOver={handleDragOver("trade")}
        onDrop={handleDrop("trade")}
        onDragEnd={handleDragEnd}
        dragState={{ dragging: dragId === "trade", dragOver: overId === "trade" }}
      >
        <div className="tp-grid">
          <div className="tp-label">{tr("品种")}</div>
          <div className="tp-input" style={{ display: "flex", alignItems: "center", fontWeight: 600 }}>
            {/* 只有加密是永续合约（-PERP）；A股/美股/大宗是现货股票/期货，不带合约后缀 */}
            {isCrypto ? `${activeSymbol}-PERP` : activeSymbol}
          </div>
          <div className="tp-label">{tr("数量")}</div>
          <input className="tp-input" defaultValue="" />
          <div className="tp-label">{tr("价格")}</div>
          <input className="tp-input" defaultValue="" />
        </div>
        <div className="tp-sides" style={{ marginTop: 4 }}>
          <div className="tp-side buy">{tr("买入")}</div>
          <div className="tp-side sell">{tr("卖出")}</div>
        </div>
        <div className="tp-label" style={{ marginTop: 4 }}>{tr("有效期")}</div>
        <div className="tp-tif">
          <div className="tp-tif-btn active">GTC</div>
          <div className="tp-tif-btn">IOC</div>
          <div className="tp-tif-btn">FOK</div>
        </div>
        <div className="tp-meta">
          <span>{tr("保证金 —")}</span>
          <span>{tr("滑点 —")}</span>
        </div>
      </Card>
    ),
    orderbook: (
      <Card
        key="orderbook"
        id="orderbook"
        title={tr("订单簿")}
        count={tr("深度")}
        onRemove={() => removeCard("orderbook")}
        onDragStart={handleDragStart("orderbook")}
        onDragOver={handleDragOver("orderbook")}
        onDrop={handleDrop("orderbook")}
        onDragEnd={handleDragEnd}
        dragState={{ dragging: dragId === "orderbook", dragOver: overId === "orderbook" }}
      >
        <div className="orderbook">
          <div className="ob-section">
            {OB_ASKS.slice().reverse().map((r, i) => (
              <div
                key={i}
                className="ob-row ask"
                onClick={() => window.dispatchEvent(new CustomEvent("trade-fill-price", { detail: r.px }))}
                style={{ cursor: "pointer" }}
              >
                <span className="px">{r.px.toLocaleString()}</span>
                <span className="sz">{r.sz.toFixed(3)}</span>
                <span className="sum">{r.sum.toFixed(2)}</span>
                <span className="bar" style={{ width: `${Math.min(r.sz * 40, 80)}%` }} />
              </div>
            ))}
          </div>
          <div className="ob-mid">
            <span className={`px ${obMidPx != null ? (tickerChangePct != null ? (tickerChangePct >= 0 ? "pos" : "neg") : "") : ""}`}>
              {obMidPx != null ? obMidPx.toLocaleString() : "—"}
            </span>
            <span className="arrow">
              {tickerChangePct != null ? `${tickerChangePct >= 0 ? "↑" : "↓"} ${Math.abs(tickerChangePct).toFixed(2)}%` : "—"}
            </span>
          </div>
          <div className="ob-section">
            {OB_BIDS.map((r, i) => (
              <div
                key={i}
                className="ob-row bid"
                onClick={() => window.dispatchEvent(new CustomEvent("trade-fill-price", { detail: r.px }))}
                style={{ cursor: "pointer" }}
              >
                <span className="px">{r.px.toLocaleString()}</span>
                <span className="sz">{r.sz.toFixed(3)}</span>
                <span className="sum">{r.sum.toFixed(2)}</span>
                <span className="bar" style={{ width: `${Math.min(r.sz * 40, 80)}%` }} />
              </div>
            ))}
          </div>
          {/* P1 订单簿深度图（设计稿 L677-727 + L5195-5278） */}
          {(() => {
            const W = 200, H = 80;
            if (OB_ASKS.length === 0 && OB_BIDS.length === 0) {
              return <div className="ob-depth"><div className="text-center text-[10px] text-ink-4 py-2">{tr("暂无数据")}</div></div>;
            }
            const midPx = OB_BIDS[0]?.px ?? OB_ASKS[0]?.px ?? 0;
            const bidMin = OB_BIDS[OB_BIDS.length - 1]?.px;  // 最远 bid 价（最低）
            const askMax = OB_ASKS[0]?.px;                    // 最优 ask 价（最高）
            // 单边空时镜像另一边跨度，避免 midPx±50 硬编码导致坐标塌陷
            const span = (() => {
              if (bidMin != null && askMax != null) return Math.max(askMax - midPx, midPx - bidMin);
              if (bidMin != null) return midPx - bidMin;   // 只有 bids
              if (askMax != null) return askMax - midPx;   // 只有 asks
              return 1;
            })();
            const pxMin = bidMin ?? midPx - span;
            const pxMax = askMax ?? midPx + span;
            const midX = W / 2;
            const cumAsk: number[] = [];
            const cumBid: number[] = [];
            OB_ASKS.forEach((r, i) => { cumAsk[i] = (i === 0 ? 0 : cumAsk[i - 1]) + r.sz; });
            OB_BIDS.forEach((r, i) => { cumBid[i] = (i === 0 ? 0 : cumBid[i - 1]) + r.sz; });
            const maxCum = Math.max(cumAsk[cumAsk.length - 1] ?? 1, cumBid[cumBid.length - 1] ?? 1);
            const toY = (cum: number) => H - (cum / maxCum) * (H - 4) - 2;
            const toXAsk = (px: number) => midX + ((px - midPx) / (pxMax - midPx || 1)) * (W - midX);
            const toXBid = (px: number) => midX - ((midPx - px) / (midPx - pxMin || 1)) * midX;
            const askMean = OB_ASKS.reduce((s, r) => s + r.sz, 0) / (OB_ASKS.length || 1);
            const bidMean = OB_BIDS.reduce((s, r) => s + r.sz, 0) / (OB_BIDS.length || 1);
            const wallMult = 1.6;
            let askPath = `M ${midX} ${H} `;
            for (let i = OB_ASKS.length - 1; i >= 0; i--) {
              askPath += `L ${toXAsk(OB_ASKS[i].px).toFixed(2)} ${toY(cumAsk[i]).toFixed(2)} `;
            }
            askPath += `L ${toXAsk(OB_ASKS[0]?.px ?? midPx).toFixed(2)} ${H} Z`;
            let bidPath = `M ${midX} ${H} `;
            for (let i = 0; i < OB_BIDS.length; i++) {
              bidPath += `L ${toXBid(OB_BIDS[i].px).toFixed(2)} ${toY(cumBid[i]).toFixed(2)} `;
            }
            bidPath += `L ${toXBid(OB_BIDS[OB_BIDS.length - 1]?.px ?? midPx).toFixed(2)} ${H} Z`;
            const wallDots: { x: number; y: number; side: "ask" | "bid" }[] = [];
            OB_ASKS.forEach((r, i) => {
              if (r.sz > askMean * wallMult) wallDots.push({ x: toXAsk(r.px), y: toY(cumAsk[i]), side: "ask" });
            });
            OB_BIDS.forEach((r, i) => {
              if (r.sz > bidMean * wallMult) wallDots.push({ x: toXBid(r.px), y: toY(cumBid[i]), side: "bid" });
            });
            const fmtP = (v: number) => v >= 1000 ? (v / 1000).toFixed(2) + "k" : v.toFixed(1);
            return (
              <div className="ob-depth">
                <div className="ob-depth-head">
                  <span className="ob-depth-title">{tr("深度图")}</span>
                  <div className="ob-depth-legend">
                    <span><span className="dot ask" />Ask</span>
                    <span><span className="dot bid" />Bid</span>
                    <span><span className="dot wall" />{tr("墙")} {wallDots.length}</span>
                  </div>
                </div>
                <div className="ob-depth-canvas">
                  <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
                    <defs>
                      <linearGradient id="askGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#c53637" stopOpacity="0.5" />
                        <stop offset="100%" stopColor="#c53637" stopOpacity="0.1" />
                      </linearGradient>
                      <linearGradient id="bidGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#278733" stopOpacity="0.5" />
                        <stop offset="100%" stopColor="#278733" stopOpacity="0.1" />
                      </linearGradient>
                    </defs>
                    <line x1={midX} y1={0} x2={midX} y2={H} stroke="var(--color-border)" strokeWidth={0.5} strokeDasharray="2,2" />
                    <path d={askPath} fill="url(#askGrad)" stroke="#f14d4c" strokeWidth={0.8} />
                    <path d={bidPath} fill="url(#bidGrad)" stroke="#31aa40" strokeWidth={0.8} />
                    {wallDots.map((d, i) => (
                      <circle
                        key={i}
                        cx={d.x.toFixed(2)}
                        cy={d.y.toFixed(2)}
                        r={2.2}
                        fill={d.side === "ask" ? "#fa6863" : "#5bb661"}
                        stroke="#e3b650"
                        strokeWidth={1}
                      />
                    ))}
                  </svg>
                </div>
                <div className="ob-depth-axis">
                  <span>{fmtP(pxMin)}</span>
                  <span style={{ color: "var(--color-ink-3)" }}>{fmtP(midPx)}</span>
                  <span>{fmtP(pxMax)}</span>
                </div>
              </div>
            );
          })()}
        </div>
      </Card>
    ),
    whaleflow: (
      <Card
        key="whaleflow"
        id="whaleflow"
        title={tr("大单流")}
        count={String(WHALEFLOWS.length)}
        onRemove={() => removeCard("whaleflow")}
        onDragStart={handleDragStart("whaleflow")}
        onDragOver={handleDragOver("whaleflow")}
        onDrop={handleDrop("whaleflow")}
        onDragEnd={handleDragEnd}
        dragState={{ dragging: dragId === "whaleflow", dragOver: overId === "whaleflow" }}
      >
        {/* P1-3 CVD 累计 + 交易所分布 */}
        <div className="wf-cvd">
          {(() => {
            if (WHALEFLOWS.length === 0) {
              return <div className="text-center text-[10px] text-ink-4 py-2">{tr("暂无数据")}</div>;
            }
            const buyUsd = WHALEFLOWS.filter((w) => w.side === "buy").reduce((s, w) => s + w.qtyUsd, 0);
            const sellUsd = WHALEFLOWS.filter((w) => w.side === "sell").reduce((s, w) => s + w.qtyUsd, 0);
            const total = buyUsd + sellUsd || 1;
            const buyPct = (buyUsd / total) * 100;
            const delta = buyUsd - sellUsd;
            return (
              <>
                <div className="wf-cvd-bar">
                  <div className="wf-cvd-buy" style={{ width: `${buyPct}%` }} />
                  <div className="wf-cvd-sell" style={{ width: `${100 - buyPct}%` }} />
                </div>
                <div className="wf-cvd-meta">
                  <span className="pos">+${(buyUsd / 1e6).toFixed(2)}M</span>
                  <span className={delta >= 0 ? "pos" : "neg"}>Δ {delta >= 0 ? "+" : ""}{(delta / 1e6).toFixed(2)}M</span>
                  <span className="neg">-${(sellUsd / 1e6).toFixed(2)}M</span>
                </div>
              </>
            );
          })()}
        </div>
        {WHALEFLOWS.length === 0 ? null : WHALEFLOWS.map((w, i) => (
          <div key={i} className="wf-row" data-tip={`${w.venue} · $${(w.qtyUsd / 1e3).toFixed(0)}K`}>
            <span className="wf-time">{w.time}</span>
            <span className={`wf-side ${w.side}`}>{w.side === "buy" ? tr("买入") : tr("卖出")}</span>
            <span className={`wf-size ${w.large ? "large" : ""}`}>{w.size}</span>
            <span className="wf-venue">{w.venue}</span>
          </div>
        ))}
      </Card>
    ),
    liquidation: (
      <Card
        key="liquidation"
        id="liquidation"
        title={tr("爆仓热力 {p0}", { p0: liqReal ? `[${liqClusters.length}c]` : "[null]" })}
        folded
        onRemove={() => removeCard("liquidation")}
        onDragStart={handleDragStart("liquidation")}
        onDragOver={handleDragOver("liquidation")}
        onDrop={handleDrop("liquidation")}
        onDragEnd={handleDragEnd}
        dragState={{ dragging: dragId === "liquidation", dragOver: overId === "liquidation" }}
      >
        <div className="info-row">
          <span className="info-lab">{tr("多头爆仓")}</span>
          <span className="info-val pos">{liqLong != null ? `${liqLong.toFixed(1)}M` : "—"}</span>
        </div>
        <div className="info-row">
          <span className="info-lab">{tr("空头爆仓")}</span>
          <span className="info-val neg">{liqShort != null ? `${liqShort.toFixed(1)}M` : "—"}</span>
        </div>
        <div className="info-row">
          <span className="info-lab">{tr("总爆仓")}</span>
          <span className="info-val">{liqTotal != null ? `${liqTotal.toFixed(1)}M` : "—"}</span>
        </div>
        <div className="liq-mini-grid" style={{ marginTop: 4 }}>
          {liqCells.map((cell, i) => (
            <div
              key={i}
              className={`hm-cell lvl-${cell.lvl}`}
              title={cell.empty ? tr("无数据") : `${cell.side ?? ""} @ ${cell.price?.toFixed(0)}`}
            >
              {cell.empty ? "—" : ""}
            </div>
          ))}
        </div>
      </Card>
    ),
    gex: (
      <Card
        key="gex"
        id="gex"
        title="GEX"
        folded
        onRemove={() => removeCard("gex")}
        onDragStart={handleDragStart("gex")}
        onDragOver={handleDragOver("gex")}
        onDrop={handleDrop("gex")}
        onDragEnd={handleDragEnd}
        dragState={{ dragging: dragId === "gex", dragOver: overId === "gex" }}
      >
        {gexReal == null ? (
          <div className="text-center text-[10px] text-ink-4 py-2">{tr("暂无 GEX 数据")}</div>
        ) : (
          <>
            <div className="info-row">
              <span className="info-lab">{tr("上阻力")}</span>
              <span className="info-val pos">
                {gexUpperStrike != null ? gexUpperStrike.toLocaleString() : "—"}
              </span>
            </div>
            <div className="info-row">
              <span className="info-lab">{tr("下支撑")}</span>
              <span className="info-val neg">
                {gexLowerStrike != null ? gexLowerStrike.toLocaleString() : "—"}
              </span>
            </div>
            <div className="info-row">
              <span className="info-lab">{tr("净 GEX")}</span>
              <span className={`info-val ${gexNet != null ? (gexNet >= 0 ? "pos" : "neg") : ""}`}>
                {gexNet != null ? fmtUsdCompact(Math.abs(gexNet)) : "—"}
              </span>
            </div>
            <div className="info-row">
              <span className="info-lab">{tr("做市定位")}</span>
              <span className="info-val">{gexReal.dealer_positioning ?? "—"}</span>
            </div>
          </>
        )}
      </Card>
    ),
    funding: (
      <Card
        key="funding"
        id="funding"
        title={tr("费率")}
        folded
        onRemove={() => removeCard("funding")}
        onDragStart={handleDragStart("funding")}
        onDragOver={handleDragOver("funding")}
        onDrop={handleDrop("funding")}
        onDragEnd={handleDragEnd}
        dragState={{ dragging: dragId === "funding", dragOver: overId === "funding" }}
      >
        <div className="fund-row">
          <span className="lab">{tr("当前")}</span>
          <span className={`val ${tacCur != null ? (tacCur >= 0 ? "pos" : "neg") : "text-ink-4"}`}>
            {tacCur != null ? `${(tacCur * 100).toFixed(4)}%` : "—"}
          </span>
        </div>
        <div className="fund-bar-wrap">
          <div
            className={`fund-bar-fill ${tacCur != null ? (tacCur >= 0 ? "pos" : "neg") : ""}`}
            style={{ width: `${Math.min(Math.abs(tacCur ?? 0) * 500, 100)}%` }}
          />
        </div>
        <div className="fund-row">
          <span className="lab">{tr("下一周期")}</span>
          <span className="val text-ink-4">—</span>
        </div>
        <div className="fund-next">
          {tr("距下次结算")} <span className="gold">{fundCountdown || "--:--:--"}</span>
        </div>
      </Card>
    ),
  };

  return (
    <div
      className="chart-area"
      style={
        dockOpen && !isSuperRest
          ? { gridTemplateColumns: "minmax(0, 1fr) auto 320px" }
          : undefined
      }
    >
      <div className="chart-main">
        <div className="chart-toolbar">
          {/* 当前品种 + 自选开关：kf 原来只有 ⌘K 里能加自选，看图时无法取消/添加
              （用户 1013 反馈）。这里对"正在看的那个交易对"提供显式开关。 */}
          <button
            type="button"
            className={`chart-tool chart-fav-toggle ${isFavActive ? "active" : ""}`}
            data-testid="kf-fav-toggle"
            aria-pressed={isFavActive}
            aria-label={isFavActive ? tr("取消自选 {activeSymbol}", { activeSymbol: activeSymbol }) : tr("加入自选 {activeSymbol}", { activeSymbol: activeSymbol })}
            title={isFavActive ? tr("取消自选") : tr("加入自选")}
            onClick={() => toggleFavorite(activeSymbol)}
          >
            <span className="kf-fav-star">{isFavActive ? "★" : "☆"}</span>
            <span className="kf-fav-sym">{activeSymbol}</span>
          </button>
          <div className="chart-tool-sep" />
          {/* 当前图型：只有 K 线一种，是状态不是开关 —— 用静态标签而非假按钮，
              否则会得到一个能聚焦但按下去没反应的控件（affordance 撒谎）。 */}
          <span className="chart-tool chart-tool-static" aria-current="true">{tr("K 线")}</span>
          <div className="chart-tool-sep" />
          <button
            type="button"
            className={`chart-tool ${tools.ma ? "active" : ""}`}
            aria-pressed={tools.ma}
            onClick={() => setTools((t) => ({ ...t, ma: !t.ma }))}
          >
            MA
          </button>
          <button
            type="button"
            className={`chart-tool ${tools.vol ? "active" : ""}`}
            aria-pressed={tools.vol}
            onClick={() => setTools((t) => ({ ...t, vol: !t.vol }))}
          >
            VOL
          </button>
          <button
            type="button"
            className={`chart-tool ${tools.macd ? "active" : ""}`}
            aria-pressed={tools.macd}
            onClick={() => setTools((t) => ({ ...t, macd: !t.macd }))}
          >
            MACD
          </button>
          <div className="chart-tool-sep" />
          {!isSuperRest && <DrawingTools symbol={activeSymbol} />}
          <div className="chart-tool-sep" />
          <div className="chart-tool-sep" />
          {/* 拿当前图表品种去回测中心下单：A股/美股走 ashare-backtest，其余走 backtests */}
          <Link
            href={
              activeMarket === "ashare"
                ? `/research/ashare-backtest?symbol=${encodeURIComponent(activeSymbol)}`
                : `/trade/backtests?symbol=${encodeURIComponent(toSymbolPair(activeSymbol))}`
            }
            className="chart-tool"
            title={`用 ${activeSymbol} 发起回测`}
          >
            回测
          </Link>
          <Link
            href="/watch/sig?tab=social"
            className={`chart-tool ${sentimentValue != null ? "" : "text-ink-4"}`}
            title={
              sentimentValue != null
                ? `社交综合情绪 ${sentimentValue.toFixed(3)}（${sentimentSamples} 个社区）`
                : "社交情绪：暂无可用的社区样本"
            }
          >
            情绪 {sentimentValue != null ? sentimentValue.toFixed(2) : "—"}
          </Link>
          <div className="chart-tool-sep" />
          {/* 手画价格预警。非加密市场显式禁用：轮询 loop 的价格源写死
              binance/futures，这里放开只会得到一条永远不响的线。 */}
          <button
            type="button"
            data-testid="kf-alert-toggle"
            className={`chart-tool ${drawMode ? "active" : canUseAlerts ? "" : "chart-tool-disabled"}`}
            aria-pressed={drawMode}
            disabled={!canUseAlerts}
            aria-disabled={!canUseAlerts}
            title={
              canUseAlerts
                ? drawMode
                  ? "退出画线（点图上的价位可挂预警）"
                  : "画价格预警线"
                : "价格预警目前仅支持加密货币：实时价源为币安合约，其它市场取不到价，挂了也不会触发"
            }
            onClick={() => setDrawMode((v) => !v)}
          >
            预警
          </button>
          <div className="chart-tool-layout" role="group" aria-label="图表布局">
            <button
              type="button"
              className={`chart-layout-btn ${layoutPreset === "default" ? "active" : ""}`}
              aria-pressed={layoutPreset === "default"}
              onClick={() => setLayoutPreset("default")}
              title={tr("单图")}
            >
              ▢
            </button>
            <button
              type="button"
              className={`chart-layout-btn ${layoutPreset === "grid4" ? "active" : ""}`}
              aria-pressed={layoutPreset === "grid4"}
              onClick={() => setLayoutPreset("grid4")}
              title={tr("四图")}
            >
              ◫
            </button>
            <button
              type="button"
              className={`chart-layout-btn ${layoutPreset === "charts9" ? "active" : ""}`}
              aria-pressed={layoutPreset === "charts9"}
              onClick={() => setLayoutPreset("charts9")}
              title={tr("九图")}
            >
              ⌗
            </button>
          </div>
          <WorkspaceLink source="kf" />
        </div>
        {/* 指标区已并入对象树（DrawingTools 的 indicators 区），页面内不再有独立弹层 */}
        {/* super-bar 已砍除（kf 验收 d14ba05）：市场/周期/星标全走 ⌘K 与 KlineChart 内建 tfOptions */}
        <div
          className="chart-body"
          style={{
            display: "grid",
            gridTemplateColumns:
              isSuperRest
                ? "minmax(0, 1fr)"
                : layoutPreset === "charts9"
                ? "minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr)"
                : layoutPreset === "grid4"
                ? "minmax(0, 1fr) minmax(0, 1fr)"
                : layoutPreset === "grid2" || layoutPreset === "charts6"
                ? "minmax(0, 1fr) minmax(0, 1fr)"
                : "minmax(0, 1fr)",
            gridTemplateRows:
              isSuperRest
                ? "minmax(0, 1fr)"
                : layoutPreset === "grid4"
                ? "minmax(0, 1fr) minmax(0, 1fr)"
                : layoutPreset === "charts6"
                ? "minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr)"
                : layoutPreset === "charts9"
                ? "minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr)"
                : "minmax(0, 1fr)",
            gap: 2,
          }}
        >
          {isSuperRest ? (
            <KlineChart
              sym={superSymbol}
              tf={superTf}
              bars={multiMarketBars.candles}
              label={`${superSymbol} · ${tr(MARKET_LABEL[superMarket] ?? superMarket)} · ${sourceLabel(superMarket, tr)}`}
              tfOptions={intervals}
              tfLabels={TF_LABELS}
              onTfChange={setSuperTf}
              allowFallback={false}
            />
          ) : (
            Array.from({
              length:
                layoutPreset === "charts9" ? 9
                : layoutPreset === "grid4" ? 4
                : layoutPreset === "charts6" ? 6
                : layoutPreset === "grid2" ? 2
                : 1,
            }).map((_, i) => {
              // P1-9 多图模式独立数据：每个 cell 不同品种
              const symbolsForGrid = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK"];
              const idx = i % symbolsForGrid.length;
              const symOverride = layoutPreset === "grid2" || layoutPreset === "grid4" || layoutPreset === "charts6" || layoutPreset === "charts9"
                ? symbolsForGrid[idx]
                : undefined;
              return (
                <KfChart
                  // key = 这张图的身份(标的@周期),**不是**网格序号。原先用 {i}
                  // 时换标的/换周期不重挂载,组件内的 prevDataRef/rowsRef/series
                  // 全部跨图存活,而两标的(两周期)的 bar 形状天然相同 → 渲染被
                  // 判为 skip、画布留在旧图(2026-10-15 实测 ~4s 残留 + 嵌合K线)。
                  // 重挂载是结构性保证:实例与所有 ref 一起重建,泄漏在构造上不可能。
                  // 组件内另有 dataKey 不变量兜底 pa-panel 等不传 key 的挂载点。
                  key={`${symOverride ?? symBase}@${tf}`}
                  showMA={tools.ma}
                  showVOL={tools.vol}
                  showMACD={tools.macd}
                  showEma20={tools.ema20}
                  showEma50={tools.ema50}
                  showBoll={tools.boll}
                  showRsi={tools.rsi}
                  showKdj={tools.kdj}
                  showAtr={tools.atr}
                  showIctSwings={tools.ictSwings}
                  showIctEvents={tools.ictEvents}
                  showIctKillzone={tools.ictKillzone}
                  showIctDealingRange={tools.ictDealRange}
                  ictDisplay={ictDisplay}
                  symbolOverride={symOverride}
                  tickerLast={symOverride ? null : tickerLast}
                  alertLines={alertLinesForCell(symOverride ?? symBase)}
                  drawMode={drawMode && canUseAlerts}
                  onDrawLevel={(p) => void handleDrawLevel(p, symOverride ?? symBase)}
                  onDeleteAlert={(id) => void handleDeleteAlert(id)}
                />
              );
            })
          )}
        </div>
        <div className="kline-timeline">
          <div className="kt-track signals">
            <div className="kt-track-label">{tr("信号")}</div>
            <div className="kt-track-body">
              <svg viewBox="0 0 100 28" preserveAspectRatio="none">
                {tracks.signals.map((s, i) => {
                  const x = xScale(s.at);
                  return s.type === "buy"
                    ? <polygon key={i} points={`${x},24 ${x - 0.6},20 ${x + 0.6},20`} className="kt-signal-buy" />
                    : <polygon key={i} points={`${x},4 ${x - 0.6},8 ${x + 0.6},8`} className="kt-signal-sell" />;
                })}
              </svg>
            </div>
          </div>
          <div className="kt-track positions">
            <div className="kt-track-label">{tr("持仓")}</div>
            <div className="kt-track-body">
              <svg viewBox="0 0 100 40" preserveAspectRatio="none">
                {tracks.positions.map((p, i) => {
                  const x = p.from * CW;
                  const w = (p.to - p.from + 1) * CW;
                  const cls = p.side === "long" ? "kt-position-long" : "kt-position-short";
                  const pnlStr = (p.pnl > 0 ? "+" : "") + p.pnl.toFixed(1) + "%";
                  const fill = p.pnl > 0 ? "#5bb661" : "#f14d4c";
                  return (
                    <g key={i}>
                      <rect x={x} y={14} width={w} height={12} className={cls} />
                      <text x={x + w / 2} y={36} fill={fill} fontSize={2.5} textAnchor="middle">{pnlStr}</text>
                    </g>
                  );
                })}
              </svg>
            </div>
          </div>
          <div className="kt-track agent">
            <div className="kt-track-label">Agent</div>
            <div className="kt-track-body" style={{ position: "relative" }}>
              <svg viewBox="0 0 100 28" preserveAspectRatio="none">
                {tracks.ai.map((a, i) => {
                  const x = xScale(a.at);
                  const c = AI_COLOR[a.action];
                  const abbr = a.action === "open_long" ? "OL"
                    : a.action === "close_long" ? "CL"
                    : a.action === "open_short" ? "OS" : "CS";
                  const isHover = hoverAi === i;
                  return (
                    <g
                      key={i}
                      style={{ cursor: "pointer" }}
                      onMouseEnter={() => setHoverAi(i)}
                      onMouseLeave={() => setHoverAi((v) => (v === i ? null : v))}
                      onClick={() => {
                        const atBar = a.at;
                        window.dispatchEvent(new CustomEvent("kf-scroll-to-bar", { detail: { at: atBar } }));
                      }}
                    >
                      <circle
                        cx={x}
                        cy={14}
                        r={isHover ? 2.5 : 1.5}
                        fill={c}
                        className="kt-ai-dot"
                        style={{ transition: "r 0.15s" }}
                      />
                      <text x={x} y={16} fill="white" fontSize={2} textAnchor="middle" fontWeight={700} style={{ pointerEvents: "none" }}>{abbr}</text>
                      {isHover && <line x1={x} y1={0} x2={x} y2={28} stroke={c} strokeWidth={0.4} strokeDasharray="1,1" />}
                    </g>
                  );
                })}
              </svg>
              {hoverAi !== null && tracks.ai[hoverAi] && (
                <div className="kt-ai-tip" style={{
                  position: "absolute",
                  left: `${Math.min(85, Math.max(5, xScale(tracks.ai[hoverAi].at)))}%`,
                  bottom: "100%",
                  marginBottom: 2,
                  pointerEvents: "none",
                }}>
                  <div className="kt-ai-tip-action" style={{ color: AI_COLOR[tracks.ai[hoverAi].action] }}>
                    {AI_LABEL[tracks.ai[hoverAi].action]} · #{hoverAi + 1}
                  </div>
                  <div className="kt-ai-tip-reason">{tracks.ai[hoverAi].reason}</div>
                  <div className="kt-ai-tip-meta">
                    <span>tool: {tracks.ai[hoverAi].tool}</span>
                    <span>{tracks.ai[hoverAi].durationMs}ms</span>
                    <span>conf {(tracks.ai[hoverAi].confidence * 100).toFixed(0)}%</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
      {/* 对象树:`.chart-main` 的兄弟而非子元素 —— `.chart-main { overflow:hidden }`
          会剪掉绝对定位的子元素,这是它早先必须走 portal 的原因。作为独立网格列,
          它既不占画布(收起时整列不渲染),展开时也不挤压图表的可见高度。
          包裹节点只在展开时存在:`.chart-area` 是 2 列网格,空节点会多占一格。 */}
      {!isSuperRest && dockOpen && (
        <ObjectTreeDock
          symbol={activeSymbol}
          indicators={treeIndicators}
          onToggleIndicator={toggleIndicator}
        />
      )}
      <div className="right-panel">
        {/* P1-8 添加模块菜单 */}
        <div className="rp-add-wrap">
          <button
            className="rp-add-btn"
            onClick={() => setAddMenuOpen((v) => !v)}
            disabled={hiddenCards.length === 0}
            title={tr("添加模块")}
          >
            {tr("+ 添加模块")} {hiddenCards.length > 0 && `(${hiddenCards.length})`}
          </button>
          <button
            className="rp-manage-btn"
            onClick={() => setManageMenuOpen((v) => !v)}
            title={tr("管理模块")}
          >
            <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
              <path d="M6 1.5 L7.2 2.2 L8.5 2 L9 3.2 L10.2 3.8 L9.8 5 L10.2 6.2 L9 6.8 L8.5 8 L7.2 7.8 L6 8.5 L4.8 7.8 L3.5 8 L3 6.8 L1.8 6.2 L2.2 5 L1.8 3.8 L3 3.2 L3.5 2 L4.8 2.2 Z" stroke="currentColor" strokeWidth="0.8" />
              <circle cx="6" cy="5" r="1.4" stroke="currentColor" strokeWidth="0.8" />
            </svg>
            {tr("管理")}
          </button>
          {addMenuOpen && hiddenCards.length > 0 && (
            <div className="rp-add-menu">
              {hiddenCards.map((id) => (
                <div
                  key={id}
                  className="rp-add-item"
                  onClick={() => addCard(id)}
                >
                  + {tr(CARD_TITLES[id] ?? id)}
                </div>
              ))}
            </div>
          )}
          {manageMenuOpen && (
            <div className="rp-manage-menu">
              <div className="rp-manage-head">
                <span>{tr("模块管理")}</span>
                <button className="rp-manage-reset" onClick={resetCards} title={tr("恢复默认")}>
                  {tr("重置")}
                </button>
              </div>
              {ALL_CARD_IDS.filter((id) => isCrypto || !CRYPTO_ONLY_CARDS.has(id)).map((id) => {
                const visible = cardOrder.includes(id);
                return (
                  <label key={id} className="rp-manage-row">
                    <input
                      type="checkbox"
                      checked={visible}
                      onChange={() => toggleCard(id)}
                    />
                    <span>{tr(CARD_TITLES[id] ?? id)}</span>
                  </label>
                );
              })}
            </div>
          )}
        </div>
        {/* P1-4 按拖拽顺序渲染 + 可移除。非加密市场隐藏加密专属卡片（见 CRYPTO_ONLY_CARDS） */}
        {cardOrder
          .filter((id) => cardContent[id])
          .filter((id) => isCrypto || !CRYPTO_ONLY_CARDS.has(id))
          .map((id) => cardContent[id])}
      </div>
    </div>
  );
}
