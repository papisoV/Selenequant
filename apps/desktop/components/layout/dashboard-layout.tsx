"use client";

import type { Scene } from "@/lib/page-manifest";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useIsNarrow } from "@/lib/hooks/use-media-query";
import { preloadAll } from "@/components/shared/page-renderer";
import { VHero } from "./vhero";
import { Sidebar } from "./sidebar";
import { SymbolBar } from "./symbol-bar";
import { CtxBar } from "./ctx-bar";
import { BottomDrawer } from "./bottom-drawer";
import { AgentFab } from "./agent-fab";
import { FeedBar } from "./feed-bar";
import { SceneWsMount, ConditionalTradingWsMount } from "./scene-ws-mount";
import { LayoutResizer } from "./layout-resizer";
import { RealtimeDebugPanel } from "./realtime-debug-panel";
import { WorkflowStepper } from "./workflow-stepper";
import { useUiText } from "@/components/i18n/use-ui-text";

interface Props {
  scene: Scene;
  children: React.ReactNode;
}

export function DashboardLayout({ scene, children }: Props) {
  const tr = useUiText();
  // Returning User 恢复:每次路由变化记最后位置,landing CTA 读之回到上次页面
  // /system 前缀不记——运维台是工作路过而非"上次的研究现场",回放会 landing 进运维页
  const pathname = usePathname();
  useEffect(() => {
    if (pathname.startsWith("/system")) return;
    try {
      window.localStorage.setItem("selenespace-last-page", pathname);
    } catch {
      // ignore storage error
    }
  }, [pathname]);
  // 首帧后空闲预载:chunk 全局(不发请求),数据按当前场景收窄 ——
  // 全量数据预载实测在 /research/today 上 100 秒打 66 个端点,其中 5 个属于本场景。
  useEffect(() => {
    preloadAll(scene);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Card hover mousemove tracking — 更新 --card-mx/--card-my CSS 变量
  // 驱动 [data-slot="card"]::before 的 radial-gradient 跟随光晕（设计稿微交互）
  // RAF 节流:mousemove 可达 100-250 次/秒,每帧最多执行一次 closest+layout,避免拖满主线程
  useEffect(() => {
    let rafId = 0;
    const onMove = (e: MouseEvent) => {
      if (rafId) return;
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        const target = (e.target as HTMLElement)?.closest(
          '[data-slot="card"]',
        ) as HTMLElement | null;
        if (!target) return;
        const rect = target.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * 100;
        const y = ((e.clientY - rect.top) / rect.height) * 100;
        target.style.setProperty("--card-mx", `${x}%`);
        target.style.setProperty("--card-my", `${y}%`);
      });
    };
    window.addEventListener("mousemove", onMove);
    return () => {
      window.removeEventListener("mousemove", onMove);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);

  // 窄屏覆盖层抽屉:sidebarOpen 只在窄屏由 toggle 置位(宽屏按钮 display:none 点不到)。
  // 刻意不上 localStorage —— 抽屉是临时覆盖层,不落盘避免"关掉后下次自动弹开"。
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const isNarrow = useIsNarrow();
  // 切回宽屏时强制收起,避免视口拉宽后 sidebarOpen 残留(下次再变窄会莫名弹开)
  useEffect(() => {
    if (!isNarrow) setSidebarOpen(false);
  }, [isNarrow]);
  // Esc 关闭 —— 覆盖层抽屉的键盘出口
  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);
  // 路由变化自动收起:点完抽屉里的导航链接后立刻关闭,不遮住刚打开的新页面
  useEffect(() => {
    setSidebarOpen(false);
  }, [pathname]);

  return (
    <div className="app-shell">
      <SceneWsMount />
      <ConditionalTradingWsMount />
      <LayoutResizer />
      <VHero />
      <RealtimeDebugPanel />
      <Sidebar scene={scene} drawerOpen={sidebarOpen} />
      {isNarrow && sidebarOpen && (
        <div
          className="sb-drawer-backdrop"
          aria-hidden="true"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <main className="main">
        {/* 窄屏导航开关:仅 ≤768px 由 CSS 显示(宽屏用既有 resizer 拖拽)。 */}
        <button
          type="button"
          className="sb-drawer-toggle"
          aria-expanded={sidebarOpen}
          aria-controls="app-sidebar"
          aria-label={sidebarOpen ? tr("关闭导航") : tr("打开导航")}
          onClick={() => setSidebarOpen((v) => !v)}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
          <span>{sidebarOpen ? tr("关闭导航") : tr("菜单")}</span>
        </button>
        <SymbolBar />
        <CtxBar scene={scene} />
        {(scene === "research" || scene === "trade") && <WorkflowStepper />}
        <div className="scene-layout">{children}</div>
        <BottomDrawer />
      </main>
      <AgentFab />
      <FeedBar />
    </div>
  );
}
