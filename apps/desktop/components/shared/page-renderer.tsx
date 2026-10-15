"use client";

import { lazy, Suspense } from "react";
import { preload } from "swr";
import type { PageMeta } from "@/lib/page-manifest";
import { fetcher } from "@/lib/hooks/use-api";
import { isDynamicPage } from "@/lib/renderer-config";
import { useMarketStore } from "@/lib/store/market";
import { DataTablePage } from "./data-table-page";
import { SymbolPanelPage } from "./symbol-panel-page";
import { ResearchPanelPage } from "./research-panel-page";
import { ChatPanelPage } from "./chat-panel-page";
import { DetailPageView } from "./detail-page-view";
import { SkeletonGrid } from "./skeleton";

// Widget Registry — React.lazy 按需加载，减少首屏 JS 体积
// 43 个页面组件从静态 import 改为 dynamic import
// 4 个通用渲染器保持静态（首屏即用）

const lazyComp = <T extends Record<string, unknown>>(
  loader: () => Promise<{ default: React.ComponentType<T> }>,
) => lazy(loader);

interface WidgetLoaderModule {
  default: React.ComponentType;
  preloadData?: () => void;
}

const WIDGET_LOADERS: Record<string, () => Promise<WidgetLoaderModule>> = {
  // 17 PAGE_RENDERERS
  liqmap: () => import("@/components/watch/liqmap-page").then((m) => ({ default: m.LiqmapPage, preloadData: m.preloadData })),
  liqratio: () => import("@/components/watch/liqratio-page").then((m) => ({ default: m.LiqRatioPage, preloadData: m.preloadData })),
  gex: () => import("@/components/watch/gex-page").then((m) => ({ default: m.GexPage, preloadData: m.preloadData })),
  agg: () => import("@/components/watch/agg-page").then((m) => ({ default: m.AggPage })),
  tac: () => import("@/components/watch/tac-page").then((m) => ({ default: m.TacPage })),
  sig: () => import("@/components/watch/sig-page").then((m) => ({ default: m.SigPage })),
  vchart: () => import("@/components/watch/vchart-page").then((m) => ({ default: m.VchartPage })),
  poly: () => import("@/components/watch/poly-page").then((m) => ({ default: m.PolyPage })),
  dex: () => import("@/components/watch/dex-page").then((m) => ({ default: m.DexPage })),
  pa: () => import("@/components/research/pa-page").then((m) => ({ default: m.PaPage })),
  experiments: () => import("@/components/research/experiments-page").then((m) => ({ default: m.ExperimentsPage })),
  "experiment-evidence": () => import("@/components/research/evidence-report-page").then((m) => ({ default: m.ExperimentEvidencePage })),
  episodes: () => import("@/components/research/episodes-page").then((m) => ({ default: m.EpisodesPage })),
  grid: () => import("@/components/trade/grid-page").then((m) => ({ default: m.GridPage })),
  tof: () => import("@/components/trade/tof-page").then((m) => ({ default: m.TofPage })),
  skill: () => import("@/components/system/skill-page").then((m) => ({ default: m.SkillPage })),
  mcp: () => import("@/components/system/mcp-page").then((m) => ({ default: m.McpPage })),
  set: () => import("@/components/system/set-page").then((m) => ({ default: m.SetPage })),
  // 9 主页面
  kf: () => import("@/components/watch/kf-page").then((m) => ({ default: m.KfPage, preloadData: m.preloadData })),
  kfi: () => import("@/components/watch/kfi-page").then((m) => ({ default: m.KfiPage })),
  mk: () => import("@/components/watch/mk-page").then((m) => ({ default: m.MkPage })),
  rsch: () => import("@/components/research/rsch-page").then((m) => ({ default: m.RschPage })),
  missions: () => import("@/components/research/missions-page").then((m) => ({ default: m.MissionsPage })),
  "strategy-cockpit": () => import("@/components/research/strategy-page").then((m) => ({ default: m.StrategyCockpitPage })),
  "strategies-strategy-id": () => import("@/components/strategy/strategy-workbench").then((m) => ({ default: m.StrategyWorkbenchPage })),
  today: () => import("@/components/research/today-page").then((m) => ({ default: m.TodayPage })),
  mem: () => import("@/components/research/memory-surface-page").then((m) => ({ default: m.MemorySurfacePage })),
  strat: () => import("@/components/research/strat-page").then((m) => ({ default: m.StratPage })),
  pos: () => import("@/components/trade/pos-page").then((m) => ({ default: m.PosPage })),
  paper: () => import("@/components/trade/paper-page").then((m) => ({ default: m.PaperPage })),
  trd: () => import("@/components/trade/trd-page").then((m) => ({ default: m.TrdPage })),
  live_log: () => import("@/components/trade/live_log-page").then((m) => ({ default: m.LiveLogPage })),
  dash: () => import("@/components/system/dash-page").then((m) => ({ default: m.DashPage })),
  // 4 设计稿扩展页
  vol_surface: () => import("@/components/watch/vol_surface-page").then((m) => ({ default: m.VolSurfacePage })),
  smart_money: () => import("@/components/watch/smart_money-page").then((m) => ({ default: m.SmartMoneyPage })),
  alpha_mine: () => import("@/components/research/alpha_mine-page").then((m) => ({ default: m.AlphaMinePage })),
  portfolio: () => import("@/components/research/portfolio-page").then((m) => ({ default: m.PortfolioPage })),
  // 17 业务域插入页
  onchain: () => import("@/components/watch/onchain-page").then((m) => ({ default: m.OnchainPage })),
  "us-radar": () => import("@/components/watch/us-radar-page").then((m) => ({ default: m.UsRadarPage })),
  hyperliquid: () => import("@/components/watch/hyperliquid-page").then((m) => ({ default: m.HyperliquidPage })),
  regime: () => import("@/components/watch/regime-page").then((m) => ({ default: m.RegimePage })),
  cvd: () => import("@/components/watch/cvd-page").then((m) => ({ default: m.CvdPage })),
  heatmap: () => import("@/components/watch/heatmap-page").then((m) => ({ default: m.HeatmapPage, preloadData: m.preloadData })),
  liquidations: () => import("@/components/watch/liquidations-page").then((m) => ({ default: m.LiquidationsPage })),
  taiwan: () => import("@/components/watch/taiwan-page").then((m) => ({ default: m.TaiwanPage })),
  "ashare-deep": () => import("@/components/watch/ashare-deep-page").then((m) => ({ default: m.AshareDeepPage })),
  "ashare-board": () => import("@/components/watch/ashare-board-page").then((m) => ({ default: m.AshareBoardPage })),
  commodities: () => import("@/components/watch/commodities-page").then((m) => ({ default: m.CommoditiesPage })),
  serenity: () => import("@/components/research/serenity-page").then((m) => ({ default: m.SerenityPage })),
  qevolve: () => import("@/components/research/qevolve-page").then((m) => ({ default: m.QevolvePage })),
  factors: () => import("@/components/research/factors-page").then((m) => ({ default: m.FactorsPage })),
  backtests: () => import("@/components/trade/backtests-page").then((m) => ({ default: m.BacktestsPage })),
  "dex-trading": () => import("@/components/trade/dex-trading-page").then((m) => ({ default: m.DexTradingPage })),
  optimizations: () => import("@/components/trade/optimizations-page").then((m) => ({ default: m.OptimizationsPage })),
  notifications: () => import("@/components/system/notifications-page").then((m) => ({ default: m.NotificationsPage })),
  memory: () => import("@/components/system/memory-page").then((m) => ({ default: m.MemoryPage })),
  marketplace: () => import("@/components/system/marketplace-page").then((m) => ({ default: m.MarketplacePage })),
  "chat-sessions": () => import("@/components/system/chat-sessions-page").then((m) => ({ default: m.ChatSessionsPage })),
  optimizer: () => import("@/components/research/optimizer-page").then((m) => ({ default: m.OptimizerPage })),
  "ashare-backtest": () => import("@/components/research/ashare-backtest-page").then((m) => ({ default: m.AshareBacktestPage })),
  "ashare-fundamentals": () => import("@/components/research/ashare-fundamentals-page").then((m) => ({ default: m.AshareFundamentalsPage })),
  "ashare-style": () => import("@/components/research/ashare-style-page").then((m) => ({ default: m.AshareStylePage })),
  "ashare-portfolio": () => import("@/components/research/ashare-portfolio-page").then((m) => ({ default: m.AsharePortfolioPage })),
  "ashare-factor-report": () => import("@/components/research/ashare-factor-report-page").then((m) => ({ default: m.AshareFactorReportPage })),
  scanner: () => import("@/components/research/scanner-page").then((m) => ({ default: m.ScannerPage })),
  tuning: () => import("@/components/research/tuning-page").then((m) => ({ default: m.TuningPage })),
  risk: () => import("@/components/trade/risk-page").then((m) => ({ default: m.RiskPage })),
  // Round 6 — kingfisher 9 组件 + 5 专用 page (derivatives/* + options/*)
  "derivatives-footprint": () => import("@/components/derivatives/footprint-page").then((m) => ({ default: m.FootprintPage })),
  "derivatives-footprint-history": () => import("@/components/derivatives/footprint-page").then((m) => ({ default: m.FootprintHistoryPage })),
  "derivatives-vpvr": () => import("@/components/derivatives/vpvr-page").then((m) => ({ default: m.VpvrPage })),
  "derivatives-merged-book": () => import("@/components/derivatives/merged-book-page").then((m) => ({ default: m.MergedBookPage })),
  "options-summary": () => import("@/components/derivatives/options-page").then((m) => ({ default: m.OptionsSummaryPage })),
  "options-tickers": () => import("@/components/derivatives/options-page").then((m) => ({ default: m.OptionsTickersPage })),
  "options-instruments": () => import("@/components/derivatives/options-page").then((m) => ({ default: m.OptionsInstrumentsPage })),
  "options-tickers-instrument-name": () => import("@/components/derivatives/options-page").then((m) => ({ default: m.OptionsTickerDetailPage })),
  // 四叉新增业务页(前端 fork 无;无 preloadData,走 chunk 预载即可)
  favorites: () => import("@/components/watch/favorites-page").then((m) => ({ default: m.FavoritesPage })),
  "intelligence-alerts-rules": () => import("@/components/watch/intelligence-alerts-rules-page").then((m) => ({ default: m.IntelligenceAlertsRulesPage })),
  // r5 鲸鱼钱流 tab 视图 — WhaleTransferFeed/KolSignalFeed 注册为 whale tab 的两个视图组件
  "onchain-whales": () => import("@/components/tactical/whale-transfer-feed").then((m) => ({ default: m.WhaleTransferFeed })),
  "onchain-whales-kol": () => import("@/components/tactical/kol-signal-feed").then((m) => ({ default: m.KolSignalFeed })),
  "quant_tools": () => import("@/components/trade/quant-tools-page").then((m) => ({ default: m.QuantToolsPage })),
  "indicator_editor": () => import("@/components/research/indicator-editor-page").then((m) => ({ default: m.IndicatorEditorPage })),
  // 分析域 3 tab — rsch hub 吸收(US-209),slug 仍在 manifest(absorbedBy: rsch)
  analysis: () => import("@/components/research/analysis-page").then((m) => ({ default: m.AnalysisPage })),
  research_conclusions: () => import("@/components/research/research-conclusions-page").then((m) => ({ default: m.ResearchConclusionsPage })),
  analytics: () => import("@/components/research/analytics-page").then((m) => ({ default: m.AnalyticsPage })),
  // R1 — us-radar 专属组件 (us-radar 入口已在上方注册)
  "us-radar-event-signals": () => import("@/components/watch/us-radar-event-signals-page").then((m) => ({ default: m.EventSignalsPage })),
  "us-radar-market-overview": () => import("@/components/watch/us-radar-market-overview-page").then((m) => ({ default: m.MarketOverviewPage })),
  "us-radar-providers-capabilities": () => import("@/components/watch/us-radar-providers-capabilities-page").then((m) => ({ default: m.ProvidersCapabilitiesPage })),
  "us-radar-providers-readiness": () => import("@/components/watch/us-radar-providers-readiness-page").then((m) => ({ default: m.ProvidersReadinessPage })),
  "us-radar-research-agents-readiness": () => import("@/components/watch/us-radar-research-agents-readiness-page").then((m) => ({ default: m.ResearchAgentsReadinessPage })),
  "us-radar-scores-symbol-breakdown": () => import("@/components/watch/us-radar-scores-symbol-breakdown-page").then((m) => ({ default: m.ScoresSymbolBreakdownPage })),
  "us-radar-snapshot": () => import("@/components/watch/us-radar-snapshot-page").then((m) => ({ default: m.SnapshotPage })),
  "us-radar-symbols-symbol": () => import("@/components/watch/us-radar-symbols-symbol-page").then((m) => ({ default: m.SymbolsSymbolPage })),
  "us-radar-symbols-symbol-ai-console": () => import("@/components/watch/us-radar-symbols-symbol-ai-console-page").then((m) => ({ default: m.SymbolsSymbolAiConsolePage })),
  "us-radar-universe": () => import("@/components/watch/us-radar-universe-page").then((m) => ({ default: m.UniversePage })),
  // R2 — derivatives/markets 12 专属页 (fork/r2-derivatives)
  "derivatives-funding-rates": () => import("@/components/derivatives/funding-rates-page").then((m) => ({ default: m.FundingRatesPage })),
  "derivatives-funding-rates-all-exchanges": () => import("@/components/derivatives/funding-rates-all-exchanges-page").then((m) => ({ default: m.FundingRatesAllExchangesPage })),
  "derivatives-large-trades-recent": () => import("@/components/derivatives/large-trades-page").then((m) => ({ default: m.LargeTradesPage })),
  "derivatives-large-trades-stats": () => import("@/components/derivatives/large-trades-stats-page").then((m) => ({ default: m.LargeTradesStatsPage })),
  "derivatives-liquidation-stats": () => import("@/components/derivatives/liquidation-stats-page").then((m) => ({ default: m.LiquidationStatsPage })),
  "derivatives-liquidation-stats-all": () => import("@/components/derivatives/liquidation-stats-all-page").then((m) => ({ default: m.LiquidationStatsAllPage })),
  "derivatives-liquidations-recent": () => import("@/components/derivatives/liquidations-recent-page").then((m) => ({ default: m.LiquidationsRecentPage })),
  "derivatives-open-interest": () => import("@/components/derivatives/open-interest-page").then((m) => ({ default: m.OpenInterestPage })),
  markets: () => import("@/components/watch/markets-page").then((m) => ({ default: m.MarketsPage })),
  // R3 — paper 9 页专属组件(trade/paper-*.tsx)
  "paper-accounts": () => import("@/components/trade/paper-accounts").then((m) => ({ default: m.PaperAccountsPage })),
  "paper-accounts-account-id": () => import("@/components/trade/paper-accounts-account-id").then((m) => ({ default: m.PaperAccountDetailPage })),
  "paper-fills": () => import("@/components/trade/paper-fills").then((m) => ({ default: m.PaperFillsPage })),
  "paper-orders": () => import("@/components/trade/paper-orders").then((m) => ({ default: m.PaperOrdersPage })),
  "paper-pnl": () => import("@/components/trade/paper-pnl").then((m) => ({ default: m.PaperPnlPage })),
  "paper-positions": () => import("@/components/trade/paper-positions").then((m) => ({ default: m.PaperPositionsPage })),
  // R4 — Polymarket 专属组件 (fork/r4-polymarket) — 13 页(含 poly 已在上方)
  "polymarket-events": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketEventsPage })),
  "polymarket-events-event-id": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketEventDetailPage })),
  "polymarket-markets": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketMarketsPage })),
  "polymarket-markets-condition-id": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketMarketDetailPage })),
  "polymarket-orderbook-token-id": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketOrderbookPage })),
  "polymarket-price-token-id": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketPricePage })),
  "polymarket-crypto-predictions": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketCryptoPredictionsPage })),
  "polymarket-addresses-address-details": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketAddressDetailsPage })),
  "polymarket-addresses-alerts": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketAlertsPage })),
  "polymarket-addresses-alerts-matches": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketAlertMatchesPage })),
  "polymarket-addresses-pipeline": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketPipelinePage })),
  "polymarket-addresses-screen": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketScreenPage })),
  "polymarket-addresses-watchlist": () => import("@/components/watch/polymarket-pages").then((m) => ({ default: m.PolymarketWatchlistPage })),
  // R6 — fork/r6-onchain — DEX / onchain / hyperliquid 孤儿页 13 个专属组件
  "dex-pairs": () => import("@/components/watch/dex-pairs-page").then((m) => ({ default: m.DexPairsPage })),
  "dex-pair-chain-pair-address": () => import("@/components/watch/dex-pair-detail-page").then((m) => ({ default: m.DexPairDetailPage })),
  "dex-tokens-token-address": () => import("@/components/watch/dex-tokens-page").then((m) => ({ default: m.DexTokensPage })),
  "onchain-metrics": () => import("@/components/watch/onchain-metrics-page").then((m) => ({ default: m.OnchainMetricsPage })),
  "onchain-metrics-active-addresses": () => import("@/components/watch/onchain-metric-page").then((m) => ({ default: m.OnchainActiveAddressesPage })),
  "onchain-metrics-nvt-ratio": () => import("@/components/watch/onchain-metric-page").then((m) => ({ default: m.OnchainNvtRatioPage })),
  "onchain-metrics-sopr": () => import("@/components/watch/onchain-metric-page").then((m) => ({ default: m.OnchainSoprPage })),
  "hyperliquid-addresses-screen": () => import("@/components/watch/hyperliquid-addresses-screen-page").then((m) => ({ default: m.HyperliquidAddressesScreenPage })),
  "hyperliquid-addresses-pipeline": () => import("@/components/watch/hyperliquid-addresses-pipeline-page").then((m) => ({ default: m.HyperliquidAddressesPipelinePage })),
  "hyperliquid-addresses-address-details": () => import("@/components/watch/hyperliquid-address-details-page").then((m) => ({ default: m.HyperliquidAddressDetailsPage })),
  // R7 — intelligence/agent/system 专属组件 (严格 append-only)
  "intelligence-market-pulse-history": () => import("@/components/watch/intelligence-market-pulse-history-page").then((m) => ({ default: m.IntelligenceMarketPulseHistoryPage })),
  "intelligence-news-factor-pipeline": () => import("@/components/watch/intelligence-news-factor-pipeline-page").then((m) => ({ default: m.IntelligenceNewsFactorPipelinePage })),
  "intelligence-news-summary": () => import("@/components/watch/intelligence-news-summary-page").then((m) => ({ default: m.IntelligenceNewsSummaryPage })),
  "intelligence-news-token-signals": () => import("@/components/watch/intelligence-news-token-signals-page").then((m) => ({ default: m.IntelligenceNewsTokenSignalsPage })),
  "intelligence-social-confirmation": () => import("@/components/watch/intelligence-social-confirmation-page").then((m) => ({ default: m.IntelligenceSocialConfirmationPage })),
  "intelligence-social-sentiment": () => import("@/components/watch/intelligence-social-sentiment-page").then((m) => ({ default: m.IntelligenceSocialSentimentPage })),
  "intelligence-social-trending": () => import("@/components/watch/intelligence-social-trending-page").then((m) => ({ default: m.IntelligenceSocialTrendingPage })),
  "agent": () => import("@/components/agent/agent-chat-page").then((m) => ({ default: m.AgentChatPage })),
  "agent-trading-workspace": () => import("@/components/agent/agent-trading-workspace-page").then((m) => ({ default: m.AgentTradingWorkspacePage })),
  "mcp-tools": () => import("@/components/system/mcp-tools-page").then((m) => ({ default: m.McpToolsPage })),
};

