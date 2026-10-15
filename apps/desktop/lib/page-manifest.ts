export type Scene = "watch" | "research" | "trade" | "system" | "workspace";
export type RenderMode = "symbol" | "research" | "list" | "chat";


export interface PageMeta {
  slug: string;
  title: string;
  scene: Scene;
  category: string;
  shortcut?: string;
  renderMode: RenderMode;
  removable: boolean;
  origin: "design" | "backend-insert";
  /** 已并入某工作台 hub(值为 hub slug):侧边栏/⌘K 不再直达,渲染能力保留(块身份)。
   *  用于有真组件的页(批次3 起):manifest 条目保留 + absorbedBy 标记;
   *  无组件的通用块直接删条目(渲染靠 BlockComposer 手工 meta 兜底)。 */
  absorbedBy?: string;
  /** 五命运落标(判定表 r5,US-208 起 watch 场景全量标注):
   *  candidate=候审组(嫌疑犯①,watch"更多"只余此组) / tool=④专业工具(工具室分组) /
   *  keep=③对象类(drilldown/动态路由,⌘K 可达)。absorbedBy 与 fate 互斥。 */
  fate?: "candidate" | "tool" | "keep" | "detail";
  rest?: string;
  ws?: "markets" | "intelligence" | "trading" | "paper" | "polymarket" | "agent" | "strategy" | "research" | "none";
}

