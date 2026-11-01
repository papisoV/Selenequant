/**
 * 框架层（chrome）译文表 —— 侧栏 / 顶栏上下文条 / 顶栏行情条 / 窄屏抽屉 /
 * 错误页 等「页面骨架」文案的 en 译文。
 *
 * 与 `data-layer.ts` 的分界:数据层来自数据文件(页面清单/导航链),本文件
 * 是组件里直接写死的框架文案。承接自 `a4044d83` 的单文件词表。
 */
export const CHROME_TEXT_EN: Record<string, string> = {
  // ── 侧边栏框架文案（sidebar.tsx，模板串用 {param}）─────────────
  "侧栏导航": "Sidebar navigation",
  "页面导航": "Page navigation",
  "数据分类": "Data Category",
  "双键快捷键": "Two-key shortcut",
  "{pages} 页 · {watchlist} 自选": "{pages} pages · {watchlist} watchlist",
  "自选 · 按市场": "Watchlist · By Market",
  "暂无自选品种 — kf 图表 ★ 加入": "No symbols yet — add one with ★ on the kf chart",
  "工具": "Tools",
  "更多": "More",
  "流程": "Workflow",
  "其他": "Other",

  // ── 场景名（侧栏标题 / 顶栏场景位；"研策" 已在数据层登记）──────
  "盯盘": "Watch",
  "执行": "Trade",
  "工作台": "Workspace",

  // ── 窄屏抽屉开关（dashboard-layout.tsx）───────────────────────
  "打开导航": "Open navigation",
  "关闭导航": "Close navigation",
  "菜单": "Menu",

  // ── 顶栏上下文条（ctx-bar.tsx）───────────────────────────────
  "品种": "Symbol",
  "策略": "Strategy",
  "最近信号": "Latest Signal",
  "当前持仓": "Position",
  "场景": "Scene",
  "{exchange} · 永续": "{exchange} · Perp",
  "多": "Long",
  "空": "Short",

  // ── 顶栏行情条（symbol-bar.tsx）─────────────────────────────
  "搜索品种(⌘K)": "Search symbol (⌘K)",
  "搜索品种…": "Search symbol…",
  "命令面板(⌘K)": "Command palette (⌘K)",
  "Kill Switch 功能开发中": "Kill Switch under development",
  "布局: {label}": "Layout: {label}",
  "重置布局": "Reset layout",
  "净值": "Equity",
  "今日": "Today",
  "夏普": "Sharpe",
  "重置": "Reset",
  "默认": "Default",
  "紧凑": "Compact",
  "专注": "Focus",
  "2 宫格": "2-Grid",
  "4 宫格": "4-Grid",
  "主+辅 2 图": "Main + Secondary",
  "6 图": "6 Charts",
  "9 图": "9 Charts",
  "极简": "Minimal",
  "专业": "Pro",

  // ── 错误页（app/error.tsx, app/global-error.tsx）──────────────
  "页面加载时发生错误，请重试。": "Something went wrong while loading the page. Please try again.",
  "重试": "Retry",
  "进入平台": "Enter Platform",
  "已复制": "Copied",
  "复制错误": "Copy Error",

  // ── B3 layout 批：agent 助手面板 / 会话 / 输入区 ────────────────
  "Agent 助手": "Agent Assistant",
  "Agent 助手 (可拖动)": "Agent Assistant (draggable)",
  "交易策略 AI 助手": "Trading Strategy AI Assistant",
  "你好，我是交易助手": "Hello, I'm your trading assistant",
  "可以问我策略、分析行情、解释指标": "Ask me about strategies, market analysis, or indicators",
  "问 Agent 操控 Selene · 输入自然语言（如「跑一遍 bb_v3 回测」）· Enter 发送":
    'Ask the Agent to drive Selene · type in natural language (e.g. "run a bb_v3 backtest") · Enter to send',
  "问 Agent… (品种上下文感知)": "Ask the Agent… (symbol-context aware)",
  "向 Agent 输入指令（Enter 发送，Shift+Enter 换行）":
    "Type a command for the Agent (Enter to send, Shift+Enter for a new line)",
  "需要登录才能使用 Agent": "Sign in to use the Agent",
  "登录后开始对话": "Sign in to start chatting",
  "新对话": "New Chat",
  "历史会话": "History",
  "暂无历史会话": "No chat history yet",
  "删除会话": "Delete session",
  "返回对话": "Back to Chat",
  "思考中...": "Thinking...",
  "内置": "Built-in",
  "📖 已加载": "📖 Loaded",
  "个方法论技能": "methodology skills",
  "{n} 个挖矿任务运行中": "{n} mining tasks running",
  "已参考": "Referenced",
  "条历史教训": "past lessons",
  "回答前按问题关键词检索了研究结论 / 历史策略失败 / 历史决策教训三类记忆库, 命中":
    "Before answering, searched three memory stores by question keywords — research conclusions / past strategy failures / past decision lessons; matched",
  "条注入到上下文。命中内容可在 Research → 记忆页查看。":
    "injected into context. View the matched content in Research → Memory.",
  "🧠 推理链 (": "🧠 Reasoning chain (",
  "步)": "steps)",
  "点击展开": "Click to expand",

  // ── B3 layout 批：确认 / 反手 / 对冲（bottom-drawer）────────────
  "确认": "Confirm",
  "确认?": "Confirm?",
  "确认反手": "Confirm Reverse",
  "确认对冲": "Confirm Hedge",
  "反手": "Reverse",
  "对冲": "Hedge",
  "反手将先平掉当前持仓，再以原数量的反向开仓。确认继续？":
    "Reversing will first close the current position, then open the opposite side at the same size. Continue?",
  "对冲将平掉当前持仓。确认继续？": "Hedging will close the current position. Continue?",
  "反手请求已发送": "Reverse request sent",
  "对冲请求已发送": "Hedge request sent",
  "再点一次确认（3 秒内）· ESC 取消": "Click again to confirm (within 3s) · ESC to cancel",
  "批准仅记录人工确认,不下单": "Approval only records manual confirmation; it does not place orders",
  "[批准]": "[Approve]",
  "[拒绝]": "[Reject]",
  "决策备注(redirect 必填理由)": "Decision note (reason required for redirect)",
  "已拍板:": "Decided:",
  "裁定失败:": "Verdict failed:",
  "提案列表不可达:": "Proposal list unreachable:",
  "。决策已写入 mission 活动流,可回放。":
    ". The decision is written to the mission activity stream and can be replayed.",
  "A股异动待批": "A-Share Anomalies Pending",
  "暂无待批异动提案": "No pending anomaly proposals",
  "异动决策档为 observe,不产生提案": "Anomaly decision mode is observe; no proposals are generated",
  "当前没有需要拍板的调查(诚实空态)。": "No investigations awaiting your call right now (honest empty state).",
  "研究决策": "Research Decisions",
  "研究决策 — 需要你拍板的调查": "Research Decisions — investigations awaiting your call",
  "研究": "Research",

  // ── B3 layout 批：持仓 / 账户 / 风控（bottom-drawer / asset-drawer）──
  "未实现盈亏": "Unrealized PnL",
  "持仓中": "In Position",
  "持仓市值": "Position Value",
  "持仓数": "Positions",
  "单仓位敞口": "Single-position exposure",
  "总敞口": "Total Exposure",
  "总杠杆": "Total Leverage",
  "风险敞口": "Risk Exposure",
  "相关性风险": "Correlation Risk",
  "核心指标": "Key Metrics",
  "日亏损": "Daily Loss",
  "历史平仓": "Closed Position History",
  "平仓": "Close",
  "平仓价": "Close Price",
  "开仓价": "Entry Price",
  "挂单": "Open Orders",
  "撤单": "Cancel",
  "改单": "Modify",
  "改单功能开发中": "Modify-order under development",
  "持仓改单功能开发中": "Modify-position under development",
  "全部挂单已撤": "All open orders cancelled",
  "无持仓可操作": "No positions to act on",
  "该市场暂无历史": "No history for this market yet",
  "该市场暂无持仓": "No positions in this market",
  "该市场暂无挂单": "No open orders in this market",
  "账户": "Account",
  "账户与状态": "Accounts & Status",
  "账户切换": "Switch Account",
  "已连接账户": "Connected Accounts",
  "暂无连接的交易所账户": "No connected exchange accounts",
  "+ 添加账户": "+ Add Account",
  "资产": "Assets",
  "资产总览 · ASSET": "Asset Overview · ASSET",
  "风控中心 · RISK": "Risk Center · RISK",
  "风控开关": "Risk Controls",
  "自动止损": "Auto stop-loss",
  "自动对冲": "Auto hedge",
  "回撤熔断": "Drawdown circuit breaker",
  "日亏损限制": "Daily loss limit",
  "数量": "Qty",
  "价格": "Price",
  "操作": "Actions",
  "状态": "Status",
  "用户": "User",
  "延迟": "Latency",
  "连接健康": "Connection Health",
  "未登录": "Not signed in",
  "登录": "Sign In",

  // ── B3 layout 批：cmdk 面板 / 搜索 ─────────────────────────────
  "搜索交易对": "Search pair",
  "搜索交易对，如 BTC / AAPL / 600519 / AU0…": "Search a pair, e.g. BTC / AAPL / 600519 / AU0…",
  "搜索页面": "Search pages",
  "搜索页面…": "Search pages…",
  "搜索结果": "Search Results",
  "页面结果": "Page Results",
  "无匹配结果": "No matching results",
  "没有匹配的品种，按 Tab 切到页面搜索": "No matching symbols; press Tab to search pages",
  "搜索服务暂不可用": "Search service temporarily unavailable",
  "↑↓ 导航 · Tab 切模式 · Enter 选中": "↑↓ navigate · Tab switch mode · Enter select",
  "页面": "Page",
  "Ctrl+J 切换": "Ctrl+J to switch",
  // 分组标题与页脚计数(2026-10-21 补:同一面板里 自选/最近 有译文而邻居没有)
  "热门": "Popular",
  "搜索": "Search",
  "添加自选": "Add to watchlist",
  "加入自选并打开图表": "Add to watchlist and open chart",
  "{n} 个符号": "{n} symbols",
  "{n} 个页面": "{n} pages",
  "{n} 个待拍板": "{n} awaiting your call",

  // ── B3 layout 批：Kill Switch / 实时连接诊断 ────────────────────
  "Kill Switch — 一键停止所有交易（两部确认）":
    "Kill Switch — stop all trading in one click (two-step confirmation)",
  "Kill Switch 已触发 — POST /trading/kill-switch/reset 重置":
    "Kill Switch triggered — POST /trading/kill-switch/reset to reset",
  "Kill Switch 已触发：所有挂单取消 + 平仓":
    "Kill Switch triggered: all open orders cancelled + positions closed",
  "仅 paper 模式可清零": "Only paper mode can be reset to zero",
  "行情流已断，正在用 REST 轮询，数据有延迟":
    "Quote stream dropped; polling via REST, data is delayed",
  "拖拽移动 · 显示 market/paper/live 三路 WebSocket 连接状态":
    "Drag to move · shows the market/paper/live WebSocket connection states",
  "显示 market/paper/live 三路 WebSocket 连接状态（live/degraded/recovering），用于诊断实时数据流是否正常。拖拽标题栏可移动，点击 − 折叠。":
    "Shows the market/paper/live WebSocket connection states (live/degraded/recovering) to diagnose whether the real-time data stream is healthy. Drag the title bar to move it; click − to collapse.",
  "暂无连接数据": "No connection data yet",
  "已连接": "Connected",
  "未连接": "Disconnected",
  "请求出错": "Request error",

  // ── B3 layout 批：面板框架 / 拖拽 / 其他 ────────────────────────
  "展开": "Expand",
  "折叠": "Collapse",
  "折叠 (ESC)": "Collapse (ESC)",
  "最大化": "Maximize",
  "最小化": "Minimize",
  "拖拽调整 Sidebar 宽度 · 双击重置": "Drag to resize the Sidebar · double-click to reset",
  "拖拽调整 VHero 宽度 · 双击重置": "Drag to resize VHero · double-click to reset",
  "拖拽调整抽屉高度 · 双击重置": "Drag to resize the drawer · double-click to reset",
  "切换到 BASE manifest": "Switch to BASE manifest",
  "切换到 EXTENDED manifest": "Switch to EXTENDED manifest",
  "多图监控暂未实现": "Multi-chart monitoring not yet implemented",
  "日志功能未规划": "Log feature not planned",
  "regime 状态加载中…": "Loading regime state…",
  "无 regime 窗口（TG 波动持续性 48h×0.85 未覆盖）":
    "No regime window (TG volatility persistence 48h×0.85 not covered)",
  "暂无实时 TG 事件 · 需启动 tg_listen --macro-db 并配置 TG 频道":
    "No live TG events yet · start tg_listen --macro-db and configure a TG channel",
  "跟单": "Copy Trade",
  "取消跟单": "Cancel Copy Trading",
  "模拟盘": "Paper",
  "实盘": "Live",
  "⚠ Agent 请求执行以下操作，需要确认":
    "⚠ The Agent requests the following actions; confirmation required",
  "h减仓": "h reduce",
  "暂无状态数据": "No status data",
  "{title} 是详情页,含动态参数({params})。请先从对应列表页选择记录打开。":
    "{title} is a detail page with dynamic params ({params}). Open it from the matching list page first.",
  "请从列表进入,或在 URL 带上 ?{param}=":
    "Open this from the list, or pass ?{param}= in the URL",
  "此页面需从列表进入": "This page must be opened from its list",
  "搜索卡片…": "Search cards…",
  "{symbol} 暂无数据": "No data for {symbol}",
  "无匹配卡片": "No matching cards",
  "行情流实时": "Market stream live",
  "行情流重连中": "Market stream reconnecting",
  "重连": "Reconnecting",
  "实时": "Live",
  "在 workspace 中以 {sym} 为标的下单": "Place an order on {sym} in the workspace",
  "后端未连接 — 界面为空态": "Backend offline — screens are empty",
  "正在后台重试；恢复后本提示自动消失": "Retrying in the background; this banner clears itself once it recovers",
  "立即重试": "Retry now",
  "诊断 ▼": "Diagnostics ▼",
  "收起诊断 ▲": "Hide diagnostics ▲",

  // ── 立问页（app/research/questions/new）────────────────────────
  "立一个新问题": "Raise a new question",
  "问题标题": "Question title",
  "一个问题,例:BTC 突破是否有效": "A question, e.g. Is the BTC breakout valid",
  "补充说明(可选)": "Additional notes (optional)",
  "任务卡四段填满,Selene 调查时才知道拿什么检验、拿什么对照。":
    "Fill in all four sections of the task card so Selene knows what to test against and what to control for.",
  "锚定 symbol": "Anchor symbol",
  "锚定周期": "Anchor interval",
  "锚定策略(可选,逗号分隔)": "Anchor strategies (optional, comma-separated)",
  "策略 ID,逗号分隔,例:V52": "Strategy IDs, comma-separated, e.g. V52",
  "锚定 regime(可选,逗号分隔)": "Anchor regimes (optional, comma-separated)",
  "Regime,逗号分隔,例:trend": "Regimes, comma-separated, e.g. trend",
  "锚定字段决定这个问题能召回哪些历史研究:symbol/周期/策略匹配的问题会互相看见证据。":
    "The anchor fields decide which past research this question can recall: questions matching on symbol / interval / strategy see each other's evidence.",
  "创建中…": "Creating…",
  "立问": "Raise",
  "填好标题就能立问": "Add a title to raise it",
  "创建失败 — 确认后端可用后重试": "Creation failed — check that the backend is up and retry",

  // ── Workspace 壳（app/workspace/**）────────────────────────────
  "多面板工作台 — 同屏显示研究 / 下单 / 监控,避免频繁切换 sidebar":
    "Multi-panel workspace — research / orders / monitoring on one screen, no sidebar hopping",
  "已存布局": "Saved layouts",
  "暂无布局": "No layouts yet",
  "未找到 signal 预设布局": "Signal preset layout not found",
  "← 返回 Workspace 首页": "← Back to Workspace home",
  "面板 · 拖拽重排 · 右下角 resize": "panels · drag to rearrange · resize from the corner",

  // ── 通用空态/兜底（components/shared）─────────────────────────
  "过滤…": "Filter…",
  "空记录": "No records",
  "页面未找到": "Page not found",
  "层级": "Tier",
  "演示层级切换": "Demo tier switcher",

  // ── hook 层 toast / 状态文案（lib/hooks/**）─────────────────────
  "推理中…": "Reasoning…",
  "反思中…": "Reflecting…",
  "已重连…": "Reconnected…",
  "连接中断，重连中 ({n}/3)…": "Connection lost, reconnecting ({n}/3)…",
  "调用工具": "Calling tool",
  "调用工具 {tool}": "Calling tool {tool}",
  "无持仓可平": "No position to close",
  "平仓请求已发送 ({side})": "Close request sent ({side})",
  "平仓失败: {msg}": "Close failed: {msg}",
  "原持仓杠杆未知,使用当前输入框杠杆 {lev}x":
    "Original position leverage unknown; using the input box leverage {lev}x",
  "持仓已平,反手开仓失败,请手动开仓: {msg}":
    "Position closed, but reversing the entry failed — open it manually: {msg}",
  "参数已过期请重新下单": "Parameters expired; place the order again",
  "会话尚未建立，请稍后重试": "Session not established yet; please retry shortly",
  "无权操作该会话的审批": "Not allowed to approve in this session",
  "审批请求失败（{code}）": "Approval request failed ({code})",
  "裁定失败": "Verdict failed",

  // ── Workspace 面板 / 布局管理（components/workspace）──────────
  "拖动以重排面板": "Drag to rearrange panels",
  "切换 panel 类型": "Switch panel type",
  "导入布局": "Import layout",
  "保存当前布局": "Save current layout",
  "重命名布局": "Rename layout",
  "布局名称": "Layout name",
  "复制布局": "Duplicate layout",
  "新布局名称": "New layout name",
  "删除布局": "Delete layout",
  "确认删除布局「": "Delete layout \"",
  "」?此操作不可撤销。": "\"? This cannot be undone.",
  "将当前 workspace 布局保存为新布局(P7 支持拖拽编辑后,这里会保存当前实际配置)。":
    "Save the current workspace layout as a new layout (once drag-editing lands in P7, this will save the actual configuration).",
  "导入失败": "Import failed",
  "知道了": "Got it",
  "面板": "panels",
  "列数": "Columns",
  "· 更新于": "· updated",
  "重命名": "Rename",
  "{name} 副本": "{name} copy",
  "· 拖拽重排 · 右下角 resize": "· drag to rearrange · resize from the corner",

  // ── 预设 workspace 布局名（lib/workspace/workspace-layouts）────
  "信号 → 下单": "Signal → order",
  "研究双栏": "Research split",
  "盯盘 + 盘口 + 下单": "Watch + book + order",

  // ── 立问页正文模板（app/research/questions/new）───────────────
  "假设(可证伪):": "Hypothesis (falsifiable):",
  "反向假设(什么情况会推翻它):": "Opposing hypothesis (what would refute it):",
  "对照信号(同条件下该看到什么/不该看到什么):":
    "Control signals (what should / should not appear under the same conditions):",
  "信号形成 → 最早可成交 → 标签窗口:":
    "Signal forms → earliest tradable → label window:",

  // ── MOCK 预览加载页（components/loading/mock-preview-loading.tsx）──
  // 该页由 app/loading.tsx 全局渲染,不在任何具体路由下,渲染普查扫不到。
  "MOCK 模式预览 · 后端未连接 · 等待启动 uvicorn":
    "MOCK preview · backend offline · waiting for uvicorn",
  "这是": "This is the",
  "模式下的界面预览": "interface preview in fallback mode",
  "后端服务未启动，当前显示的是前端 fallback 数据。":
    "The backend is not running; this page shows frontend fallback data.",
  "启动后端即可看到真实市场数据。":
    "Start the backend to see live market data.",
  "启动后端": "Start the backend",
  "# 启动 local-api 服务": "# Start the local-api service",
  "启动后端后，所有页面将显示真实市场数据":
    "Every page will show live market data once the backend is up",
  "WebSocket 实时推送订单簿/大单流/持仓":
    "WebSocket streams the order book, large trades and positions in real time",
  "MOCK 徽章自动消失": "The MOCK badge disappears on its own",
  "先进平台看看 MOCK 界面": "Open the platform to preview the MOCK UI",
  "平台内的橙色 MOCK 徽章会在后端连接后消失":
    "The orange MOCK badges inside the platform clear once the backend connects",
}