const WIDGET_REGISTRY: Record<string, React.LazyExoticComponent<React.ComponentType>> = Object.fromEntries(
  Object.entries(WIDGET_LOADERS).map(([slug, loader]) => [slug, lazyComp(loader)]),
);

export function preloadWidget(slug: string) {
  WIDGET_LOADERS[slug]?.().catch(() => {});
}

// hover 预载:触发 widget chunk + 页面数据预载(preloadData 预热进 SWR global cache)
export async function preloadPage(slug: string) {
  const mod = await WIDGET_LOADERS[slug]?.().catch(() => null);
  mod?.preloadData?.();
}

// 空闲调度:优先 requestIdleCallback,兜底 setTimeout
const schedule = (fn: () => void) => {
  if (typeof window !== "undefined" && "requestIdleCallback" in window) {
    (window as unknown as { requestIdleCallback: (cb: () => void, o?: { timeout: number }) => void })
      .requestIdleCallback(fn, { timeout: 1500 });
  } else {
    setTimeout(fn, 300);
  }
};

// 带最小间隔的调度 —— 预载专用。
//
// requestIdleCallback 的语义是"浏览器闲下来就跑",而首帧渲染完浏览器立刻
// 就闲了,于是批次背靠背触发,间隔形同虚设:实测 69 个数据预载请求有 67 个
// 挤在首秒内(其余 2 个在第二秒)。预载的定位是**后台**优化,不该和用户的
// 真实点击抢同一份后端容量(单 worker 事件循环,行情轮询已在共用)。
// 故批次之间强制一个下限间隔,把预载摊到几十秒,让它退回背景。
const PRELOAD_MIN_GAP_MS = 1200;
const scheduleSpaced = (fn: () => void) => {
  if (typeof window === "undefined") return;
  window.setTimeout(fn, PRELOAD_MIN_GAP_MS);
};