// 设计稿 base 22 页 + extended 5 页 = 27 页（spec 来源：_temp/final/demo-aa-scene-layout.html 第 3993-4036 行）
// BACKEND_AUTO_PAGES (240 页, 自动生成) 已移除 — 无前端组件、标题机器翻译、全堆一个 "auto" 分类
export const DESIGN_PAGES: PageMeta[] = [
  // watch 盯盘 13 页
  { slug: "kf", title: "主力监控", scene: "watch", category: "主力", shortcut: "⌘1", renderMode: "symbol", removable: false, origin: "design", rest: "/api/v1/markets/{symbol}/kline", ws: "markets" },
  { slug: "kfi", title: "主力指数", scene: "watch", category: "主力", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/intelligence/whale-flow", ws: "markets" },
  { slug: "mk", title: "行情总览", scene: "watch", category: "主力", shortcut: "⌘2", renderMode: "symbol", removable: true, origin: "design", ws: "markets" },
  { slug: "liqmap", title: "爆仓地图", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/liqmap/current", ws: "intelligence", absorbedBy: "derivatives-hub" },
  { slug: "liquidation-hub", title: "爆仓工作台", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/liqmap/current", ws: "intelligence", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-hub", title: "衍生品面", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/derivatives/funding-rates", ws: "none" },
  { slug: "cvd-hub", title: "CVD 工作台", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/cvd/all", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "gex-hub", title: "GEX 工作台", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/gex/current", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "liqratio", title: "多空比", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", ws: "intelligence", absorbedBy: "derivatives-hub" },
  { slug: "gex", title: "Gamma 暴露", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/gex", ws: "intelligence", absorbedBy: "derivatives-hub" },
  { slug: "agg", title: "聚合盘口", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", ws: "markets", absorbedBy: "mk" },
  { slug: "tac", title: "资金费率", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", ws: "intelligence" },
  { slug: "sig", title: "信号中心", scene: "watch", category: "信号", renderMode: "symbol", removable: true, origin: "design", ws: "intelligence" },
  { slug: "vchart", title: "专业图表", scene: "watch", category: "信号", renderMode: "symbol", removable: true, origin: "design", ws: "markets", fate: "tool" },
  { slug: "poly", title: "预测市场", scene: "watch", category: "其他", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/polymarket", ws: "polymarket", fate: "candidate" },
  { slug: "dex", title: "链上 DEX", scene: "watch", category: "其他", renderMode: "symbol", removable: true, origin: "design", ws: "intelligence" },
      { slug: "onchain-hub", title: "链上情报", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/onchain", ws: "intelligence" },
  { slug: "quant_tools", title: "量化工具", scene: "watch", category: "工具", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/grid", ws: "trading", fate: "tool" },
  // research 研策 4 页
  { slug: "rsch", title: "研究中心", scene: "research", category: "研究", shortcut: "⌘4", renderMode: "research", removable: false, origin: "design", rest: "/api/v1/research", ws: "none" },
  { slug: "strat", title: "策略工厂", scene: "research", category: "策略", shortcut: "⌘3", renderMode: "research", removable: false, origin: "design", rest: "/api/v1/strategies/factory-templates", ws: "none" },
  // trade 执行 5 页
  { slug: "pos", title: "我的持仓", scene: "trade", category: "交易", shortcut: "⌘5", renderMode: "list", removable: false, origin: "design", rest: "/api/v1/trading/positions", ws: "trading" },
  { slug: "paper", title: "模拟交易", scene: "trade", category: "交易", renderMode: "list", removable: true, origin: "design", rest: "/api/v1/paper", ws: "paper" },
  { slug: "trd", title: "实盘下单", scene: "trade", category: "交易", renderMode: "list", removable: true, origin: "design", rest: "/api/v1/trading/orders", ws: "trading" },
  { slug: "grid", title: "网格策略", scene: "trade", category: "策略", renderMode: "list", removable: true, origin: "design", rest: "/api/v1/grid", ws: "trading" },
  { slug: "tof", title: "交易运维", scene: "trade", category: "策略", renderMode: "list", removable: true, origin: "design", rest: "/api/v1/tof", ws: "none" },
  // system 系统 4 页
  { slug: "dash", title: "总览", scene: "system", category: "系统", renderMode: "chat", removable: false, origin: "design", rest: "/api/v1/dashboard", ws: "none" },
  { slug: "skill", title: "技能市场", scene: "system", category: "系统", renderMode: "chat", removable: true, origin: "design", rest: "/api/v1/skills", ws: "none" },
  { slug: "mcp", title: "MCP 管理", scene: "system", category: "系统", renderMode: "chat", removable: true, origin: "design", rest: "/api/v1/mcp", ws: "none" },
  { slug: "set", title: "设置", scene: "system", category: "系统", renderMode: "chat", removable: false, origin: "design", rest: "/api/v1/settings", ws: "none" },
];

// 设计稿 extended 5 页
export const DESIGN_PAGES_EXTENDED: PageMeta[] = [
  { slug: "vol_surface", title: "波动率曲面", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/options/vol-surface", ws: "intelligence", absorbedBy: "options-hub"},
  { slug: "smart_money", title: "聪明钱追踪", scene: "watch", category: "其他", renderMode: "symbol", removable: true, origin: "design", rest: "/api/v1/onchain/smart-money", ws: "intelligence", absorbedBy: "sig"},
  { slug: "alpha_mine", title: "Alpha 挖矿", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "design", rest: "/api/v1/alpha/mining", ws: "none" },
  { slug: "portfolio", title: "组合分析", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "design", rest: "/api/v1/portfolio", ws: "none", absorbedBy: "pos" },
  { slug: "indicator_editor", title: "自定义指标编辑器", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "design", rest: "/api/v1/backtests", ws: "none", fate: "tool" },
  { slug: "research_conclusions", title: "因子研究报告", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "design", rest: undefined, ws: "none", absorbedBy: "rsch" },
  { slug: "analytics", title: "量化分析", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "design", rest: undefined, ws: "none", absorbedBy: "rsch" },
  { slug: "live_log", title: "实盘日志", scene: "trade", category: "交易", renderMode: "list", removable: true, origin: "design", rest: "/api/v1/trading/live-log", ws: "none" },
];

// 后端业务域插入 ~18 页 — 设计稿没有但后端有的端点，按场景归位
export const BACKEND_INSERT_PAGES: PageMeta[] = [
  // watch 场景插入（onchain / us-radar / hyperliquid / regime / cvd / heatmap / liquidations）
  { slug: "onchain", title: "链上指标", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/onchain", ws: "none", absorbedBy: "onchain-hub" },
  { slug: "onchain-whales", title: "鲸鱼钱流", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/onchain/whale-transfers", ws: "intelligence", absorbedBy: "onchain-hub" },
  { slug: "us-radar", title: "US Radar", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar", ws: "none" },
  { slug: "hyperliquid", title: "HL 筛选", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/hyperliquid", ws: "none", fate: "candidate" },
  { slug: "regime", title: "市场体制", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/regime", ws: "none", absorbedBy: "mk" },
  { slug: "cvd", title: "CVD", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/cvd", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "heatmap", title: "热力图", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/heatmap", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "liquidations", title: "爆仓", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/liquidations", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "taiwan", title: "台股", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/taiwan", ws: "none", absorbedBy: "mk" },
  { slug: "ashare-deep", title: "A股深度", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/ashare", ws: "none", absorbedBy: "mk" },
  { slug: "ashare-board", title: "A股看盘", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "design", rest: "/api/v1/ashare", ws: "none" },
  { slug: "commodities", title: "大宗外汇", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/commodities-fx", ws: "none", absorbedBy: "mk" },
  { slug: "intelligence-alerts-rules", title: "情报预警规则", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/alerts/rules", ws: "intelligence", fate: "tool" },
  { slug: "derivatives-footprint", title: "逐笔足迹", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/footprint", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-footprint-history", title: "逐笔足迹历史", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/footprint/history", ws: "none", absorbedBy: "derivatives-hub" },
    { slug: "derivatives-merged-book", title: "合并盘口", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/merged-book", ws: "none", absorbedBy: "mk" },
  { slug: "derivatives-vpvr", title: "VPVR", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/vpvr", ws: "none", absorbedBy: "mk" },
    { slug: "options-hub", title: "期权工作台", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "design" },
{ slug: "options-summary", title: "期权总览", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/options/summary", ws: "none", absorbedBy: "options-hub" },
  { slug: "options-tickers", title: "期权报价", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/options/tickers", ws: "none", absorbedBy: "options-hub"},
  { slug: "options-instruments", title: "期权合约", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/options/instruments", ws: "none", absorbedBy: "options-hub"},
  { slug: "options-tickers-instrument-name", title: "期权合约详情", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/options/tickers", ws: "none", fate: "keep" },
  // research 场景插入（serenity / qevolve / factors）
  { slug: "serenity", title: "风险扫描", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/serenity", ws: "none" },
  { slug: "qevolve", title: "Q-Evolve", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/qevolve", ws: "none" },
  { slug: "factors", title: "因子库", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/research/factors", ws: "none" },
  { slug: "optimizer", title: "策略优化器", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "design", rest: "/api/v1/backtests", ws: "none" },
  { slug: "ashare-backtest", title: "A股回测", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/ashare/backtest", ws: "none" },
  { slug: "ashare-fundamentals", title: "A股财报PIT", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/ashare/fundamentals-backfill", ws: "none" },
  { slug: "ashare-style", title: "A股风格监测", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/ashare/style-report", ws: "none" },
  { slug: "ashare-portfolio", title: "A股组合回测", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/ashare/portfolio-backtest", ws: "none" },
  { slug: "ashare-factor-report", title: "A股因子IC报告", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/ashare/factor-report", ws: "none" },
  { slug: "scanner", title: "批量扫描", scene: "research", category: "策略", renderMode: "research", removable: true, origin: "design", rest: "/api/v1/backtests", ws: "none" },
  { slug: "tuning", title: "参数调优", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "design", rest: "/api/v1/research/tuning/tasks", ws: "none" },
  { slug: "analysis", title: "行情分析", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "backend-insert", rest: undefined, ws: "none", absorbedBy: "rsch" },
  { slug: "pa", title: "决策工作流", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/decision-workflow/trees", ws: "none" },
  { slug: "experiments", title: "实验登记", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/experiments", ws: "none" },
  { slug: "experiment-evidence", title: "证据报告", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/experiments/{id}/evidence-report", ws: "none" },
  { slug: "episodes", title: "经验回放", scene: "research", category: "研究", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/episodes", ws: "none" },
  // trade 场景插入（backtests / dex-trading / optimizations）
  { slug: "backtests", title: "回测中心", scene: "trade", category: "策略", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/backtests", ws: "none" },
  { slug: "dex-trading", title: "DEX 交易", scene: "trade", category: "交易", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/dex-trading", ws: "none" },
  { slug: "optimizations", title: "策略优化", scene: "trade", category: "策略", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/optimizations", ws: "none" },
  // system 场景插入（notifications / memory / marketplace / agent / chat-sessions / admin）
  { slug: "notifications", title: "通知中心", scene: "system", category: "系统", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/notifications", ws: "none" },
  { slug: "memory", title: "市场记忆", scene: "system", category: "系统", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/memory", ws: "none" },
  { slug: "marketplace", title: "策略市场", scene: "system", category: "系统", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/marketplace", ws: "none" },
  { slug: "agent", title: "Agent Chat", scene: "system", category: "系统", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/agent", ws: "agent" },
  { slug: "chat-sessions", title: "Agent 会话", scene: "system", category: "系统", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/chat/sessions", ws: "none" },
  // ── 孤儿页地基收敛注册(2026-08-19) 147 页:95 静态 + 52 动态(13 symbol 可用)──
  // watch 盯盘 场景
    { slug: "ashare-factors-symbol", title: "A股因子", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/ashare/factors/{symbol}", ws: "none", fate: "keep" },
  { slug: "ashare-regime", title: "A股体制", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/ashare/regime", ws: "none", absorbedBy: "mk" },
  { slug: "derivatives-funding-rates", title: "资金费率", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/funding-rates", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-funding-rates-all-exchanges", title: "全交易所资金费率", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/funding-rates/all-exchanges", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-large-trades-recent", title: "大宗成交", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/large-trades/recent", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-large-trades-stats", title: "大宗成交统计", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/large-trades/stats", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-liquidation-stats", title: "爆仓统计", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/liquidation-stats", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-liquidation-stats-all", title: "全市场爆仓统计", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/liquidation-stats/all", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-liquidations-recent", title: "近期爆仓", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/liquidations/recent", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "derivatives-open-interest", title: "未平仓量", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/derivatives/open-interest", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "dex-pair-chain-pair-address", title: "DEX 对链", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/dex/pair/{chain}/{pair_address}", ws: "none", fate: "keep" },
  { slug: "dex-pairs", title: "DEX 交易对", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/dex/pairs", ws: "none", absorbedBy: "dex" },
    { slug: "dex-tokens-token-address", title: "DEX 代币", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/dex/tokens/{token_address}", ws: "none", fate: "keep" },
    { slug: "hyperliquid-addresses-address-details", title: "HL 地址详情", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/hyperliquid/addresses/{address}/details", ws: "none", fate: "keep" },
  { slug: "hyperliquid-addresses-pipeline", title: "HL 流水线", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/hyperliquid/addresses/pipeline", ws: "none", absorbedBy: "hyperliquid" },
  { slug: "hyperliquid-addresses-screen", title: "HL 筛选", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/hyperliquid/addresses/screen", ws: "none", absorbedBy: "hyperliquid" },
  { slug: "intelligence-market-pulse-history", title: "市场脉搏历史", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/market-pulse/history", ws: "intelligence", absorbedBy: "sig"},
  { slug: "intelligence-news-factor-pipeline", title: "新闻因子流水线", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/news/factor-pipeline", ws: "intelligence", absorbedBy: "sig"},
  { slug: "intelligence-news-summary", title: "新闻摘要", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/news/summary", ws: "intelligence", absorbedBy: "sig"},
  { slug: "intelligence-news-token-signals", title: "代币信号", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/news/token-signals", ws: "intelligence", absorbedBy: "sig"},
  { slug: "intelligence-social-confirmation", title: "社交确认", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/social/confirmation", ws: "intelligence", absorbedBy: "sig"},
  { slug: "intelligence-social-sentiment", title: "社交情绪", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/social/sentiment", ws: "intelligence", absorbedBy: "sig"},
  { slug: "intelligence-social-trending", title: "社交热点", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/social/trending", ws: "intelligence", absorbedBy: "sig"},
  { slug: "intelligence-whale-flow", title: "鲸鱼资金流", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/intelligence/whale-flow", ws: "intelligence", absorbedBy: "sig" },
  { slug: "kf-index-compute", title: "指数计算", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/kf-index/compute", ws: "none", absorbedBy: "kfi" },
  { slug: "kf-index-compute-all", title: "全指数计算", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/kf-index/compute-all", ws: "none", absorbedBy: "kfi" },
  { slug: "kf-index-list", title: "指数列表", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/kf-index/list", ws: "none", absorbedBy: "kfi" },
  { slug: "liqmap-all-tiers", title: "爆仓全层级", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/liqmap/all-tiers", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "liqratio-all-tiers", title: "多空全层级", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/liqratio/all-tiers", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "liquidations-heatmap", title: "爆仓热力", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/liquidations/heatmap", ws: "none", absorbedBy: "derivatives-hub" },
  { slug: "markets", title: "市场", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/markets", ws: "none", absorbedBy: "mk" },
        { slug: "polymarket-addresses-address-details", title: "PM 地址详情", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/addresses/{address}/details", ws: "polymarket", fate: "keep" },
  { slug: "polymarket-addresses-alerts", title: "PM 预警", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/addresses/alerts", ws: "polymarket", absorbedBy: "poly" },
  { slug: "polymarket-addresses-alerts-matches", title: "PM 预警匹配", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/addresses/alerts/matches", ws: "polymarket", absorbedBy: "poly" },
  { slug: "polymarket-addresses-pipeline", title: "PM 流水线", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/addresses/pipeline", ws: "polymarket", absorbedBy: "poly" },
  { slug: "polymarket-addresses-screen", title: "PM 筛选", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/addresses/screen", ws: "polymarket", absorbedBy: "poly" },
  { slug: "polymarket-addresses-watchlist", title: "PM 关注", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/addresses/watchlist", ws: "polymarket", absorbedBy: "poly" },
  { slug: "polymarket-crypto-predictions", title: "PM 加密预测", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/crypto-predictions", ws: "polymarket", absorbedBy: "poly" },
  { slug: "polymarket-events", title: "PM 事件", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/events", ws: "polymarket", absorbedBy: "poly" },
  { slug: "polymarket-events-event-id", title: "PM 事件详情", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/events/{event_id}", ws: "polymarket", fate: "keep" },
  { slug: "polymarket-markets", title: "PM 市场", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/markets", ws: "polymarket", absorbedBy: "poly" },
  { slug: "polymarket-markets-condition-id", title: "PM 市场详情", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/markets/{condition_id}", ws: "polymarket", fate: "keep" },
  { slug: "polymarket-orderbook-token-id", title: "PM 盘口", scene: "watch", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/polymarket/orderbook/{token_id}", ws: "polymarket", fate: "keep" },
    { slug: "signals-current", title: "当前信号", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/signals/current", ws: "intelligence", absorbedBy: "sig" },
  { slug: "signals-dashboard", title: "信号面板", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/signals/dashboard", ws: "intelligence", absorbedBy: "sig" },
  { slug: "stock-analysis-symbol", title: "股票分析", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/stock/analysis/{symbol}", ws: "none", fate: "keep" },
  { slug: "stock-score-symbol", title: "股票评分", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/stock/score/{symbol}", ws: "none", fate: "keep" },
  { slug: "us-radar-event-signals", title: "事件信号", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/event-signals", ws: "intelligence", absorbedBy: "us-radar"},
  { slug: "us-radar-market-overview", title: "市场概览", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/market/overview", ws: "none", absorbedBy: "us-radar"},
  { slug: "us-radar-providers-capabilities", title: "供应商能力", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/providers/capabilities", ws: "none", absorbedBy: "us-radar"},
  { slug: "us-radar-providers-readiness", title: "供应商就绪", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/providers/readiness", ws: "none", absorbedBy: "us-radar"},
  { slug: "us-radar-research-agents-readiness", title: "研究代理就绪", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/research-agents/readiness", ws: "agent", absorbedBy: "us-radar"},
  { slug: "us-radar-scores-symbol-breakdown", title: "评分分解", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/scores/{symbol}/breakdown", ws: "none", fate: "keep" },
  { slug: "us-radar-snapshot", title: "快照", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/snapshot", ws: "none", absorbedBy: "us-radar" },
  { slug: "us-radar-symbols-symbol", title: "符号详情", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/symbols/{symbol}", ws: "none", fate: "keep" },
  { slug: "us-radar-symbols-symbol-ai-console", title: "AI 控制台", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/symbols/{symbol}/ai-console", ws: "none", fate: "keep" },
  { slug: "us-radar-universe", title: "US 宇宙", scene: "watch", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/us-radar/universe", ws: "none", absorbedBy: "us-radar"},
  // research 研策 场景
  { slug: "alpha-formulas", title: "Alpha 公式", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/alpha/formulas", ws: "none" },
  // alpha-formulas-formula-id 已删(2026-10-01, detail-cleanup):薄壳路由 → DataTablePage 只渲染
  // DynamicPlaceholder(不解析 ?id=),点进去必落「请从列表进入」。公式详情已由挖矿页 Dialog inline 承载
  // (alpha-mining-panel.tsx openFormulaDetail → getFormulaDetail,全字段含 training_history)。
  // 后端 GET /alpha/formulas/{formula_id} 保留,仍由该 Dialog 消费。
  { slug: "alpha-saved-strategies", title: "已存策略", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/alpha/saved-strategies", ws: "none" },
  { slug: "alpha-vocab", title: "Alpha 词表", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/alpha/vocab", ws: "none" },
  // qevolve-evolve-id 已删(2026-10-01, detail-cleanup):母页 qevolve-page.tsx 只渲染单个「当前进化」
  // 概览(46 行,无多记录列表 → 无行可钻取),该详情页无任何入口且同为 DynamicPlaceholder 死占位。
  // 后端 GET /qevolve/{id} 保留(功能端点,非文档)；将来若做进化历史列表,再从列表 drill-down 重建。
  { slug: "research-tuning-results", title: "调参结果", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/research/tuning/results", ws: "none" },
  // research-tuning-results-file-name 已删(2026-10-01, detail-cleanup):同上死占位。调参结果详情已由
  // tuning-results-panel.tsx 行展开 inline 承载(best_params + 9 项回测指标)。后端端点保留仍由该面板消费。
  { slug: "missions", title: "AI 组织 · Mission", scene: "research", category: "研究", renderMode: "research", removable: false, origin: "design", ws: "none" },
  { slug: "questions", title: "AI 组织 · Question", scene: "research", category: "研究", renderMode: "research", removable: false, origin: "design", ws: "none" },
  { slug: "desk", title: "变化 · 值班台", scene: "research", category: "研究", renderMode: "research", removable: false, origin: "design", ws: "none" },
  { slug: "mem", title: "AI 组织 · 记忆", scene: "research", category: "研究", renderMode: "research", removable: false, origin: "design", rest: "/api/v1/research/graph/memory", ws: "none" },
  { slug: "today", title: "TODAY · 值班室", scene: "research", category: "研究", renderMode: "research", removable: false, origin: "design", ws: "none" },
  { slug: "strategy-cockpit", title: "策略驾驶舱", scene: "research", category: "研究", renderMode: "research", removable: false, origin: "design", ws: "none" },
  { slug: "graph", title: "研究图谱", scene: "research", category: "研究", renderMode: "research", removable: false, origin: "design", rest: "/api/v1/research/graph/view", ws: "none" },
  { slug: "research-tuning-tasks", title: "调参任务", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/research/tuning/tasks", ws: "none" },
  { slug: "research-tuning-tasks-task-id", title: "调参任务详情", scene: "research", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/research/tuning/tasks/{task_id}", ws: "none" , fate: "detail" },
  // serenity-deep/history-symbol 已删(2026-10-02, detail-batches):薄壳路由 → DynamicPlaceholder
  // 不解析 ?symbol=。能力由 serenity-panel.tsx:116/132 inline fetch 渲染完整报告,独立页纯冗余。
  // 后端 GET /serenity/deep/{symbol}、/history/{symbol} 保留,仍由 panel 消费。
  { slug: "serenity-factors", title: "风险因子", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/serenity/factors", ws: "none" },
  { slug: "serenity-score-symbol", title: "风险评分", scene: "research", category: "数据", renderMode: "symbol", removable: true, origin: "backend-insert", rest: "/api/v1/serenity/score/{symbol}", ws: "none" , fate: "detail" },
  { slug: "serenity-scores", title: "风险评分列表", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/serenity/scores", ws: "none" },
  { slug: "strategies", title: "策略列表", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/strategies", ws: "none" },
  { slug: "strategies-deployments-list", title: "策略部署", scene: "research", category: "数据", renderMode: "research", removable: true, origin: "backend-insert", rest: "/api/v1/strategies/deployments/list", ws: "none" },
  { slug: "strategies-strategy-id", title: "策略详情", scene: "research", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/strategies/{strategy_id}", ws: "none" , fate: "detail" },
  // strategies-strategy-id-versions 已删(2026-10-01):该路由是死占位页(DynamicPlaceholder
  // 只渲染「请从列表进入」,不解析 ?id=),而版本能力已由工作台内嵌 VersionTimeline 完整承载
  // (版本号/时间/回测指标 + 回滚动作)。留独立页只会分叉出"一处能回滚、一处不能"。后端端点
  // GET /strategies/{id}/versions 保留,仍由 VersionTimeline 消费。
  // trade 执行 场景
  // backtests-task-id 走通用 DetailPageView(?task_id= 直达渲染键值/表格);
  // 母页 backtests-page 的 Dialog 是完整版详情(图表+交易表),URL 同步见该页。
  { slug: "backtests-task-id", title: "回测详情", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/backtests/{task_id}", ws: "none" , fate: "detail" },
  // backtests-task_id / marketplace-listings-listing_id 下划线 twin 已删(2026-10-02, detail-batches):
  // 与连字符版同端点的 backend-insert 期重复变体(DEDUPE_UNDERSCORE_SLUGS 明说不进链),零引用。
  // 连字符版保留(批2/批3 接详情渲染)。
  // dex-trading-balance/portfolio 已删:薄壳路由,能力由 dex-trading-page inline 承载
  // (portfolio 走 /dex-trading/portfolio inline 渲染;balance 无人调,后端端点保留)。
  { slug: "grid-markets-exchange", title: "网格市场", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/grid/markets/{exchange}", ws: "none" , fate: "detail" },
  { slug: "grid-sentinel", title: "网格哨兵", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/grid/sentinel", ws: "none" },
  { slug: "grid-state", title: "网格状态", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/grid/state", ws: "none" },
  { slug: "optimizations-auto-auto-id", title: "优化详情", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/optimizations/auto/{auto_id}", ws: "none" , fate: "detail" },
  { slug: "optimizations-optimization-id", title: "优化详情", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/optimizations/{optimization_id}", ws: "none" , fate: "detail" },
  { slug: "paper-accounts", title: "模拟账户", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/paper/accounts", ws: "paper" },
  { slug: "paper-accounts-account-id", title: "模拟账户详情", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/paper/accounts/{account_id}", ws: "paper" , fate: "detail" },
  { slug: "paper-fills", title: "模拟成交", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/paper/fills", ws: "paper" },
  { slug: "paper-orders", title: "模拟订单", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/paper/orders", ws: "paper" },
  { slug: "paper-pnl", title: "模拟盈亏", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/paper/pnl", ws: "paper" },
  { slug: "paper-positions", title: "模拟持仓", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/paper/positions", ws: "paper" },
  { slug: "tof-all", title: "TOF 全部", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/tof/all", ws: "none", absorbedBy: "tof" },
  { slug: "tof-current", title: "TOF 当前", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/tof/current", ws: "none", absorbedBy: "tof" },
  { slug: "trading-balances", title: "交易余额", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/trading/balances", ws: "trading" },
  { slug: "trading-history", title: "交易历史", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/trading/history", ws: "trading" },
  { slug: "trading-kill-switch", title: "交易急停", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/trading/kill-switch", ws: "trading" },
  { slug: "trading-latency-stats", title: "延迟统计", scene: "trade", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/trading/latency-stats", ws: "trading" },
  // system 系统 场景
  { slug: "admin-kill-switch", title: "系统急停", scene: "system", category: "数据", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/admin/kill-switch", ws: "none" },
  { slug: "agent-trading-workspace", title: "交易工作台", scene: "system", category: "数据", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/agent/trading/workspace", ws: "trading" },
  { slug: "chat-sessions-session-id", title: "会话详情", scene: "system", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/chat/sessions/{session_id}", ws: "strategy", absorbedBy: "chat-sessions" },
  { slug: "marketplace-listings", title: "市场列表", scene: "system", category: "数据", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/marketplace/listings", ws: "none" },
  { slug: "marketplace-listings-listing-id", title: "市场详情", scene: "system", category: "数据", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/marketplace/listings/{listing_id}", ws: "none" , fate: "detail" },
  { slug: "marketplace-my-purchases", title: "我的购买", scene: "system", category: "数据", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/marketplace/my-purchases", ws: "none" },
  { slug: "mcp-tools", title: "MCP 工具", scene: "system", category: "数据", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/mcp/tools", ws: "none" },
  { slug: "me-export", title: "数据导出", scene: "system", category: "数据", renderMode: "chat", removable: true, origin: "backend-insert", rest: "/api/v1/me/export", ws: "none" },
  // notifications-event-types/preferences 已删(2026-10-02, detail-batches):薄壳路由,
  // 能力由 notify-preferences.tsx inline 承载(1001 P0-1:notifications-page 挂 NotifyPreferences)。
  // settings-llm-models/public-key 已删:薄壳路由,llm-settings-panel / public-key-panel inline 承载。
  // skills-name 已删:后端 GET /skills/{name} 真实,但 skill-page 母页零钻取零列表联动,页面无人到达。
  // 端点保留,等技能页做深链再回来建详情。
  { slug: "skills", title: "技能", scene: "system", category: "系统", renderMode: "list", removable: true, origin: "backend-insert", rest: "/api/v1/skills", ws: "none" },
  // risk overview (merged from pos/portfolio/serenity/dash)
  { slug: "risk", title: "风险总览", scene: "trade", category: "交易", renderMode: "research", removable: false, origin: "backend-insert", rest: undefined, ws: "trading" },
];

export const ALL_PAGES: PageMeta[] = [
  ...DESIGN_PAGES,
  ...DESIGN_PAGES_EXTENDED,
  ...BACKEND_INSERT_PAGES,
];

export function pagesByScene(scene: Scene): PageMeta[] {
  return ALL_PAGES.filter((p) => p.scene === scene);
}

// 场景默认页 — research 场景指向 AI 组织值班室(Director 打开第一眼是 TODAY),
// 其余场景取第一个 removable:false 的设计稿页
export const SCENE_DEFAULT_PAGE: Record<Scene, string> = {
  watch: "kf",
  research: "today",
  trade: "pos",
  system: "dash",
  workspace: "default",
};
