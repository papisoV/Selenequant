"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Scene } from "@/lib/page-manifest";
import { pagesByScene, SCENE_DEFAULT_PAGE } from "@/lib/page-manifest";
import { useContextStore } from "@/lib/store/context";
import { useSymbolFavorites } from "@/lib/hooks/use-symbol-favorites";
import { useMarketsWs, type MarketMessage } from "@/lib/ws/use-markets-ws";
import { categoryFromScene, CATEGORY_LABELS, CATEGORY_COLORS } from "@/lib/data/page-categories";
import { preloadPage } from "@/components/shared/page-renderer";
import { CHAINS, moreSlugsOf, toolSlugsOf, type NavigationChain } from "@/lib/navigation-chains";
import { useWorkflowStore } from "@/lib/store/workflow";
import { clickable } from "@/lib/a11y/clickable";

interface Props {
  scene: Scene;
  /** 窄屏覆盖层抽屉是否展开。宽屏无效果(常驻列由 CSS 决定)。 */
  drawerOpen?: boolean;
}

const SCENE_LABEL: Record<Scene, string> = {
  watch: "盯盘",
  research: "研策",
  trade: "执行",
  system: "系统",
  workspace: "工作台",
};

// 双键快捷键提示 — 与 use-global-shortcuts.ts 保持同步
const SCENE_SHORTCUT_HINT: Record<Scene, string> = {
  watch: "G W",
  research: "G R",
  trade: "G T",
  system: "G S",
  workspace: "G K",
};

type WlTicker = { price?: number; chg?: number };

// 自选分组：按 quote 币种数据驱动（USDT/USDC/其他），组内字母序
// favorites 已由 hook 归一化为完整对（"BTC/USDT"）；裸符号兜底视为 /USDT
function groupByQuote(symbols: string[]): { name: string; items: string[] }[] {
  const usdt: string[] = [];
  const usdc: string[] = [];
  const other: string[] = [];
  for (const s of symbols) {
    const q = s.split("/")[1] ?? "USDT";
    if (q === "USDT") usdt.push(s);
    else if (q === "USDC") usdc.push(s);
    else other.push(s);
  }
  const sortAsc = (a: string, b: string) => a.localeCompare(b);
  return [
    { name: "USDT", items: [...usdt].sort(sortAsc) },
    { name: "USDC", items: [...usdc].sort(sortAsc) },
    { name: "其他", items: [...other].sort(sortAsc) },
  ].filter((g) => g.items.length > 0);
}

function fmtPrice(p: number): string {
  if (p >= 1000) return p.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (p >= 1) return p.toFixed(2);
  return p.toPrecision(4);
}