// 全量 chunk 预载:空闲时小批 import 全部 widget chunk,消除首次点击某页时的编译/加载等待。
// hover 只覆盖鼠标路径,直接点击/快捷键不触发,故全量预载让所有页面 chunk 提前就绪。
const PRELOAD_BATCH = 3;
export function preloadAllChunks() {
  if (typeof window === "undefined") return;
  const slugs = Object.keys(WIDGET_LOADERS);
  let i = 0;
  const step = () => {
    const end = Math.min(i + PRELOAD_BATCH, slugs.length);
    for (; i < end; i++) WIDGET_LOADERS[slugs[i]]?.().catch(() => {});
    if (i < slugs.length) schedule(step);
  };
  schedule(step);
}

// ===== 数据全量预载 =====
// SWR key 通常 === 请求 path(useApi 以 path 为 key);少数页自定义 key(onchain/liquidations),
// 用 {key, path} 同时复刻。symbol 占位由当前 market store 解析(与各页 useContextStore 同源):
// {sym}=bare("BTC")、{symFullEnc}="BTC%2FUSDT"、{apiSymEnc}="BTC-USDT-SWAP"
// 依赖运行时参数(accountId/用户输入/sessionId/window)的页不在注册表内,数据由挂载时自行拉取。
type DataEntry = string | { key: string; path: string };

