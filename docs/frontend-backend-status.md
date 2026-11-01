# SeleneQuant 前后端对接状态

> 更新时间: 2026-07-15

## 已对接功能 (25+)

| 前端页面 | 后端路由 | API 调用 | 状态 |
|---------|---------|---------|------|
| Dashboard | health, backtests, paper, trading | checkHealth, listBacktests, listPaperAccounts, getTradingMode | ✅ |
| Kingfisher (行情) | markets | getTicker, getKlines, getOrderbook | ✅ |
| Markets2 (市场总览) | markets | getTicker, getKlines, getOrderbook | ✅ |
| Strategies (策略管理) | strategies | listStrategies, createStrategy, updateStrategy, deleteStrategy, validateStrategy, deployStrategy, getStrategyVersions, getFactoryTemplates | ✅ |
| Research (因子研究/调参) | research | runFactorResearch, getResearchResults, getTuningResults, runSmartTuning | ✅ |
| PA Analysis (价格行为) | pa | runPAAnalysis, getPAStatus, getPAHistory | ✅ |
| Paper Trading (模拟盘) | paper | listPaperAccounts, createPaperAccount, placePaperOrder, getPaperPositions, getPaperFills, getPaperPnL, getPaperEquityHistory, updatePaperSettings | ✅ |
| LiqMap (清算热力图) | liqmap | getLiqMap | ✅ |
| LiqRatio (清算比) | liqratio | getLiqRatio | ✅ |
| ToF (资金费率) | tof | getToF | ✅ |
| Agg Orderbook (聚合订单簿) | agg_orderbook | getAggOrderbook | ✅ |
| GEX (Gamma敞口) | gex | getGEX | ✅ |
| Signals (交易信号) | signals | getSignals | ✅ |
| KF Index | kf_index | getKFIndex | ✅ |
| Polymarket (预测市场) | polymarket | getPolymarketMarkets, getPolymarketEvents | ✅ |
| Serenity (基本面评分) | serenity | runSerenityAnalysis, getSerenityResults | ✅ |
| Skills (技能管理) | skills_api | listSkills, getSkillDetail, installSkill, uninstallSkill | ✅ |
| MCP (MCP服务器管理) | mcp | listMCPServers, getMCPTools, addMCPServer, removeMCPServer | ✅ |
| Settings (设置) | settings | getSettings, updateSettings, listLLMModels, setTradingMode | ✅ |
| Agent Chat (AI对话) | agent, chat_sessions | sendAgentMessage (SSE), listChatSessions, createChatSession, deleteChatSession | ✅ |
| Favorites (收藏) | favorites | listFavorites, addFavorite, removeFavorite | ✅ |
| Alpha Strategies | alpha | listAlphaStrategies, getAlphaStrategy | ✅ |
| Live Trading (实盘) | trading | placeOrder, cancelOrder, getPositions, getBalances, getTradeHistory | ✅ |
| Backtests (回测) | backtests | runBacktest, listBacktests, getBacktestResult | ✅ |
| DEX (去中心化交易所) | dex, dex_trading | getDEXData, getDEXTradingPairs | ✅ |
| Runner Status | health/agent | getRunnerStatus | ✅ |

---

## 未对接功能 (16个后端路由无前端)

### 高优先级

| 后端路由 | 功能 | 缺失影响 | 建议前端页面 |
|---------|------|---------|------------|
| `derivatives.py` | 衍生品行情 (期货/永续) | 量化平台没有衍生品界面，砍了一半功能 | Markets > Derivatives 子页 |
| `options.py` | 期权行情、期权链、隐含波动率 | 期权策略无法使用 | Markets > Options 子页 |
| `regime.py` | 市场状态分类 (趋势/震荡/波动) | 策略需要 regime 做自适应，用户看不到当前状态 | Dashboard > Regime 指标卡 |
| `cvd.py` | 累计成交量差 (CVD) | 机构级订单流指标，后端算好了没地方展示 | Kingfisher > CVD 指标叠加 |
| `account.py` | API Key 管理、交易所账户绑定 | 用户没地方配 API Key，实盘交易无法启动 | Settings > Accounts 子页 |
| `notifications.py` | 通知推送、风控告警 | 风控��警和交易信号没有推送，用户得一直盯盘 | 全局 Notification Center |

### 中优先级

| 后端路由 | 功能 | 缺失影响 | 建议前端页面 |
|---------|------|---------|------------|
| `heatmap.py` | 市场热力图 | 有端点没组件 | Markets > Heatmap |
| `hyperliquid.py` | Hyperliquid 交易所数据 | 交易所集成没界面 | Markets > Hyperliquid |
| `optimizations.py` | 策略优化引擎 | 和 Research smart tuning 不同，独立优化引擎没暴露 | Research > Optimization |
| `qevolve.py` | 遗传优化 (QE Evolve) | 高级优化功能不可用 | Research > QE Evolve |
| `intelligence.py` | 智能分析聚合 | 分析聚合层没界面 | Dashboard > Intelligence |
| `onchain.py` | 链上分析 | 链上数据没展示 | Markets > On-chain |

### 低优先级

| 后端路由 | 功能 | 缺失影响 | 建议前端页面 |
|---------|------|---------|------------|
| `us_radar.py` | 美股扫描 | 非核心 | Markets > US Radar |
| `marketplace.py` | 策略市场/社区分享 | 概念阶段 | Community > Marketplace |
| `admin.py` | 管理后台 | 内部功能 | Admin Panel |
| `memory.py` | Agent 记忆管理 | 内部功能 | Settings > Memory |
| `liquidations.py` | 清算事件 | LiqMap 部分覆盖 | LiqMap 扩展 |

---

## 覆盖率统计

- **已对接**: 25+ 功能 25+ / 后端路由 ~41 → **约 60%**
- **高优先级缺失**: 6 个路由
- **中优先级缺失**: 6 个路由
- **低优先级缺失**: 4 个路由

---

## 并行开发建议

按模块边界分任务，每个窗口只动一个包：

| 窗口 | 任务 | 只动 | 优先级 |
|------|------|------|--------|
| 1 | 衍生品+期权前端页 | `apps/desktop/src/` (新增页面) | 高 |
| 2 | Regime + CVD 前端展示 | `apps/desktop/src/` (指标组件) | 高 |
| 3 | Account 管理 + 通知中心 | `apps/desktop/src/` (设置+通知) | 高 |
| 4 | 回测前视偏差修复 | `packages/backtest-engine/` | 高 |
| 5 | 模拟盘滑点+资金费率 | `packages/paper-engine/` | 高 |