export function Sidebar({ scene, drawerOpen = false }: Props) {
  const pathname = usePathname();
  const pages = pagesByScene(scene);
  const chains = CHAINS[scene];
  const moreSlugs = moreSlugsOf(scene);
  // /api/v1/favorites 返回完整对；失败/空均 fallback 到空数组
  const { favorites } = useSymbolFavorites();
  // 分组折叠态持久化（key: more:<scene> / chain:<id> / tools:<scene> / wl:<组名>）——kf 刀5
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    if (typeof window === "undefined") return {};
    try {
      const raw = window.localStorage.getItem("selenespace-sidebar-collapsed");
      return raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
    } catch {
      return {};
    }
  });
  const { pageFilter, setSymBase } = useContextStore();

  // 自选区折叠（整区）+ 各分组折叠
  const [sectionCollapsed, setSectionCollapsed] = useState(false);
  // P1-2 overlay scrollbar：滚动时显示金色滚动条，停 1s 后淡出
  const [scrolling, setScrolling] = useState(false);
  const [hovering, setHovering] = useState(false);
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 自选 ticker：订阅完整对（singleton WS，favorites 变化走增量 sub/unsub），
  // 消息缓冲进 ref + 1s 节流 setState，避免高频 ticker 打爆 sidebar 重渲染
  const [tickers, setTickers] = useState<Record<string, WlTicker>>({});
  const tickerBufRef = useRef<Record<string, WlTicker>>({});
  const tickerDirtyRef = useRef(false);
  const onWsMessage = useCallback((msg: MarketMessage) => {
    if (msg.type !== "ticker") return;
    const sym = String(msg.data.symbol ?? "");
    if (!sym) return;
    tickerBufRef.current = {
      ...tickerBufRef.current,
      [sym]: {
        price: msg.data.last_price as number | undefined,
        chg: msg.data.change_24h_pct as number | undefined,
      },
    };
    tickerDirtyRef.current = true;
  }, []);
  useMarketsWs({
    symbols: favorites,
    exchange: "binance",
    marketType: "futures",
    interval: "1h",
    onMessage: onWsMessage,
  });
  useEffect(() => {
    const timer = setInterval(() => {
      if (!tickerDirtyRef.current) return;
      tickerDirtyRef.current = false;
      setTickers({ ...tickerBufRef.current });
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // collapsed 变化落盘（首帧跳过——初值即持久值，无需回写）
  const collapsedFirstRef = useRef(true);
  useEffect(() => {
    if (collapsedFirstRef.current) {
      collapsedFirstRef.current = false;
      return;
    }
    try {
      window.localStorage.setItem("selenespace-sidebar-collapsed", JSON.stringify(collapsed));
    } catch {
      // ignore storage error
    }
  }, [collapsed]);

  const onScroll = () => {
    setScrolling(true);
    if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current);
    scrollTimerRef.current = setTimeout(() => setScrolling(false), 1000);
  };

  useEffect(() => {
    return () => {
      if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      const key = e.key;
      // scene 根路由无 page.tsx,必须落具体 slug(⌘1-4 否则 404)
      const target = SCENE_DEFAULT_PAGE[key as keyof typeof SCENE_DEFAULT_PAGE];
      if (target) {
        e.preventDefault();
        window.location.href = `/${key === "1" ? "trade" : key === "2" ? "watch" : key === "3" ? "research" : "system"}/${target}`;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const toggleGroup = (name: string) =>
    setCollapsed((c) => ({ ...c, [name]: !c[name] }));

  // "更多"组默认折叠:undefined 视为 true(折叠);toggle 时按默认值翻转
  const moreCollapsed = collapsed[`more:${scene}`] ?? true;
  const toggleMore = () =>
    setCollapsed((c) => ({ ...c, [`more:${scene}`]: !(c[`more:${scene}`] ?? true) }));

  const selectWatchlist = (sym: string) => {
    const bare = sym.replace(/[-/].*$/, "");
    // setSymBase 同时同步 symBase 与 pageFilter.symbol,避免两个渲染源分叉
    // (setPageFilter({symbol}) 只改 pageFilter.symbol,kf/tof/grid/ctx-bar 等 symBase 源页面会停在旧币)
    setSymBase(bare);
  };

  const sceneCategory = categoryFromScene(scene);
  const sceneCategoryColor = CATEGORY_COLORS[sceneCategory];
  const sceneCategoryLabel = CATEGORY_LABELS[sceneCategory];

  // workflow 链 ✓ 状态 — 策略/因子/优化/回测/部署 从 workflow store 读
  const wf = useWorkflowStore();
  const wfDone: Record<string, boolean> = {
    strat: wf.template !== null,
    factors: wf.factors !== null,
    optimizer: wf.optimization !== null,
    scanner: wf.optimization !== null,
    tuning: wf.optimization !== null,
    backtests: wf.backtest !== null,
    paper: wf.backtest !== null,
    trd: wf.backtest !== null,
  };

  const renderStep = (
    p: { slug: string; title: string; shortcut?: string },
    stepNumber?: number,
  ) => {
    const href = `/${scene}/${p.slug}`;
    const active = pathname === href;
    const done = wfDone[p.slug];
    return (
      <Link
        key={p.slug}
        href={href}
        className={`page-item ${active ? "active" : ""}`}
        aria-current={active ? "page" : undefined}
        onMouseEnter={() => preloadPage(p.slug)}
      >
        <span className="flex items-center gap-1.5">
          {stepNumber !== undefined && <span className="wf-step-n">{stepNumber}</span>}
          <span className="dot" />
          {done && !active && <span className="wf-check">✓</span>}
          {p.title}
        </span>
        {p.shortcut && <span className="sc">{p.shortcut}</span>}
      </Link>
    );
  };

  const renderChain = (chain: NavigationChain) => {
    const isCollapsed = !!collapsed[`chain:${chain.id}`];
    const stepPages = chain.steps
      .map((slug) => pages.find((p) => p.slug === slug))
      .filter((p) => p !== undefined);
    return (
      <div key={chain.id}>
        <div
          className={`page-group-title nav-chain-title ${isCollapsed ? "collapsed" : ""}`}
          aria-expanded={!isCollapsed}
          {...clickable(() => toggleGroup(`chain:${chain.id}`))}
        >
          <span className={`page-group-cat-dot ${sceneCategoryColor.border.replace("border-", "bg-")}`} />
          <span>{chain.label}</span>
          {chain.workflow && <span className="wf-badge">流程</span>}
          <svg className="wl-group-arrow" viewBox="0 0 8 8" fill="none">
            <path d="M1 2.5 L4 5.5 L7 2.5" stroke="currentColor" strokeWidth="1.2" />
          </svg>
        </div>
        <div
          className={`chain-body ${isCollapsed ? "collapsed" : ""}`}
          style={{ maxHeight: isCollapsed ? 0 : `${stepPages.length * 30}px` }}
        >
          {stepPages.map((p, i) => renderStep(p, chain.workflow ? i + 1 : undefined))}
        </div>
      </div>
    );
  };

  const moreSlugsSet = new Set(moreSlugs);
  const morePages = pages.filter((p) => moreSlugsSet.has(p.slug));

  // ④ 工具室:专业工具页独立分组(watch 场景,候审制收敛后"更多"只剩嫌疑犯)
  const toolSlugsSet = new Set(toolSlugsOf(scene));
  const toolPages = pages.filter((p) => toolSlugsSet.has(p.slug));
  const toolsCollapsed = collapsed[`tools:${scene}`] ?? true;
  const toggleTools = () =>
    setCollapsed((c) => ({ ...c, [`tools:${scene}`]: !(c[`tools:${scene}`] ?? true) }));

  // 自选区内容（分组行 + ticker 填充）；分组数据驱动，价格/涨跌来自 markets WS
  const groups = useMemo(() => groupByQuote(favorites), [favorites]);
  const watchlistCount = favorites.length;

  const renderWatchlistSection = () => (
    <>
      <div
        className={`page-group-title wl-section-head ${sectionCollapsed ? "collapsed" : ""}`}
        aria-expanded={!sectionCollapsed}
        {...clickable(() => setSectionCollapsed((c) => !c))}
      >
        <span className="wl-section-title-text">自选 · 按市场</span>
        <span className="more-n">{watchlistCount}</span>
        <svg className="wl-group-arrow" viewBox="0 0 8 8" fill="none">
          <path d="M1 2.5 L4 5.5 L7 2.5" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      </div>
      {sectionCollapsed ? null : (
        <>
          {watchlistCount === 0 && (
            <div className="wl-empty">暂无自选品种 — kf 图表 ★ 加入</div>
          )}
          {groups.map((g) => {
            const isCollapsed = !!collapsed[`wl:${g.name}`];
            return (
              <div key={g.name} className="wl-group">
                <div
                  className={`wl-group-header ${isCollapsed ? "collapsed" : ""}`}
                  aria-expanded={!isCollapsed}
                  {...clickable(() => toggleGroup(`wl:${g.name}`))}
                >
                  <span>{g.name}</span>
                  <svg className="wl-group-arrow" viewBox="0 0 8 8" fill="none">
                    <path d="M1 2.5 L4 5.5 L7 2.5" stroke="currentColor" strokeWidth="1.2" />
                  </svg>
                </div>
                <div
                  className={`wl-group-body ${isCollapsed ? "collapsed" : ""}`}
                  style={{ maxHeight: isCollapsed ? 0 : `${g.items.length * 36}px` }}
                >
                  {g.items.map((pair) => {
                    const bare = pair.replace(/[-/].*$/, "");
                    const active = pageFilter.symbol === bare;
                    const t = tickers[pair];
                    const hasChg = typeof t?.chg === "number";
                    const up = (t?.chg ?? 0) >= 0;
                    const hasPrice = typeof t?.price === "number";
                    return (
                      <button
                        key={pair}
                        type="button"
                        onClick={() => selectWatchlist(pair)}
                        className={`wl-row ${active ? "active" : ""}`}
                        aria-current={active ? "true" : undefined}
                      >
                        <div className="flex items-center gap-1">
                          <span className={`wl-signal ${hasChg ? (up ? "up" : "down") : "signal"}`} />
                          <span className="wl-sym">{bare}</span>
                          <span className="wl-exch">{g.name}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <div className="flex flex-col items-end">
                            <span className="wl-price">{hasPrice ? fmtPrice(t!.price!) : "—"}</span>
                            <span className={`wl-chg ${up ? "pos" : "neg"}`}>
                              {hasChg ? `${up ? "+" : ""}${t!.chg!.toFixed(2)}%` : "—"}
                            </span>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </>
      )}
    </>
  );

  // 窄屏抽屉打开时把焦点移入其中(plan Step 2「打开时焦点入抽屉」)。
  // 用 prev 标记只在"关闭→打开"的沿触发一次,避免每次渲染都抢焦点。
  const asideRef = useRef<HTMLElement | null>(null);
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (drawerOpen && !wasOpenRef.current) asideRef.current?.focus();
    wasOpenRef.current = drawerOpen;
  }, [drawerOpen]);

  // watch 场景自选置顶（盯盘第一对象是自选），其他场景保持尾部
  const wlFirst = scene === "watch";

  return (
    <aside
      ref={asideRef}
      id="app-sidebar"
      tabIndex={-1}
      aria-label="侧栏导航"
      className={`sidebar flex h-full flex-col bg-surface ${drawerOpen ? "drawer-open" : ""}`}
    >
      <div className="sb-head">
        <span className="sb-title">{SCENE_LABEL[scene]}</span>
        <span className="sb-cnt">
          <span
            title="数据分类"
            className={`sb-category-tag ${sceneCategoryColor.border} ${sceneCategoryColor.text} ${sceneCategoryColor.bg}`}
          >
            {sceneCategoryLabel}
          </span>
          <span
            title="双键快捷键"
            style={{
              display: "inline-block",
              fontSize: 9,
              color: "var(--color-gold)",
              border: "1px solid var(--color-border-soft)",
              borderRadius: 3,
              padding: "0 4px",
              letterSpacing: 0.5,
              marginRight: 4,
            }}
          >
            {SCENE_SHORTCUT_HINT[scene]}
          </span>
          {pages.length} 页 · {watchlistCount} 自选
        </span>
      </div>
      <nav
        aria-label="页面导航"
        className={`sb-body ${scrolling || hovering ? "scrolling" : ""}`}
        onScroll={onScroll}
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
      >
        {wlFirst && renderWatchlistSection()}
        {chains.map(renderChain)}

        {toolPages.length > 0 && (
          <div className="more-group">
            <div
              className={`page-group-title more-title ${toolsCollapsed ? "collapsed" : ""}`}
              aria-expanded={!toolsCollapsed}
              {...clickable(toggleTools)}
            >
              <span className="more-count">工具</span>
              <span className={`page-group-cat-label ${sceneCategoryColor.text}`}>{sceneCategoryLabel}</span>
              <span className="more-n">{toolPages.length}</span>
              <svg className="wl-group-arrow" viewBox="0 0 8 8" fill="none">
                <path d="M1 2.5 L4 5.5 L7 2.5" stroke="currentColor" strokeWidth="1.2" />
              </svg>
            </div>
            <div
              className={`chain-body ${toolsCollapsed ? "collapsed" : ""}`}
              style={{ maxHeight: toolsCollapsed ? 0 : `${toolPages.length * 30}px` }}
            >
              {toolPages.map((p) => renderStep(p))}
            </div>
          </div>
        )}

        <div className="more-group">
          <div
            className={`page-group-title more-title ${moreCollapsed ? "collapsed" : ""}`}
            aria-expanded={!moreCollapsed}
            {...clickable(toggleMore)}
          >
            <span className="more-count">更多</span>
            <span className={`page-group-cat-label ${sceneCategoryColor.text}`}>{sceneCategoryLabel}</span>
            <span className="more-n">{morePages.length}</span>
            <svg className="wl-group-arrow" viewBox="0 0 8 8" fill="none">
              <path d="M1 2.5 L4 5.5 L7 2.5" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          </div>
          <div
            className={`chain-body ${moreCollapsed ? "collapsed" : ""}`}
            style={{ maxHeight: moreCollapsed ? 0 : `${morePages.length * 30}px` }}
          >
            {morePages.map((p) => renderStep(p))}
          </div>
        </div>

        {!wlFirst && renderWatchlistSection()}
      </nav>
    </aside>
  );
}