const DATA_PRELOAD: Record<string, DataEntry[]> = {
  // ===== watch =====
  agg: ["/api/v1/agg-orderbook/current?symbol={symFullEnc}&depth=25"],
  tac: ["/api/v1/derivatives/funding-rates"],
  sig: ["/api/v1/signals/current"],
  vchart: ["/api/v1/vchart?symbol={apiSymEnc}&interval={interval}"],
  poly: ["/api/v1/polymarket/events?limit=10"],
  beta: ["/api/v1/beta-play"],
  kfi: ["/api/v1/kf-index/list"],
  vol_surface: ["/api/v1/options/vol-surface"],
  smart_money: ["/api/v1/onchain/smart-money"],
  onchain: [{ key: "onchain-{sym}-0", path: "/api/v1/onchain/metrics?symbol={sym}" }],
  "us-radar": ["/api/v1/us-radar/snapshot"],
  hyperliquid: ["/api/v1/hyperliquid/addresses/screen"],
  regime: ["/api/v1/regime?symbol={sym}"],
  cvd: ["/api/v1/cvd/current?symbol={sym}"],
  liquidations: [{ key: "liq-{sym}-h4-0", path: "/api/v1/liquidations/heatmap?symbol={sym}&timeframe=h4" }],
  // ===== research =====
  pa: ["/api/v1/decision-workflow/trees"],
  experiments: ["/api/v1/experiments?limit=100"],
  rsch: ["/api/v1/strategies/factory-templates"],
  strat: [
    "/api/v1/strategies/factory-templates",
    "/api/v1/alpha/saved-strategies",
    "/api/v1/research/tuning/results",
    "/api/v1/strategies/deployments/list",
  ],
  alpha_mine: ["/api/v1/alpha/formulas", "/api/v1/alpha/saved-strategies"],
  qevolve: ["/api/v1/qevolve/current"],
  mem: ["/api/v1/research/graph/memory"],
  optimizer: ["/api/v1/strategies/factory-templates"],
  scanner: ["/api/v1/strategies/factory-templates"],
  tuning: ["/api/v1/research/tuning/results"],
  // ===== trade =====
  grid: ["/api/v1/grid/state"],
  tof: ["/api/v1/tof/current?symbol={symFullEnc}&cvd_interval={cvdInterval}"],
  paper: ["/api/v1/paper/positions?account_id=paper-default", "/api/v1/paper/orders?account_id=paper-default"],
  dash: ["/api/v1/dashboard", "/api/v1/live/status"],
  backtests: ["/api/v1/backtests"],
  "dex-trading": ["/api/v1/dex-trading/portfolio"],
  optimizations: ["/api/v1/optimizations"],
  risk: ["/api/v1/dashboard"],
  // R3 paper 子页 — 默认账户 paper-default 数据预热
  "paper-accounts": ["/api/v1/paper/accounts"],
  "paper-accounts-account-id": ["/api/v1/paper/accounts", "/api/v1/paper/accounts/paper-default"],
  "paper-fills": ["/api/v1/paper/fills?account_id=paper-default"],
  "paper-orders": ["/api/v1/paper/orders?account_id=paper-default"],
  "paper-pnl": ["/api/v1/paper/pnl?account_id=paper-default"],
  "paper-positions": ["/api/v1/paper/positions?account_id=paper-default"],
  // ===== system =====
  skill: ["/api/v1/skills"],
  mcp: ["/api/v1/mcp/servers"],
  set: ["/api/v1/settings"],
  notifications: ["/api/v1/notifications/channels"],
  memory: ["/api/v1/memory/market-patterns"],
  marketplace: ["/api/v1/marketplace/listings"],
  "chat-sessions": ["/api/v1/chat/sessions"],
  // R6 fork/r6-onchain — DEX / onchain / hyperliquid 孤儿页
  // dex 页用裸 useSWR 自定义 key,预载复刻默认 key(chain=ethereum)
  "dex-pairs": [{ key: "dex-pairs-orphan-ethereum", path: "/api/v1/dex/pairs?chain=ethereum&limit=50" }],
  // dex-search 等依赖用户输入,无稳定预载 key,跳过
  "onchain-metrics": [{ key: "onchain-metrics-{sym}-0", path: "/api/v1/onchain/metrics?symbol={sym}" }],
  "onchain-metrics-active-addresses": [{ key: "onchain-active-addresses-{sym}-0", path: "/api/v1/onchain/metrics/active-addresses?symbol={sym}" }],
  "onchain-metrics-nvt-ratio": [{ key: "onchain-nvt-ratio-{sym}-0", path: "/api/v1/onchain/metrics/nvt-ratio?symbol={sym}" }],
  "onchain-metrics-sopr": [{ key: "onchain-sopr-{sym}-0", path: "/api/v1/onchain/metrics/sopr?symbol={sym}" }],
  "hyperliquid-addresses-screen": [{ key: "hl-screen-all-30", path: "/api/v1/hyperliquid/addresses/screen?classification=all&window_days=30&limit=200" }],
  "hyperliquid-addresses-pipeline": [{ key: "hl-pipeline", path: "/api/v1/hyperliquid/addresses/pipeline" }],
};

