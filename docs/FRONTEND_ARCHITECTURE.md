# SeleneQuant 前端架构

> `apps/desktop` Next.js 16 / React 19 / Tauri 应用的全面指南。
> 目标读者:需要端到端理解前端工作原理的新开发者。

---

## 目录

1. [入口与 Provider 栈](#1-入口与-provider-栈)
2. [页面清单系统(Page Manifest)](#2-页面清单系统page-manifest)
3. [PageRenderer + Widget 注册表分发](#3-pagerenderer--widget-注册表分发)
4. [四个通用渲染器](#4-四个通用渲染器)
5. [四场景布局架构](#5-四场景布局架构)
6. [Zustand 状态管理](#6-zustand-状态管理)
7. [数据获取双轨制](#7-数据获取双轨制)
8. [WebSocket 的高级机制](#8-websocket-的高级机制)
9. [BackendReady 门控与认证流程](#9-backendready-门控与认证流程)
10. [落地页各区块](#10-落地页各区块)
11. [完整的请求到渲染流水线](#11-完整的请求到渲染流水线)
12. [死代码清单](#12-死代码清单)
13. [架构图](#13-架构图)
14. [关键架构决策与理由](#14-关键架构决策与理由)

---

## 1. 入口与 Provider 栈

### Next.js App Router 入口

本应用使用 **Next.js 16 App Router** + React 19。根布局(`app/layout.tsx`)加载三套 Google 字体(Noto Sans SC 用于中文,Inter 用于拉丁字母,JetBrains Mono 用于代码),并用 `<ClientProviders>` 包裹所有内容。

```tsx
// app/layout.tsx (简化版)
export default function RootLayout({ children }) {
  return (
    <html lang="zh-CN" className={`${notoSansSC.variable} ${inter.variable} ${jetbrainsMono.variable}`}>
      <body><ClientProviders>{children}</ClientProviders></body>
    </html>
  );
}
```

### Provider 栈(由外到内顺序)

```
<html>
  <body>
    <ClientProviders>                          ← 根客户端边界
      <I18nProvider>                           ← i18n 上下文(默认 zh-CN)
        <AuthProvider>                         ← JWT 认证状态、token 刷新
          <BackendReadyProvider>               ← 健康检查门控
            {children}                         ← 应用路由
          </BackendReadyProvider>
        </AuthProvider>
      </I18nProvider>
    </ClientProviders>
  </body>
</html>
```

**`ClientProviders`**(`components/client-providers.tsx`)是单一客户端边界,组合了三个 provider。顺序很重要:

- **I18nProvider** 在最外层,这样认证错误和启动日志都能本地化。
- **AuthProvider** 包裹 BackendReady,这样健康门控在需要时可以读取认证状态。
- **BackendReadyProvider** 在最内层,门控 `children` —— 如果后端不可达且当前不是落地页路由,它会渲染一个诊断启动屏而不是应用本身。

### 根页面

`app/page.tsx` 仅渲染 `<LandingPage />`。落地页被 `BackendReadyProvider` 旁路(它的 `/` 路径在 `BYPASS_PATHS` 中),所以即使没有后端也始终能渲染。

### 路由结构

Next.js App Router 文件夹映射到 URL 段:

```
app/
├── layout.tsx              ← 根布局(providers)
├── page.tsx                ← "/" → LandingPage
├── watch/
│   ├── layout.tsx          ← <DashboardLayout scene="watch">
│   └── kf/page.tsx         ← /watch/kf → <PageRenderer meta={kf}>
├── research/
│   ├── layout.tsx          ← <DashboardLayout scene="research">
│   └── rsch/page.tsx       ← /research/rsch
├── trade/
│   ├── layout.tsx          ← <DashboardLayout scene="trade">
│   └── pos/page.tsx        ← /trade/pos
├── system/
│   ├── layout.tsx          ← <DashboardLayout scene="system">
│   └── dash/page.tsx       ← /system/dash
└── error.tsx / global-error.tsx / loading.tsx
```

每个场景有自己的 `layout.tsx`,挂载带正确 scene 的 `<DashboardLayout scene="...">`。scene 决定了侧边栏显示哪些页面,以及订阅哪些 WS 频道。

### API 代理

`next.config.ts` 把 `/api/:path*` 和 `/ws/:path*` 重写到 `http://localhost:8088`:

```ts
async rewrites() {
  return [
    { source: "/api/:path*", destination: "http://localhost:8088/api/:path*" },
    { source: "/ws/:path*", destination: "http://localhost:8088/ws/:path*" },
  ];
}
```

这让前端可以从浏览器调用 `/api/v1/markets/...` 而不会有 CORS 问题。WebSocket hooks 直接连到 `ws://localhost:8088/api/v1/ws/...`(不走代理 —— 它们绕过 Next.js)。

---

## 2. 页面清单系统(Page Manifest)

### 中央注册表

`lib/page-manifest.ts` 是所有页面的唯一真相源。它导出 `ALL_PAGES: PageMeta[]` 以及辅助函数 `pagesByScene()` / `SCENE_DEFAULT_PAGE`。

### PageMeta 模式

```ts
export interface PageMeta {
  slug: string;          // URL 段,例如 "kf", "us-radar"
  title: string;         // 中文显示名
  scene: Scene;          // "watch" | "research" | "trade" | "system"
  category: string;      // 侧边栏分组,例如 "主力", "数据", "策略"
  shortcut?: string;     // 键盘快捷键,例如 "⌘1"
  renderMode: RenderMode;// "symbol" | "research" | "list" | "chat"
  removable: boolean;    // 用户能否从侧边栏隐藏此页?
  origin: "design" | "backend-insert";
  rest?: string;         // REST 端点模板,例如 "/api/v1/markets/{symbol}/kline"
  ws?: WsChannel;        // 此页面订阅的 WS 频道
}
```

### 三个来源(`ALL_PAGES` 数组)

`ALL_PAGES` 是三个导出数组的拼接:

| 数组 | 数量 | 来源 | 示例 |
|---|---|---|---|
| `DESIGN_PAGES` | 22 | 原始 HTML 设计规范(`demo-aa-scene-layout.html` L3993-4036) | kf, mk, rsch, strat, pos, dash |
| `DESIGN_PAGES_EXTENDED` | 5 | 扩展设计规范 | vol_surface, smart_money, alpha_mine, portfolio, live_log |
| `BACKEND_INSERT_PAGES` | 18 | 没有设计规范的后端端点 | onchain, us-radar, hyperliquid, regime, cvd, heatmap, liquidations, serenity, qevolve, factors, backtests, dex-trading, optimizations, notifications, memory, marketplace, agent, chat-sessions |

**总计:45 个页面。**(第 46 个数组 `BACKEND_AUTO_PAGES`,共 240 个自动生成的页面,已被移除 —— 见[死代码清单](#12-死代码清单)。)

### 场景分布

| 场景 | 页面数 | 默认页 |
|---|---|---|
| `watch`(盯盘) | 22 | `kf`(主力监控) |
| `research`(研策) | 10 | `rsch`(研究中心) |
| `trade`(执行) | 8 | `pos`(我的持仓) |
| `system`(系统) | 9 | `dash`(总览) |

### Sidebar 完整页面列表（按场景 + 分类）

`Sidebar` 通过 `pagesByScene(scene)` 获取当前场景的页面，再按 `category` 字段分组渲染。🔒 = `removable: false`（不可从侧边栏移除）。

#### 盯盘 watch（22 页）

| 分类 | slug | 标题 | 渲染 | WS | Rest 端点 |
|---|---|---|---|---|---|
| **主力** | `kf` 🔒 | 主力监控 | Widget | markets | `/api/v1/markets/{symbol}/kline` |
| | `kfi` | 主力指数 | Widget | markets | `/api/v1/intelligence/whale-flow` |
| | `mk` | 行情总览 | Widget | markets | — |
| **数据** | `liqmap` | 爆仓地图 | Widget | intelligence | `/api/v1/liqmap/current` |
| | `liqratio` | 多空比 | Widget | intelligence | — |
| | `gex` | Gamma 暴露 | Widget | intelligence | `/api/v1/gex` |
| | `agg` | 聚合盘口 | Widget | markets | — |
| | `tac` | 资金费率 | Widget | intelligence | — |
| | `vol_surface` | 波动率曲面 | Widget | intelligence | `/api/v1/options/vol-surface` |
| | `onchain` | 链上指标 | symbol | — | `/api/v1/onchain` |
| | `us-radar` | US Radar | symbol | — | `/api/v1/us-radar` |
| | `hyperliquid` | HL 筛选 | list | — | `/api/v1/hyperliquid` |
| | `regime` | 市场体制 | symbol | — | `/api/v1/regime` |
| | `cvd` | CVD | symbol | — | `/api/v1/cvd` |
| | `heatmap` | 热力图 | symbol | — | `/api/v1/heatmap` |
| | `liquidations` | 爆仓 | symbol | — | `/api/v1/liquidations` |
| **信号** | `sig` | 信号中心 | Widget | intelligence | — |
| | `vchart` | 专业图表 | Widget | markets | — |
| **其他** | `poly` | 预测市场 | Widget | polymarket | `/api/v1/polymarket` |
| | `dex` | 链上 DEX | Widget | intelligence | — |
| | `beta` | 对冲策略 | Widget | — | `/api/v1/beta-play` |
| | `smart_money` | 聪明钱追踪 | Widget | intelligence | `/api/v1/onchain/smart-money` |

#### 研策 research（10 页）

| 分类 | slug | 标题 | 渲染 | WS | Rest 端点 |
|---|---|---|---|---|---|
| **研究** | `rsch` 🔒 | 研究中心 | Widget | — | `/api/v1/research` |
| | `alpha_mine` | Alpha 挖矿 | Widget | — | `/api/v1/alpha/mining` |
| | `serenity` | 风险扫描 | Widget | — | `/api/v1/serenity` |
| | `factors` | 因子库 | Widget | — | `/api/v1/research/factors` |
| | `tuning` | 参数调优 | Widget | — | `/api/v1/research/tuning/tasks` |
| **策略** | `strat` 🔒 | 策略工厂 | Widget | — | `/api/v1/strategies/factory-templates` |
| | `wid` | 组件库 | Widget | — | `/api/v1/widgets` |
| | `qevolve` | Q-Evolve | Widget | — | `/api/v1/qevolve` |
| | `portfolio` | 组合分析 | Widget | — | `/api/v1/portfolio` |

#### 执行 trade（8 页）

| 分类 | slug | 标题 | 渲染 | WS | Rest 端点 |
|---|---|---|---|---|---|
| **交易** | `pos` 🔒 | 我的持仓 | Widget | trading | `/api/v1/trading/positions` |
| | `paper` | 模拟交易 | Widget | paper | `/api/v1/paper` |
| | `trd` | 实盘下单 | Widget | trading | `/api/v1/trading/orders` |
| | `dex-trading` | DEX 交易 | Widget | — | `/api/v1/dex-trading` |
| | `live_log` | 实盘日志 | Widget | — | `/api/v1/trading/live-log` |
| **策略** | `grid` | 网格策略 | Widget | trading | `/api/v1/grid` |
| | `tof` | 交易运维 | Widget | — | `/api/v1/tof` |
| | `backtests` | 回测中心 | Widget | — | `/api/v1/backtests` |

#### 系统 system（9 页）

| 分类 | slug | 标题 | 渲染 | WS | Rest 端点 |
|---|---|---|---|---|---|
| **系统** | `dash` 🔒 | 总览 | Widget | — | `/api/v1/dashboard` |
| | `set` 🔒 | 设置 | Widget | — | `/api/v1/settings` |
| | `skill` | 技能市场 | Widget | — | `/api/v1/skills` |
| | `mcp` | MCP 管理 | Widget | — | `/api/v1/mcp` |
| | `notifications` | 通知中心 | Widget | — | `/api/v1/notifications` |
| | `memory` | 市场记忆 | Widget | — | `/api/v1/memory` |
| | `marketplace` | 策略市场 | Widget | — | `/api/v1/marketplace` |
| | `agent` | Agent Chat | Widget | agent | `/api/v1/agent` |
| | `chat-sessions` | Agent 会话 | Widget | — | `/api/v1/chat/sessions` |

> **注意**：`optimizer`、`scanner` 两个 slug 在 `BACKEND_INSERT_PAGES` 中定义（research 场景，策略分类），但代码中对应的是 `optimizations`（trade 场景）和 `scanner`（research 场景），分别占一个 sidebar 条目。由于它们的 `origin: "design"` 且部分字段与实际 `ALL_PAGES` 导出不完全一致，实际侧边栏显示以 `pagesByScene()` 返回结果为准。

> `BACKEND_AUTO_PAGES`（240 页，自动生成，origin="auto"）已于 2026-07 移除 —— 无前端组件、标题机器翻译、全堆一个 "auto" 分类。

### 场景默认值

```ts
export const SCENE_DEFAULT_PAGE: Record<Scene, string> = {
  watch: "kf",
  research: "rsch",
  trade: "pos",
  system: "dash",
};
```

`VHero` 在点击场景图标时用这个来导航(`router.push(\`/${s}/${SCENE_DEFAULT_PAGE[s]}\`)`)。

### 渲染模式分布

| RenderMode | 数量 | 使用者 |
|---|---|---|
| `symbol` | 21 | watch 页面(大多数)、vol_surface、smart_money、onchain 等 |
| `research` | 10 | rsch, pa, strat, wid, alpha_mine, portfolio, serenity 等 |
| `list` | 8 | pos, paper, trd, grid, tof, live_log, backtests 等 |
| `chat` | 6 | dash, skill, mcp, set, notifications, memory, marketplace, agent |

### 每页路由文件

`ALL_PAGES` 中的每个页面都有对应的 `app/<scene>/<slug>/page.tsx`,内容就是:

```tsx
// app/watch/kf/page.tsx
import { PageRenderer } from "@/components/shared/page-renderer";
import { ALL_PAGES } from "@/lib/page-manifest";

export default function Page() {
  const meta = ALL_PAGES.find((p) => p.slug === "kf")!;
  return <PageRenderer meta={meta} />;
}
```

页面文件只是 URL 到 meta 的薄映射层。所有渲染逻辑都在 `PageRenderer` 里。

---

## 3. PageRenderer + Widget 注册表分发

`components/shared/page-renderer.tsx` 实现了 **两级分发**:

### 第一级:Widget 注册表(懒加载的特定组件)

`WIDGET_REGISTRY` 把 `slug → React.lazy(Component)` 做映射。如果某个 slug 有条目,就用 `<Suspense>` 包裹懒加载组件并渲染。这是"真实页面"路径 —— 存在 43+ 个特定页面组件。

```tsx
const WIDGET_REGISTRY: Record<string, React.LazyExoticComponent<...>> = {
  // 17 个 PAGE_RENDERERS(特定实现)
  liqmap: lazyComp(() => import("@/components/watch/liqmap-page").then(m => ({ default: m.LiqmapPage }))),
  gex:    lazyComp(() => import("@/components/watch/gex-page").then(m => ({ default: m.GexPage }))),
  // ... 还有 15 个
  
  // 9 个主页面
  kf:    lazyComp(() => import("@/components/watch/kf-page").then(m => ({ default: m.KfPage }))),
  rsch:  lazyComp(() => import("@/components/research/rsch-page").then(m => ({ default: m.RschPage }))),
  pos:   lazyComp(() => import("@/components/trade/pos-page").then(m => ({ default: m.PosPage }))),
  // ... 还有 6 个
  
  // 4 个扩展页面
  vol_surface: lazyComp(() => import("@/components/watch/vol_surface-page").then(m => ({ default: m.VolSurfacePage }))),
  // ...
  
  // 17 个后端插入页面
  onchain: lazyComp(() => import("@/components/watch/onchain-page").then(m => ({ default: m.OnchainPage }))),
  // ...
  
  // 9 个衍生品/期权页面(Round 6)
  "derivatives-liqmap":     lazyComp(() => import("@/components/derivatives/liqmap-page").then(m => ({ default: m.LiqmapPage }))),
  "options-summary":        lazyComp(() => import("@/components/derivatives/options-page").then(m => ({ default: m.OptionsSummaryPage }))),
  // ...
};
```

### 第二级:通用渲染器(按 `renderMode` 兜底)

如果 slug **不在** `WIDGET_REGISTRY` 中,分发会根据 `meta.renderMode` 落到四个通用渲染器之一:

```tsx
export function PageRenderer({ meta }: { meta: PageMeta | undefined }) {
  if (!meta) return <NotFound />;

  // 第一级:尝试特定的懒加载组件
  const LazyComponent = WIDGET_REGISTRY[meta.slug];
  if (LazyComponent) {
    return (
      <Suspense fallback={<WidgetFallback />}>
        <LazyComponent />
      </Suspense>
    );
  }

  // 第二级:按模式选择通用渲染器
  switch (meta.renderMode) {
    case "symbol":   return <SymbolPanelPage meta={meta} />;
    case "research": return <ResearchPanelPage meta={meta} />;
    case "list":     return <DataTablePage meta={meta} />;
    case "chat":     return <ChatPanelPage meta={meta} />;
    default:         return <DataTablePage meta={meta} />;
  }
}
```

### 为什么这样设计

- **代码分割**:43+ 页面通过 `React.lazy` 懒加载,保持初始 JS bundle 较小。
- **优雅降级**:没有特定组件的页面仍能根据 `renderMode` + `rest` 模板渲染出可用的通用视图。
- **单一分发点**:所有 45 个页面路由汇聚到一个组件,所以添加页面 = 加到 `ALL_PAGES` + (可选)加到 `WIDGET_REGISTRY`。

---

## 4. 四个通用渲染器

每个通用渲染器都是一个自包含的 React 组件,接收 `meta: PageMeta` 并渲染通用视图。它们是静态导入(非懒加载),因为首屏渲染就会用到。

### 4.1 `SymbolPanelPage`(renderMode: "symbol")

**用途**:盯盘页面的 symbol 范围数据展示。

**Props**: `{ meta: PageMeta }`

**行为**:
1. 从 `useContextStore` 读取 `pageFilter.symbol`(例如 "BTC")。
2. 在 `meta.rest` 中替换 `{symbol}` / `{sym}` 来构建 REST 路径:
   ```ts
   const path = (meta.rest ?? `/api/v1/${meta.slug}`)
     .replace("{symbol}", symbol)
     .replace("{sym}", symbol);
   ```
3. 通过 `useApi<Record<string, unknown> | null>(path)` 拉取数据。
4. 渲染头部(标题 + symbol 徽标 + origin 徽标)和一个 2-3 列的 `<dl>`,显示前 40 个对象字段。

**使用者**:21 个 watch 页面(kfi, mk, liqmap, gex, agg, tac, sig, vchart, poly, dex, beta, vol_surface, smart_money, onchain, us-radar, regime, cvd, heatmap, liquidations)。

### 4.2 `ResearchPanelPage`(renderMode: "research")

**用途**:研策页面的卡片网格展示。

**Props**: `{ meta: PageMeta }`

**行为**:
1. 通过 `useApi<unknown[] | null>` 拉取 `meta.rest`(无 symbol 替换)。
2. 渲染 1/2/3 列响应式卡片网格,每张卡片显示某条数据的前 5 个字段。
3. 最多显示 60 条。

**使用者**:10 个 research 页面(rsch, pa, strat, wid, alpha_mine, portfolio, serenity, qevolve, factors,以及 optimizer/scanner/tuning)。

### 4.3 `DataTablePage`(renderMode: "list")

**用途**:trade/system 列表页面的表格展示。

**Props**: `{ meta: PageMeta }`

**行为**:
1. 通过 `useApi<unknown[] | null>` 拉取 `meta.rest`。
2. 渲染一个 HTML `<table>`,显示第一个对象的前 8 列,最多 100 行。
3. 头部显示行数。

**使用者**:8 个 trade 页面(pos, paper, trd, grid, tof, live_log, backtests, dex-trading, optimizations, hyperliquid)。

### 4.4 `ChatPanelPage`(renderMode: "chat")

**用途**:system 页面的状态卡片展示。

**Props**: `{ meta: PageMeta }`

**行为**:
1. 通过 `useApi<Record<string, unknown> | null>` 拉取 `meta.rest`。
2. 渲染纵向卡片,每张显示一个键值对(对象则 JSON 序列化)。
3. 最多 30 条。

**使用者**:6 个 system 页面(dash, skill, mcp, set, notifications, memory, marketplace, agent, chat-sessions)。

### 通用模式

四个渲染器都:
- 使用 `useApi`(基于 SWR —— 见[第 7 节](#7-数据获取双轨制))。
- 渲染带标题 + origin 徽标 + REST 路径的头部。
- 当 `data` 为 null 时显示加载/空状态。
- 刻意保持极简 —— 它们是兜底,不是功能完整的视图。

---

## 5. 四场景布局架构

`DashboardLayout`(`components/layout/dashboard-layout.tsx`)是所有四个场景的外壳。它接收 `scene` 作为 prop 并渲染:

```
┌─────────────────────────────────────────────────────────────────┐
│ app-shell (grid container)                                      │
│                                                                 │
│ ┌────┐ ┌────────┐ ┌──────────────────────────────────────────┐ │
│ │    │ │        │ │ main                                     │ │
│ │ V  │ │ Side   │ │ ┌──────────────────────────────────────┐ │ │
│ │ H  │ │ bar    │ │ │ SymbolBar                            │ │ │
│ │ e  │ │        │ │ ├──────────────────────────────────────┤ │ │
│ │ r  │ │ (pages │ │ │ CtxBar                               │ │ │
│ │ o  │ │  by    │ │ ├──────────────────────────────────────┤ │ │
│ │    │ │  scene)│ │ │ scene-layout {children}              │ │ │
│ │    │ │        │ │ │                                      │ │ │
│ │    │ │        │ │ │                                      │ │ │
│ │    │ │        │ │ └──────────────────────────────────────┘ │ │
│ └────┘ └────────┘ └──────────────────────────────────────────┘ │
│                                                                 │
│ BottomDrawer (overlay)  AgentFab (overlay)  FeedBar (overlay)   │
└─────────────────────────────────────────────────────────────────┘
```

### 组件组合

```tsx
export function DashboardLayout({ scene, children }) {
  useEffect(() => {
    // 卡片悬停 mousemove 跟踪,用于径向渐变发光
    const onMove = (e) => { /* 设置 --card-mx/--card-my CSS 变量 */ };
    window.addEventListener("mousemove", onMove);
    return () => window.removeEventListener("mousemove", onMove);
  }, []);

  return (
    <div className="app-shell">
      <SceneWsMount />                    {/* 不可见:挂载 WS hooks */}
      <ConditionalTradingWsMount />      {/* 不可见:实盘交易 WS(按模式门控) */}
      <LayoutResizer />                   {/* 不可见:拖拽调整侧边栏 */}
      <VHero />                           {/* 左侧垂直导航(60px) */}
      <RealtimeDebugPanel />              {/* 仅开发的浮动面板 */}
      <Sidebar scene={scene} />           {/* 页面列表 + 自选列表(240px) */}
      <main className="main">
        <SymbolBar />                     {/* symbol 搜索 + 周期 + 布局预设 */}
        <CtxBar scene={scene} />          {/* 上下文栏:活跃策略、信号 */}
        <div className="scene-layout">{children}</div>
      </main>
      <BottomDrawer />                    {/* 可展开的底部面板 */}
      <AgentFab />                        {/* 浮动 agent 按钮 */}
      <FeedBar />                         {/* 底部 feed 滚动条 */}
    </div>
  );
}
```

### 四条垂直条

#### VHero(60px,最左)

`components/layout/vhero/index.tsx` —— 一个垂直导航条,包含:

- **VheroAvatar** —— 用户头像(顶部)
- **VheroTrigger × 2** —— "资产"和"风险"触发按钮,切换 `AssetDrawer` / `RiskDrawer`。资产值从 `/api/v1/portfolio` 计算(`size × current_price` 之和),显示为 `$XK`。风险显示为 `% PnL`。
- **VheroConn** —— WS 连接状态指示器
- **VheroTime** —— 当前时间
- **场景导航(4 个图标)** —— watch / research / trade / system。点击调用 `router.push(\`/${s}/${SCENE_DEFAULT_PAGE[s]}\`)`。
- **VheroManifest** —— 布局清单切换(展开显示更多)
- **VheroKill** —— 急停按钮

#### Sidebar(240px,左数第二)

`components/layout/sidebar.tsx` —— 按场景的页面导航:

- **头部**:场景标签 + 页面数 + 自选数(例如 "盯盘 · 13 页 · 5 自选")
- **页面列表**按 `category` 分组:遍历 `pagesByScene(scene)`,按 `category` 分组,渲染 `<Link href="/{scene}/{slug}">`,激活状态来自 `usePathname()`。
- **自选列表**区块:拉取 `/api/v1/favorites`,通过 `groupByExch()` 把 symbol 分成 "CEX 主力" / "ALT 热点" / "DEX 新锐"。每项可点击设置 `pageFilter.symbol`。
- **覆盖滚动条**:滚动时出现金色滚动条,1s 后淡出(`scrolling` 状态 + 1000ms 定时器)。
- **键盘快捷键**:⌘1-4 切换场景(通过 `window.location.href` —— 整页刷新)。

#### Main(中央弹性区)

包含三个堆叠的水平栏:
- **SymbolBar**(`components/layout/symbol-bar.tsx`):symbol 搜索(通过 `cmdk-open` 自定义事件打开 CmdkPanel),7 个周期按钮(1m/5m/15m/1h/4h/1d/1w),9 个布局预设(default/compact/focus/grid2/grid4/charts6/charts9/minimal/pro),权益/PnL 跳动显示。
- **CtxBar**(`components/layout/ctx-bar.tsx`):上下文栏,显示活跃策略(`/api/v1/strategies/active`)、最新信号(`/api/v1/signals/latest?symbol=...`)、模拟持仓数量。
- **scene-layout {children}**:来自 `<PageRenderer>` 的实际页面内容。

#### 底部抽屉 + 覆盖层

- **BottomDrawer**(`components/layout/bottom-drawer.tsx`):可展开的底部面板,带 positions / events / agent / log / terminal 标签页。适配模拟 vs 实盘交易模式。
- **AgentFab**:浮动的 agent 聊天按钮。
- **FeedBar**:底部 feed 滚动条。

### 场景级 WS 挂载

`<SceneWsMount />` 是一个客户端包装器,在布局层一次性挂载所有 WS hooks(因为 `DashboardLayout` 是服务端组件,不能直接调用 hooks):

```tsx
export function SceneWsMount() {
  useSceneWs();              // markets + intelligence WS
  usePaperAccountTick(true); // 1.5s 权益/PnL 跳动
  usePaperWs("paper-default"); // 模拟账户 WS
  useStrategyWs();           // 策略信号 WS
  return null;
}

export function ConditionalTradingWsMount() {
  const mode = useTradingModeStore((s) => s.mode);
  if (mode !== "live") return null;
  return <LiveTradingWsMount />; // 仅 mode=live 时挂载实盘交易 WS
}
```

---

## 6. Zustand 状态管理

本应用使用 **Zustand v5**,采用 **基于切片的架构**。共有 9 个 store,每个聚焦一个关注点。`useContextStore` 聚合 hook 保留了对原巨型 store 的向后兼容。

### Store 清单

| Store | 文件 | 用途 | 使用文件数 | 关键字段 |
|---|---|---|---|---|
| `useContextStore` | `lib/store/context.ts` | **聚合 hook** —— 代理到 market + trading store | 33 文件(66 处) | symBase, pageFilter, gridParams, layoutPreset, tick |
| `useMarketStore` | `lib/store/market.ts` | 市场选择(symbol/tf/exchange)+ markets streamState | 6 文件(13 处) | symBase="BTC", pageFilter={tf:"1h",symbol:"BTC",exchange:"all"}, marketsStreamState |
| `useTradingStore` | `lib/store/trading.ts` | 交易域(layout/tick/gridParams) | 4 文件(8 处) | layoutPreset, tick={equity,pnlToday,flash}, gridParams |
| `useSubscriptionStore` | `lib/store/subscription.ts` | WS 订阅统计(markets + intelligence) | **0 文件(死代码)** | markets/intelligence connected/streamState/count |
| `useSceneStore` | `lib/store/scene.ts` | 当前场景 + 当前页面 | 1 文件(3 处) | current="watch", currentPage="kf" |
| `usePaperStore` | `lib/store/paper.ts` | 模拟账户实时状态 | 9 文件(33 处) | account, positions, orders, fills, equityPoints, signals, slotStatus, version |
| `useLiveTradingStore` | `lib/store/live-trading.ts` | 实盘账户实时状态 | 5 文件(17 处) | orders, positions, balances, fills, version |
| `useStrategyStore` | `lib/store/strategy.ts` | 策略信号流 | 3 文件(10 处) | signals[], latestSignal, version |
| `useTradingModeStore` | `lib/store/trading-mode.ts` | paper/live 模式切换(localStorage 持久化) | 6 文件(11 处) | mode="paper"(持久化到 "selenespace-trading-mode") |

### 聚合模式

`useContextStore` 是一个兼容垫片。原巨型 store 被拆成 `market` + `trading` + `subscription`,以防止级联 re-render(symbol 变化不再触发 tick 消费者 re-render)。新代码应直接使用具体 store:

```ts
// lib/store/context.ts
export function useContextStore() {
  const { symBase, pageFilter, setSymBase, setPageFilter } = useMarketStore();
  const { gridParams, layoutPreset, tick, setGridParams, setLayoutPreset, setTick } = useTradingStore();
  return { symBase, pageFilter, gridParams, layoutPreset, tick, /* setters */ };
}
```

### 实时 Store 模式(paper / live-trading / strategy)

这三个 store 共享相同的架构:

1. **状态**:`connected`、`streamState`、数据数组、`version`(每条 WS 消息自增)。
2. **`setFromMessage(msg)`**:一个 reducer,根据 `msg.type` 切换并更新相关切片。例如 `usePaperStore.setFromMessage` 处理 `fill` / `order_update` / `position_update` / `balance_update` / `account_snapshot` / `equity_point` / `runner_status` / `strategy_signal`。
3. **`version` 计数器**:每条改变状态的消息自增,这样消费者可以用单个 selector 对"任何变化"做出反应。

```ts
// 示例:usePaperStore position_update 处理器
case "position_update": {
  const pos = (msg.data ?? {}) as unknown as PaperPositionData;
  set((s) => {
    const idx = s.positions.findIndex(
      (p) => p.symbol === pos.symbol && (p.pos_side ?? p.side) === (pos.pos_side ?? pos.side)
    );
    const next = [...s.positions];
    if (idx >= 0) next[idx] = pos;
    else next.push(pos);
    return { positions: next, version: s.version + 1 };
  });
  break;
}
```

### 持久化

只有 `useTradingModeStore` 持久化(到 `localStorage["selenespace-trading-mode"]`)。其他所有 store 都是内存态,刷新即重置。

---

## 7. 数据获取双轨制

前端使用 **两条并行数据轨道**:SWR 用于 REST,原生 WebSocket hooks 用于实时流。

### 轨道一:SWR REST(`useApi`)

`lib/hooks/use-api.ts` 用项目专属约定包装 `swr`:

```ts
export function useApi<T>(
  path: string | null,
  opts?: { refreshInterval?: number; onErrorRetry?: boolean | ((err: ApiError) => boolean) }
) {
  const { data, error, isLoading } = useSWR<T | null>(
    path,
    () => fetcher<T>(path as string),
    {
      revalidateOnFocus: false,
      dedupingInterval: 5000,        // 5s 去重
      refreshInterval: opts?.refreshInterval,
      onErrorRetry: opts?.onErrorRetry === false ? () => false : undefined,
    }
  );
  return { data, error: error as ApiError | undefined, isLoading };
}
```

#### fetcher

`fetcher`(L8-33)处理三种响应形态:

1. **裸端点**(例如 `/api/v1/trading/positions`)—— 直接返回 JSON。
2. **APIResponse 包裹的端点** —— 返回 `{ data, meta, error }`。解包取 `data`。如果 `error` 有值,返回 null。如果 `data.message` 匹配 `/No .+ data available/i`,视为空(返回 null)。
3. **认证**:如果 `getAccessToken()` 返回 token,附加 `Authorization: Bearer <token>` 头。

#### 更底层的 `api-client.ts`

`lib/api-client.ts` 是另一个独立 fetcher,被变更路径(POST/PUT/DELETE)和需要精细控制的组件使用。关键特性:

- **在途 GET 去重**:`_inflight: Map<string, Promise>` 对相同 URL 的在途 GET promise 做复用。
- **`abortAllPendingGets()`**:发起非 GET 请求时取消所有在途 GET(释放连接)。
- **401 处理**:`handle401` 调用 `refreshToken()`(通过 `_refreshPromise` 去重)并重试原请求一次。
- **裸 vs 信封自动检测**:`api<T>(path)` 检查 `BARE_ENDPOINT_PATTERNS`(6 个正则,匹配 trading/portfolio/mcp/skills_api)并相应调用 `fetchBare` 或 `fetchEnvelope`。
- **`ApiError`**:自定义 Error,带 `status`、`code`、`details`,用于结构化错误处理。

#### `lib/api/` 子模块(死代码)

`lib/api/index.ts` 重新导出 5 个命名空间(`trading`、`markets`、`research`、`system`、`chat`),外加 `settings.ts`。**没有任何文件 `from "@/lib/api"` 或 `from "@/lib/api/trading"` 等导入** —— 这些是死代码。见[第 12 节](#12-死代码清单)。

### 轨道二:原生 WebSocket Hooks

`lib/ws/index.ts` 导出 **6 个 WS hooks**(外加 1 个工具):

| Hook | 频道 | 消息类型 | 消费 Store |
|---|---|---|---|
| `useMarketsWs` | `/api/v1/ws/markets` | ticker, kline, depth, kline_snapshot, large_trade, large_order, liquidation, funding_rate, footprint_bar, vpvr_update, options_ticker | `useMarketStore`(streamState) |
| `useIntelligenceWs` | `/api/v1/ws/intelligence` | news_update, event_signal, pulse_update, alert, tg_signal | (仅统计,通过 `useSceneWs`) |
| `usePaperWs` | (模拟账户) | fill, order_update, position_update, balance_update, account_snapshot, equity_point, runner_status, strategy_signal | `usePaperStore` |
| `useTradingWs` | (实盘交易) | fill, order_update, position_update, balance_update | `useLiveTradingStore` |
| `useStrategyWs` | (策略信号) | strategy_signal | `useStrategyStore` |
| `useTuningWs` | (LLM 调参) | tuning 进度 | (仅被 `tuning-control-panel.tsx` 消费) |
| `useRafBatch` | (工具,非 WS) | — | 用于需要 RAF 批处理的组件 |

#### 挂载模式

WS hooks 在布局层通过 `SceneWsMount` **一次性挂载**(见[第 5 节](#5-四场景布局架构))。它们写入 Zustand store;页面组件通过 selector 从 store 读取。这避免了每页都建 WS 连接。

---

## 8. WebSocket 的高级机制

WS hooks(尤其是 `useMarketsWs`)是前端最复杂的部分。它们实现了客户端代码中罕见的、生产级的可靠性模式。

### 8.1 RAF 批处理

高频消息(ticker/depth/kline,10+ Hz)被累积到 `pendingBatchRef`,通过 `requestAnimationFrame` 每帧刷新一次:

```ts
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  // ... 过期更新 ...
  if (batchHandlerRef.current) {
    pendingBatchRef.current.push(msg);
    if (rafIdRef.current === null) {
      rafIdRef.current = requestAnimationFrame(() => {
        rafIdRef.current = null;
        const items = pendingBatchRef.current;
        pendingBatchRef.current = [];
        if (items.length > 0 && batchHandlerRef.current) {
          batchHandlerRef.current(items);  // 每帧一次回调
        }
      });
    }
  } else {
    handlerRef.current?.(msg);  // 兜底:逐条消息回调
  }
};
```

独立的 `useRafBatch` hook(`lib/ws/use-raf-batch.ts`)以可复用工具形式提供相同模式:`schedule(item)`、`flush()`、`cancel()`。被需要自行批处理的组件使用。

**理由**:逐条消息 `setState` 会导致 10+ 次 re-render/秒。RAF 批处理把它们合并成 1 render/帧(最多 60fps)。

### 8.2 延迟退订(5s 宽限期)

用户切换 symbol 时,hook **立即订阅**新 symbol,但**延迟 5 秒**退订旧 symbol(`UNSUB_DELAY_MS = 5000`):

```ts
useEffect(() => {
  // symbols 变化
  sendSubscribe(ws, symbols, ...);          // 立即订阅新
  if (pendingUnsubRef.current) clearTimeout(pendingUnsubRef.current);
  
  pendingUnsubRef.current = setTimeout(() => {
    sendUnsubscribe(currentWs, oldSymbols, ...);  // 5s 后退订旧
    pendingUnsubRef.current = null;
  }, UNSUB_DELAY_MS);
}, [symbols.join(",")]);
```

**理由**:用户经常在 2-3 个 symbol 之间切换。如果 5s 内切回,挂起的退订会被取消,旧 symbol 的流仍然热 —— 没有冷启动间隙。

### 8.3 REST 轮询兜底(降级模式)

当 `streamState` 变为 `"degraded"`(5s 无消息)时,hook 开始**每 5s 轮询 REST ticker 端点**作为兜底:

```ts
useEffect(() => {
  if (state.streamState !== "degraded") return;
  const interval = setInterval(async () => {
    if (state.streamState !== "degraded") return;  // 重新检查
    for (const sym of symbols) {
      try {
        const resp = await fetch(`http://${apiHost}/api/v1/markets/${sym}/ticker`);
        if (!resp.ok) continue;
        const json = await resp.json();
        // 作为 ticker 消息发出,让消费者自然更新
        const msg: MarketMessage = { type: "ticker", data: {...}, timestamp: new Date().toISOString() };
        if (batchHandlerRef.current) batchHandlerRef.current([msg]);
        else handlerRef.current?.(msg);
      } catch { /* 忽略 */ }
    }
  }, 5000);
  return () => clearInterval(interval);
}, [enabled, symbols, state.streamState]);
```

发出的消息与 WS 消息无法区分,所以消费者不需要单独的代码路径。

### 8.4 过期检测(三态流)

hook 维护三态 `streamState`:

| 状态 | 含义 | 触发 |
|---|---|---|
| `"live"` | 最近收到过消息 | 任何消息到达 |
| `"degraded"` | markets >5s / intelligence/paper >10s 无消息 | 过期定时器触发 |
| `"recovering"` | 已断开,正在重连 | `onclose` / `onerror` / seq 间隙 |

一个 `setInterval` 每秒运行,检查 `lastMessageAt`:

```ts
staleTimerRef.current = setInterval(() => {
  setState((s) => {
    if (s.streamState === "recovering" || s.lastMessageAt === null) return s;
    const since = Date.now() - s.lastMessageAt;
    if (since > STALE_AFTER_MS && s.streamState !== "degraded") {
      return { ...s, streamState: "degraded" };
    }
    return s;
  });
}, 1000);
```

`streamState` 同步到 `useMarketStore.marketsStreamState`,`use-klines.ts` 监听它,在 `recovering → live` 转换时触发补缺 REST 拉取。

### 8.5 序列号间隙检测

每条消息可能带 `seq` / `sequence` / `update_id` / `u` 字段。hook 跟踪 `lastSeqRef`,如果间隙超过 1 则触发重连:

```ts
function extractSequence(msg: unknown): number | null {
  const raw = m.seq ?? m.sequence ?? d?.seq ?? d?.sequence ?? d?.update_id ?? d?.u;
  return typeof raw === "number" && Number.isFinite(seq) ? seq : null;
}

// 在 onmessage 中:
const seq = extractSequence(msg);
if (seq !== null) {
  const prev = lastSeqRef.current;
  if (prev !== null && seq > prev + 1) {
    setState((s) => ({ ...s, streamState: "recovering" }));
    ws.close();  // 触发重连
    return;
  }
  lastSeqRef.current = seq;
}
```

**理由**:间隙意味着传输中丢失了消息。重连并补缺比静默漏掉更新更好。

### 8.6 指数退避重连

```ts
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

const scheduleReconnect = () => {
  if (intentionalCloseRef.current) return;
  const attempt = attemptRef.current;
  const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, attempt), RECONNECT_MAX_MS);
  attemptRef.current += 1;
  reconnectTimerRef.current = setTimeout(() => connect(), delay);
};
```

退避:1s → 2s → 4s → 8s → 16s → 30s(封顶)。`onopen` 成功后重置为 0。

### 8.7 增量订阅(不重建 ws)

hook 把**连接级** effect 与**订阅级** effect 分开:

- **连接 effect**(deps:`exchange`、`marketType`、`enabled`):重建 WebSocket。
- **Symbols effect**(deps:`symbols.join(",")`):在已有 socket 上发 `subscribe` / `unsubscribe` 消息。
- **Interval effect**(deps:`interval`):仅对 kline 流发 `unsubscribe`(旧周期)+ `subscribe`(新周期)—— ticker/depth 不受影响。

**理由**:每次 symbol 变化就重建 WS 会丢失连接状态并触发 1s 重连窗口。增量订阅保持 socket 热状态。

### 8.8 重连订阅恢复

`onopen` 时,hook 用 `subRef.current`(上次已知订阅状态)重新发 `subscribe` 消息:

```ts
ws.onopen = () => {
  attemptRef.current = 0;
  lastSeqRef.current = null;
  setState((s) => ({ ...s, connected: true, streamState: "recovering" }));
  const cur = subRef.current;
  sendSubscribe(ws, cur.symbols, cur.exchange, cur.marketType, cur.interval);
};
```

这确保订阅在重连后存活,消费者无需重新发起。

---

## 9. BackendReady 门控与认证流程

### BackendReadyProvider

`components/providers/backend-ready-provider.tsx` 是一个**健康检查门控**,在后端可达前阻塞应用渲染。

#### 旁路路径

```ts
const BYPASS_PATHS = ["/"];
```

落地页(`/`)始终渲染,即使没有后端。一旦用户导航到任何 `/watch`、`/research`、`/trade` 或 `/system` 路由,门控就会激活。

#### 健康检查循环

```ts
const MAX_RETRIES = 60;
const POLL_INTERVAL = 1000;  // 1s

const startPolling = async () => {
  setStatus("checking");
  for (let i = 0; i < MAX_RETRIES; i++) {
    setAttempt(i + 1);
    const ok = await checkHealth();  // 拉取 /api/v1/health,5s 超时
    if (ok) return;
    await new Promise(r => setTimeout(r, POLL_INTERVAL));
  }
  setStatus("failed");
};
```

- 每 1s 轮询 `/api/v1/health`,最多 60 次(共 60s)。
- 成功:渲染 children。
- 失败:渲染 "Runtime Unreachable" 错误块,带 "Reconnect" 按钮。

#### 启动序列 UI

检查期间,provider 渲染一个电影感的启动屏:

- **环境光球**(3 个模糊径向渐变,通过 CSS 动画漂移)
- **网格层**(淡 60×60px 网格,带径向遮罩)
- **扫描线**(垂直线扫下)
- **噪声纹理**(SVG turbulence,1.2% 不透明度)
- **品牌行**(SeleneQuant logo,带旋转锥形渐变边框)
- **启动日志**(`BOOT_SEQUENCE` 数组的条目,在特定尝试次数时发出):
  ```
  [core] Quant engine loaded
  [data] Market data layer initialized
  [strategy] 18 strategy templates registered
  [ws] WebSocket channels pending
  [api] REST endpoints standing by
  [cache] SQLite connection pool ready
  [auth] Local auth context initialized
  [runtime] Still waiting for health check...
  ```
- **活动行**,带动画圆点 + 进度条(就绪前封顶 95%)

`BOOT_SEQUENCE` 数组把尝试次数映射到消息,所以随着轮询进行,会陆续出现更多日志行,模拟真实启动序列。

#### 诊断日志

失败时,一个可折叠的 "Diagnostic log" 区块显示 `debugRef.current` 中收集的原始 HTTP 状态 / 错误字符串。

### AuthProvider

`components/auth/AuthProvider.tsx` 提供基于 JWT 的认证上下文:

- **`isAuthenticated**:`getAccessToken()` 中存在未过期 token 时为 true。
- **`userId` / `role` / `displayName` / `avatarUrl`**:从 JWT payload 提取。
- **`logout()`**:清除 token。
- **`syncAuth()`**:重新读取 token 状态。
- **Token 过期检查**:`isTokenExpired()` 比较 `payload.exp` 与 `Date.now()`。
- **自动刷新**:在 fetcher 层处理(`api-client.ts:handle401`)。

#### 认证流程

1. 用户登录(登录 UI 不在本文档范围)。
2. token 存储(可能通过 `lib/auth.ts` 存 localStorage)。
3. `AuthProvider` mount 时读取 token,设置 `isAuthenticated=true`。
4. 所有 REST 请求附加 `Authorization: Bearer <token>` 头。
5. 401 时:`handle401` 调用 `refreshToken()`(去重),重试原请求。
6. 刷新失败:清除 token,`isAuthenticated=false`,用户重定向到登录。

社区版默认:`userId="local"`、`role="admin"` —— 本地模式假定管理员权限,无需真实认证。

---

## 10. 落地页各区块

`components/landing/landing-page.tsx` 在 `/` 渲染一个营销落地页。所有区块使用 `dynamic(() => import(...), { ssr: false })` 以避免 `framer-motion` 的 `useInView` 引发 SSR 问题。

### 区块顺序

```tsx
<main className="min-h-screen bg-[#131722] text-white">
  <SplineSceneBasic />       {/* 带 3D spline 场景的 Hero */}
  <TickerMarquee />         {/* 滚动的 BTC/ETH/SOL/... ticker */}
  <CorePhilosophy />        {/* "为何量化交易" */}
  <SectionDivider glow />
  <WorkflowDemo />          {/* 策略 → 回测 → 部署 流程 */}
  <SectionDivider dot />
  <WhySeleneSpace />        {/* 功能亮点 */}
  <SectionDivider line />
  <SkillsSystem />          {/* AI skills 展示 */}
  <SectionDivider dot />
  <Architecture />          {/* 系统架构图 */}
  <SectionDivider glow />
  <ExchangesStats />        {/* 支持的交易所 + 统计 */}
  <SectionDivider line />
  <Roadmap />               {/* 开发路线图 */}
  <SectionDivider glow />
  <CtaSection />            {/* 行动号召 */}
  <Footer />                {/* 链接 + 版权 */}
</main>
```

### TickerMarquee

一个硬编码的 7 对 ticker(BTC/ETH/SOL/BNB/ARB/OP/AVAX),通过 `framer-motion` 水平滚动:

```tsx
const items = [...TICKERS, ...TICKERS, ...TICKERS];  // 三倍以无缝循环
<motion.div
  className="flex gap-12 w-max"
  animate={{ x: ["-33.33%", "0%"] }}
  transition={{ duration: 30, repeat: Infinity, ease: "linear" }}
>
```

### SplineSceneBasic

一个重型客户端组件(framer-motion + SVG),通过 `dynamic` 加载,`ssr: false`,带 `Skeleton` 兜底以避免阻塞首屏。

### SectionDividers

来自 `primitives.tsx` 的三个变体:`glow`、`dot`、`line` —— 区块间的视觉分隔符。

### 文件

```
components/landing/
├── landing-page.tsx       ← 组合根
├── spline-scene.tsx       ← hero 3D 场景
├── core-philosophy.tsx    ← CorePhilosophy + WorkflowDemo
├── why-selene.tsx         ← WhySeleneSpace
├── skills-system.tsx      ← SkillsSystem
├── architecture.tsx       ← Architecture
├── exchanges-stats.tsx    ← ExchangesStats
├── roadmap.tsx            ← Roadmap
├── cta-footer.tsx         ← CtaSection + Footer
└── primitives.tsx         ← SectionDivider
```

---

## 11. 完整的请求到渲染流水线

### 追踪:从 URL 到像素的 `/watch/kf`

#### 步骤 1:URL → 路由匹配

Next.js App Router 把 `/watch/kf` 匹配到 `app/watch/kf/page.tsx`。场景布局 `app/watch/layout.tsx` 包裹它:

```tsx
// app/watch/layout.tsx
export default function WatchLayout({ children }) {
  return <DashboardLayout scene="watch">{children}</DashboardLayout>;
}
```

#### 步骤 2:Provider 栈初始化

路由渲染前,provider 栈(来自 `app/layout.tsx`)初始化:

1. `I18nProvider` 加载 zh-CN 字符串。
2. `AuthProvider` 从 localStorage 读取 JWT,设置 `isAuthenticated`。
3. `BackendReadyProvider` 检查 `pathname`:
   - `/watch/kf` 不在 `BYPASS_PATHS` 中,所以开始健康轮询。
   - 每 1s 轮询 `/api/v1/health`。
   - 轮询期间:渲染启动屏(光球 + 启动日志 + 进度条)。
   - 成功:渲染 children。

#### 步骤 3:DashboardLayout 挂载

后端就绪后,`<DashboardLayout scene="watch">` 渲染:

1. `<SceneWsMount />` 挂载所有 WS hooks:
   - `useSceneWs()` 从 `useContextStore` → `useMarketStore` 读取 `pageFilter`。Symbol 是 "BTC" → 归一化为 "BTC/USDT"。调用 `useMarketsWs({ symbols: ["BTC/USDT"], exchange: "binance", interval: "1h", onMessage: pushMarket })`。WebSocket 连到 `ws://localhost:8088/api/v1/ws/markets`,发送 `subscribe` 消息。
   - `useSceneWs()` 还调用 `useIntelligenceWs({ channels: ["intel:news","intel:events","intel:pulse","intel:alerts"], onMessage: pushIntel })`。第二个 WebSocket 连到 `/api/v1/ws/intelligence`。
   - `usePaperWs("paper-default")` 连到模拟账户 WS,写入 `usePaperStore`。
   - `useStrategyWs()` 连到策略信号 WS,写入 `useStrategyStore`。
   - `usePaperAccountTick(true)` 启动 1.5s 间隔拉取权益/PnL,写入 `useTradingStore.tick`。
2. `<ConditionalTradingWsMount />` 读取 `useTradingModeStore.mode`。如果是 "paper"(默认),返回 null。如果是 "live",挂载 `useTradingWs`。
3. `<VHero />` 渲染。拉取 `/api/v1/portfolio` 计算 "资产" 触发器的总权益。
4. `<Sidebar scene="watch" />` 渲染。拉取 `/api/v1/favorites` 获取自选列表。渲染 13 个 watch 页面,按类别分组(主力 / 数据 / 信号 / 其他)。
5. `<SymbolBar />` 渲染。从 `useContextStore` 读取 `pageFilter` 获取当前周期。读取 `tick` 显示权益/PnL。
6. `<CtxBar scene="watch" />` 渲染。拉取 `/api/v1/strategies/active` 和 `/api/v1/signals/latest?symbol=BTC`。读取 `usePaperStore.positions` 获取持仓数。
7. `<div className="scene-layout">{children}</div>` 渲染页面。

#### 步骤 4:PageRenderer 分发

`app/watch/kf/page.tsx` 执行:

```tsx
const meta = ALL_PAGES.find((p) => p.slug === "kf")!;
return <PageRenderer meta={meta} />;
```

`PageRenderer` 检查 `WIDGET_REGISTRY["kf"]` → 找到(`lazyComp(() => import("@/components/watch/kf-page"))`)。用 `<Suspense fallback={<WidgetFallback />}>` 包裹并渲染 `<KfPage />`。

#### 步骤 5:KfPage 渲染

`components/watch/kf-page.tsx`(最重的页面,约 970 行):

1. 从 `useContextStore` 读取 `pageFilter`、`symBase`、`layoutPreset`。
2. 调用 `useKTTracks()` 获取 kline 时间轴轨道(信号 / 持仓 / agent 动作)。
3. 通过 `useApi` 并行拉取 6 个 REST 端点:
   - `/api/v1/liqmap/current?symbol=BTC`(清算数据)
   - `/api/v1/derivatives/funding-rates`(资金费率)
   - `/api/v1/markets/BTC/USDT/orderbook?depth=5`(订单簿)
   - `/api/v1/intelligence/whale-flow?symbol=BTC&limit=10`(鲸鱼流)
   - `/api/v1/markets/BTC/ticker`(ticker)
   - `/api/v1/derivatives/open-interest`(持仓量)
   - `/api/v1/gex/current?currency=BTC`(GEX)
4. 把后端原始数据形态适配为展示形态(`adaptOb`、`adaptWhaleFlow`)。
5. 渲染:
   - **图表工具栏**(K线/面积/MA/VOL/MACD 切换,布局按钮)
   - **图表主体**(基于 `layoutPreset` 的网格:1/2/4/6/9 格,每格渲染 `<KfChart>`,多 symbol 网格可带 `symbolOverride`)
   - **Kline 时间轴**(3 条 SVG 轨道:信号、持仓、agent 动作,带悬停 tooltip)
   - **右侧面板**(7 张可拖拽/可移除卡片:info、trade、orderbook、whaleflow、liquidation、gex、funding)
6. `<KfChart>`(独立组件)可能用 `lightweight-charts` 和 `use-klines` hook,后者订阅 kline WS 消息,并在 `marketsStreamState` 从 `recovering → live` 转换时通过 REST 补缺。

#### 步骤 6:实时更新

挂载后,页面从两个来源更新:

1. **REST revalidation**:SWR 在 focus 时重新验证(已禁用)、按间隔(若设了 `refreshInterval`)、以及手动 mutate 时。上述 6 个端点按需重新拉取。
2. **WebSocket 消息**:`useMarketsWs` hook(挂载在 `SceneWsMount`)接收 ticker/kline/depth 消息。这些流入:
   - `useMarketStore.marketsStreamState`(用于流状态)
   - `use-klines` hook(用于 kline 数据,如果 `KfChart` 用了它)
   - 任何传了 `onMessage` 或 `onMessageBatch` 给 `useMarketsWs` 的组件

实时 store 的 `version` 计数器(`usePaperStore.version` 等)在每条消息时自增,触发选中了 `version` 的组件 re-render。

#### 步骤 7:切换 Symbol

用户在侧边栏自选列表点 "ETH" 时:

1. `selectWatchlist("ETH")` 调用 `useMarketStore` 上的 `setPageFilter({ symbol: "ETH" })`。
2. `useContextStore` 让所有 33 个消费者 re-render(包括 `KfPage`)。
3. `KfPage` 用新 symbol 重新拉取所有 6 个 REST 端点。
4. `useSceneWs`(挂载在 `SceneWsMount`)用 `symbols: ["ETH/USDT"]` 重新调用 `useMarketsWs`。symbols effect 触发:
   - 立即发送 "ETH/USDT" 的 `subscribe`。
   - 5s 后调度 "BTC/USDT" 的 `unsubscribe`。
   - 调用 `onSymbolsChangeRef.current()`,让 `use-klines` 清除旧 BTC klines。
5. 如果用户 5s 内切回 BTC:挂起的退订被取消,BTC 流保持热状态。

#### 步骤 8:像素

浏览器绘制更新后的 DOM。RAF 批处理的 WS 消息确保即使 10+ 消息/秒,每帧最多 1 次绘制。

---

## 12. 死代码清单

### 12.1 死 Store

| Store | 文件 | 状态 | 证据 |
|---|---|---|---|
| `useSubscriptionStore` | `lib/store/subscription.ts` | **死** | 0 个消费文件。仅在自己的定义 + `lib/store/context.ts` 中被引用(后者其实没用到 —— `useContextStore` 不返回 subscription 字段)。原本打算把 WS 统计透传给 `VheroConn`,但从未接通。 |

### 12.2 死 API 层

| 模块 | 文件 | 状态 | 证据 |
|---|---|---|---|
| `lib/api/index.ts` | 重新导出 `trading/markets/research/system/chat` 命名空间 | **死** | 0 文件 `from "@/lib/api"` 导入。 |
| `lib/api/trading.ts` | trading API 命名空间 | **死** | 0 文件 `from "@/lib/api/trading"` 导入。 |
| `lib/api/markets.ts` | markets API 命名空间 | **死** | 0 文件 `from "@/lib/api/markets"` 导入。 |
| `lib/api/research.ts` | research API 命名空间 | **死** | 0 文件 `from "@/lib/api/research"` 导入。 |
| `lib/api/system.ts` | system API 命名空间 | **死** | 0 文件 `from "@/lib/api/system"` 导入。 |
| `lib/api/chat.ts` | chat API 命名空间 | **死** | 0 文件 `from "@/lib/api/chat"` 导入。 |
| `lib/api/settings.ts` | settings API 命名空间 | **死** | 0 文件 `from "@/lib/api/settings"` 导入。 |

所有真实 REST 调用都走 `useApi`(SWR)或 `lib/api-client.ts` 的 `api/apiPost/apiPut/apiDelete`。`lib/api/` 目录是一个半途而废的重构。

### 12.3 死页面组件

| 组件 | 文件 | 状态 | 证据 |
|---|---|---|---|
| `PageStub` | `components/shared/page-stub.tsx` | **死** | 仅在 `e2e/phase5-smoke.spec.ts`(一个测试)中被引用。`PageRenderer` 和其他生产代码都不导入。是通用渲染器存在之前的 Phase-4 占位符。 |

### 12.4 已移除的自动生成页面

`lib/page-manifest.ts` 第 19 行记录了移除:

```ts
// BACKEND_AUTO_PAGES (240 页, 自动生成) 已移除 — 无前端组件、标题机器翻译、全堆一个 "auto" 分类
```

一个 `scripts/gen-auto-pages.py` 仍存在(grep 中被引用 2 次),但它生成的数组已没了。240 个自动页面没有前端组件、标题是机器翻译、全堆在一个 "auto" 分类 —— 因质量原因被移除。

### 12.5 重复路由(连字符 vs 下划线)

`app/` 目录对同一逻辑页面存在**重复路由文件夹**,仅连字符 vs 下划线不同:

| 连字符路径 | 下划线路径 | 可能意图 |
|---|---|---|
| `research/alpha-formulas-formula-id/` | `research/alpha-formulas-formula_id/` | 本意是动态路由 `[formula_id]`;两个文件夹都存在 |
| `research/alpha-train-session-id-status/` | `research/alpha-train-session_id-status/` | 同模式 |
| `system/agent-analysis-status-analysis-id/` | `system/agent-analysis-status-analysis_id/` | 重复 |
| `system/agent-analysis-stream-analysis-id/` | `system/agent-analysis-stream-analysis_id/` | 重复 |
| `system/chat-sessions-session-id/` | `system/chat-sessions-session_id/` | 重复 |
| `system/chat-sessions-session-id-messages/` | `system/chat-sessions-session_id-messages/` | 重复 |
| `system/marketplace-listings-listing-id/` | `system/marketplace-listings-listing_id/` | 重复 |
| `system/memory-strategy-history-strategy-id/` | (无) | 仅连字符版本 |
| `trade/backtests-task-id/` | `trade/backtests-task_id/` | 重复 |
| `trade/grid-markets-exchange/` | (无) | 仅连字符版本 |

**根因**:Next.js 动态路由用 `[param]` 方括号,不是连字符/下划线。这些文件夹似乎是从 OpenAPI 路径自动生成的,`{formula_id}` 被扁平化成 `formula-id` 和 `formula_id` 两种文件夹名。**每对中只有一个真正接到了 `ALL_PAGES`** —— 其他是孤儿路由,可能 404 或渲染陈旧内容。这些应当去重。

### 12.6 死 WS Hook(部分)

| Hook | 状态 | 备注 |
|---|---|---|
| `useTuningWs` | **边缘** | 仅被 `tuning-control-panel.tsx` 消费。不像其他 5 个 WS hook 那样在布局层挂载。如果调参页面没挂载该面板,hook 永远不会运行。 |

### 12.7 未使用的 `BACKEND_AUTO_PAGES` 引用

`lib/page-manifest.ts:19` 和 `scripts/gen-auto-pages.py` 仍引用 `BACKEND_AUTO_PAGES`,但数组本身已被移除。脚本已过期。

---

## 13. 架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            BROWSER (client)                                 │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Next.js App Router                                                   │  │
│  │  app/layout.tsx → ClientProviders                                     │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │  I18nProvider > AuthProvider > BackendReadyProvider             │  │  │
│  │  │                                                                 │  │  │
│  │  │  app/page.tsx         → LandingPage                             │  │  │
│  │  │  app/<scene>/<slug>/  → DashboardLayout > PageRenderer          │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  DashboardLayout (4-scene shell)                                      │  │
│  │                                                                       │  │
│  │  ┌─────────┬─────────┬─────────────────────────────────┬───────────┐ │  │
│  │  │ VHero   │ Sidebar │ main                            │ Bottom   │ │  │
│  │  │ (60px)  │ (240px) │ ┌─────────────────────────────┐ │ Drawer   │ │  │
│  │  │         │         │ │ SymbolBar                   │ │ + Agent  │ │  │
│  │  │ avatar  │ pages   │ ├─────────────────────────────┤ │ Fab +    │ │  │
│  │  │ asset   │ by      │ │ CtxBar                      │ │ FeedBar  │ │  │
│  │  │ risk    │ scene   │ ├─────────────────────────────┤ │          │ │  │
│  │  │ conn    │ +       │ │ scene-layout                │ │          │ │  │
│  │  │ time    │ watch-  │ │ ┌─────────────────────────┐ │ │          │ │  │
│  │  │ nav ×4  │ list    │ │ │ PageRenderer            │ │ │          │ │  │
│  │  │ manifest│         │ │ │ ┌─────────────────────┐ │ │ │          │ │  │
│  │  │ kill    │         │ │ │ │ WIDGET_REGISTRY    │ │ │ │          │ │  │
│  │  └─────────┴─────────┴─┤ │ │ (lazy × 43)        │ │ │ │          │ │  │
│  │                        │ │ │ OR                 │ │ │ │          │ │  │
│  │  SceneWsMount (inv.)   │ │ │ GenericRenderer    │ │ │ │          │ │  │
│  │  ├ useSceneWs          │ │ │ (symbol/research/  │ │ │ │          │ │  │
│  │  │ ├ useMarketsWs ─────┼─┼─┼─┤ list/chat)        │ │ │ │          │ │  │
│  │  │ └ useIntelligenceWs │ │ └─┼─────────────────────┘ │ │          │ │  │
│  │  ├ usePaperWs ─────────┼─┼───┼─→ usePaperStore       │ │          │ │  │
│  │  ├ useStrategyWs ──────┼─┼───┼─→ useStrategyStore    │ │          │ │  │
│  │  └ usePaperAccountTick │ │   │                       │ │          │ │  │
│  │  ConditionalTradingWs  │ │   │                       │ │          │ │  │
│  │  └ useTradingWs ───────┼─┼───┼─→ useLiveTradingStore │ │          │ │  │
│  └────────────────────────┴─┴───┴───────────────────────┴─┴──────────┘ │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Zustand Stores (9 total, 1 dead)                                     │  │
│  │  ┌──────────────┬─────────────┬──────────────┬────────────────────┐  │  │
│  │  │ MarketStore  │ TradingStore│ SceneStore   │ TradingModeStore   │  │  │
│  │  │ (symbol/tf)  │ (layout/tick│ (current     │ (paper/live,       │  │  │
│  │  │              │  /grid)     │  scene/page) │  localStorage)     │  │  │
│  │  └──────────────┴─────────────┴──────────────┴────────────────────┘  │  │
│  │  ┌──────────────┬──────────────┬──────────────┬──────────────────┐   │  │
│  │  │ PaperStore   │ LiveTrading  │ StrategyStore│ SubscriptionStore│   │  │
│  │  │ (positions/  │ Store        │ (signals)    │ (DEAD - 0 users) │   │  │
│  │  │  orders/     │ (positions/  │              │                  │   │  │
│  │  │  fills/      │  orders/     │              │                  │   │  │
│  │  │  equity)     │  balances)   │              │                  │   │  │
│  │  └──────────────┴──────────────┴──────────────┴──────────────────┘   │  │
│  │  Context aggregation: useContextStore → MarketStore + TradingStore    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Data Layer                                                           │  │
│  │                                                                       │  │
│  │  REST (SWR):                  WebSocket (raw):                        │  │
│  │  useApi(path) ─→ fetcher ─→   useMarketsWs    ─→ ws://.../ws/markets  │  │
│  │    ├ envelope unwrap          useIntelligenceWs ─→ ws://.../ws/intel  │  │
│  │    ├ bare endpoint detect     usePaperWs       ─→ ws://.../ws/paper   │  │
│  │    ├ 401 → refreshToken       useTradingWs     ─→ ws://.../ws/trading │  │
│  │    └ dedup 5s                 useStrategyWs    ─→ ws://.../ws/strategy│  │
│  │                                                                       │  │
│  │  api-client.ts (mutations):   WS features:                            │  │
│  │  apiPost/apiPut/apiDelete     ├ RAF batching (60fps max)              │  │
│  │    ├ in-flight GET dedup      ├ delayed unsub (5s grace)              │  │
│  │    ├ 401 → refreshToken       ├ REST polling fallback (degraded)      │  │
│  │    └ abortAllPendingGets      ├ stale detection (5s/10s)              │  │
│  │                               ├ seq gap detection → reconnect         │  │
│  │  lib/api/* (DEAD - 6 files)   ├ exponential backoff (1s→30s)          │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ Next.js rewrites
                                      │ /api/* → localhost:8088/api/*
                                      │ /ws/*  → localhost:8088/ws/*
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     LOCAL BACKEND (localhost:8088)                          │
│  FastAPI (services/local-api/)                                              │
│  ├ /api/v1/health          ← BackendReadyProvider polls this               │
│  ├ /api/v1/markets/...     ← REST ticker/orderbook/klines                  │
│  ├ /api/v1/intelligence/...← REST whale-flow/gex/etc                       │
│  ├ /api/v1/portfolio       ← VHero asset display                           │
│  ├ /api/v1/favorites       ← Sidebar watchlist                             │
│  ├ /api/v1/ws/markets      ← WebSocket (ticker/kline/depth)                │
│  ├ /api/v1/ws/intelligence ← WebSocket (news/events/pulse/alerts)          │
│  └ ... (40+ endpoints)                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 14. 关键架构决策与理由

### 14.1 页面清单作为唯一真相源

**决策**:所有 45 个页面定义在一个 `ALL_PAGES` 数组中,带元数据(`scene`、`renderMode`、`rest`、`ws`)。

**理由**:前端有 4 个场景 × 每场景 10+ 页面。没有中央注册表,页面元数据(REST 端点、WS 频道、渲染模式)会散落在 45 个路由文件中。清单让 `Sidebar` 遍历 `pagesByScene(scene)`、`PageRenderer` 按 `renderMode` 分发、`SymbolPanelPage` 从 `meta.rest` 构建 REST 路径 —— 全部来自一个源。

### 14.2 两级分发(Widget 注册表 + 通用渲染器)

**决策**:`PageRenderer` 先尝试 `WIDGET_REGISTRY[slug]`(懒加载特定组件),再按 `renderMode` 落到通用渲染器。

**理由**:不是每个页面都需要定制组件。4 个通用渲染器(symbol/research/list/chat)覆盖 80% 的情况(数据展示)。UI 丰富的页面(kf、rsch、pos)有特定组件;其余得到可用通用视图。这让新后端端点能立即出现在 UI 中 —— 只需加到 `BACKEND_INSERT_PAGES`,通用渲染器自动处理。

### 14.3 Store 切片(拆分巨型 context)

**决策**:原 `useContextStore` 被拆成 `useMarketStore` + `useTradingStore` + `useSubscriptionStore`。`useContextStore` 保留为聚合垫片。

**理由**(来自 `lib/store/context.ts` 注释):"原单 store 把市场/交易/订阅状态混在一起,任一字段变更触发所有消费者 re-render. 拆分后:symbol 切换只触发 market 消费者 re-render,tick 跳动只触发 trading 消费者." 有 33 个消费文件时,巨型 store 会在每次 tick 时大面积 re-render。切片隔离了 re-render 范围。

### 14.4 WS Hooks 在布局层(非每页)

**决策**:所有 WS hooks 在 `DashboardLayout` 层的 `SceneWsMount` 中一次性挂载。页面组件从 Zustand store 读取,不直接从 WS 读取。

**理由**:如果每页都挂载自己的 WS hook,页面间导航会拆掉并重建 socket —— 丢失连接状态、触发 1s 重连、丢失延迟退订宽限期。集中挂载让 socket 在页面切换间保持热状态。

### 14.5 高频消息的 RAF 批处理

**决策**:`useMarketsWs` 把消息累积到 `pendingBatchRef`,通过 `requestAnimationFrame` 刷新,而非逐条消息 `setState`。

**理由**:加密市场数据流 10-50 消息/秒。逐条 `setState` 会导致 10-50 次 re-render/秒,卡顿 UI。RAF 批处理合并成每帧 1 次 render(最多 60fps),React 工作量减少 10-50 倍。

### 14.6 延迟退订(5s 宽限)

**决策**:symbol 变化时,立即订阅新,但延迟 5s 退订旧。

**理由**:用户经常在 2-3 个 symbol 间切换(BTC → ETH → BTC)。立即退订会迫使切回时冷启动重连。5s 宽限窗口让短暂切换能复用热流。

### 14.7 降级状态下的 REST 轮询兜底

**决策**:WS `streamState` 变为 `"degraded"` 时,每 5s 轮询 REST ticker 端点。把响应作为合成 WS 消息发出。

**理由**:WS 连接可能静默断开(代理超时、网络切换)。与其显示陈旧数据,hook 退回到 REST 轮询。通过发出合成消息,消费者代码路径完全相同 —— 没有 "REST 模式" vs "WS 模式" 的分支。

### 14.8 带旁路的 BackendReady 门控

**决策**:`BackendReadyProvider` 在 `/api/v1/health` 返回 200 前阻塞应用渲染,但在 `/`(落地页)旁路。

**理由**:桌面应用在 Python 后端完成启动前就启动了。立即显示应用会导致 100% 拉取失败(每个 `useApi` 返回 null,每个页面显示"暂无数据")。门控等待后端就绪。落地页旁路让用户立即看到营销站点,"进入平台"按钮触发门控。

### 14.9 通用渲染器作为兜底(非默认)

**决策**:通用渲染器仅在 slug 不在 `WIDGET_REGISTRY` 时渲染。特定组件始终优先。

**理由**:通用渲染器刻意保持极简(原始键值展示)。它们的存在是为了让 `ALL_PAGES` 中的每个页面都能渲染出*有用的东西*,但它们不是期望的终态。随着特定组件被构建,它们逐个 slug 替换通用渲染器,而不破坏兜底。

### 14.10 `origin` 字段用于来源追踪

**决策**:`PageMeta.origin` 是 `"design"` 或 `"backend-insert"`,在通用渲染器中作为徽标显示。

**理由**:45 个页面来自两个来源(设计规范 vs 后端端点)。徽标让来源在 UI 中可见,帮助开发者识别哪些页面仍需设计工作,哪些是后端驱动。

### 14.11 实时 Store 的 `version` 计数器

**决策**:`usePaperStore`、`useLiveTradingStore`、`useStrategyStore` 都有 `version: number` 字段,每条改变状态的 WS 消息自增。

**理由**:需要对"任何变化"做出反应的组件(例如任何持仓更新时重算派生数据)可以把 `version` 选为依赖。这避免了选中整个 `positions` 数组(每次引用变化都 re-render),同时仍获得"有变化"的信号。

### 14.12 用 Next.js Rewrites 而非基于环境变量的 API Base

**决策**:`lib/api-client.ts` 用 `API_BASE = ""`(空字符串)。`next.config.ts` 把 `/api/*` 重写到 `localhost:8088`。

**理由**:空 `API_BASE` 意味着所有拉取路径都是相对的(`/api/v1/...`),在 dev(Next.js dev server 代理)和 prod(Tauri 同源服务)都能用。无需 env var 管理、无 CORS、无 `NEXT_PUBLIC_API_HOST` 漂移。WebSocket hooks 绕过这一点(它们硬编码 `ws://localhost:8088`)—— 一个小不一致。

---

## 附录:文件数量汇总

| 类别 | 数量 |
|---|---|
| 页面清单条目 | 45 |
| `WIDGET_REGISTRY` 懒加载组件 | 43+ |
| 通用渲染器 | 4 |
| Zustand store | 9(1 个死) |
| WS hook | 6(1 个边缘) |
| 布局组件 | 15+ |
| 落地页区块 | 10 |
| `lib/api/*` 死模块 | 7 |
| 重复路由文件夹 | 6+ 对 |

---

*本文档基于 2026-08-01 对 `apps/desktop` 代码库的详尽阅读生成。如需最新状态,请直接参考源文件。*