function dataCtx() {
  const st = useMarketStore.getState();
  const raw = st.pageFilter.symbol || "BTC";
  const base = raw.split("/")[0];
  const tf = st.pageFilter.tf || "1h";
  return {
    sym: base,
    symFullEnc: encodeURIComponent(`${base}/USDT`),
    apiSymEnc: encodeURIComponent(`${base}-USDT-SWAP`),
    // 与 tof-page.tsx 的 tf→cvd_interval 映射保持一致
    cvdInterval: tf === "1h" ? "5m" : tf === "4h" ? "1h" : "4h",
    // 与 vchart-page.tsx 的 tf→interval 映射保持一致
    interval: tf === "1h" ? "1h" : tf === "4h" ? "4h" : tf === "1d" ? "1d" : "15m",
  };
}

function firePreload(entry: DataEntry, ctx: ReturnType<typeof dataCtx>) {
  const fill = (s: string) =>
    s
      .replaceAll("{sym}", ctx.sym)
      .replaceAll("{symFullEnc}", ctx.symFullEnc)
      .replaceAll("{apiSymEnc}", ctx.apiSymEnc)
      .replaceAll("{cvdInterval}", ctx.cvdInterval)
      .replaceAll("{interval}", ctx.interval);
  if (typeof entry === "string") {
    const url = fill(entry);
    preload(url, fetcher);
  } else {
    const key = fill(entry.key);
    const url = fill(entry.path);
    preload(key, () => fetcher(url));
  }
}

// 有 preloadData export 的 hot-path 页(无 DATA_PRELOAD entry,须动态 import 拿 preloadData)。
// 其余 slug 无 preloadData,chunk 已由 preloadAllChunks 提前 import → preloadSlugData 再 import 纯属空转。
const PRELOAD_FALLBACK_SLUGS = new Set(["liqmap", "liqratio", "gex", "kf", "heatmap"]);

// 单页数据预载:registry 命中直接 preload;否则仅对已知带 preloadData 的 hot-path 页做 fallback import
async function preloadSlugData(slug: string) {
  const entries = DATA_PRELOAD[slug];
  if (entries) {
    const ctx = dataCtx();
    for (const e of entries) firePreload(e, ctx);
    return;
  }
  if (!PRELOAD_FALLBACK_SLUGS.has(slug)) return;
  const mod = await WIDGET_LOADERS[slug]?.().catch(() => null);
  mod?.preloadData?.();
}

// 全量数据预载:分批 preload 全部页面数据端点,首访页面命中 SWR cache 立即渲染,
// 消除"切页后 skeleton→数据跳变"闪眼(缓存数据先渲染,后台 revalidate 刷新)
//
// 走 scheduleSpaced 而非 schedule:数据预载会真的打后端,必须限速(见其注释)。
// chunk 预载(localhost 模块拉取)仍用 schedule —— 它不占后端容量。
const DATA_BATCH = 2;
export function preloadAllData() {
  if (typeof window === "undefined") return;
  const slugs = Object.keys(WIDGET_LOADERS);
  let i = 0;
  const step = () => {
    const end = Math.min(i + DATA_BATCH, slugs.length);
    for (; i < end; i++) preloadSlugData(slugs[i]);
    if (i < slugs.length) scheduleSpaced(step);
  };
  scheduleSpaced(step);
}

// 全量预载入口:挂载后调用。chunk 先行,数据随之,idle 分批避免卡首帧
export function preloadAll() {
  preloadAllChunks();
  preloadAllData();
}

function WidgetFallback() {
  return <SkeletonGrid cols={3} rows={3} />;
}

// useSearchParams 需要 Suspense 边界(Next.js CSR bailout 约束),外层包一层
function DetailPageWithSuspense({ meta }: { meta: PageMeta }) {
  return (
    <Suspense fallback={<WidgetFallback />}>
      <DetailPageView meta={meta} />
    </Suspense>
  );
}

export function PageRenderer({ meta }: { meta: PageMeta | undefined }) {
  if (!meta) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-[color:var(--color-text-muted)]">
        页面未找到
      </div>
    );
  }
  const LazyComponent = WIDGET_REGISTRY[meta.slug];
  if (LazyComponent) {
    return (
      <Suspense fallback={<WidgetFallback />}>
        <LazyComponent />
      </Suspense>
    );
  }

  // 动态详情页(非 symbol 占位符)优先走通用详情视图:?task_id= 直达,否则提示从列表进入
  if (isDynamicPage(meta)) {
    return <DetailPageWithSuspense meta={meta} />;
  }

  switch (meta.renderMode) {
    case "symbol":
      return <SymbolPanelPage meta={meta} />;
    case "research":
      return <ResearchPanelPage meta={meta} />;
    case "list":
      return <DataTablePage meta={meta} />;
    case "chat":
      return <ChatPanelPage meta={meta} />;
    default:
      return <DataTablePage meta={meta} />;
  }
}
