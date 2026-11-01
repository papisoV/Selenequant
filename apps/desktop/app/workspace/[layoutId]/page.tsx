"use client";

import { notFound, useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ensurePresetsSeeded,
  loadLayout,
  saveLayout,
  setLastUsedLayoutId,
  type PanelId,
  type WorkspaceLayout,
} from "@/lib/workspace/workspace-layouts";
import { WorkspaceGrid } from "@/components/workspace/workspace-grid";
import { useUiText } from "@/components/i18n/use-ui-text"

export default function WorkspaceLayoutPage() {
  const tr = useUiText()

  const params = useParams<{ layoutId: string }>();
  const layoutId = params?.layoutId;
  const [layout, setLayout] = useState<WorkspaceLayout | null>(null);
  const [loading, setLoading] = useState(true);
  // P7: 500ms debounce 持久化,避免拖拽/resize/切换每次都写 localStorage
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    ensurePresetsSeeded();
    if (!layoutId) {
      setLoading(false);
      return;
    }
    const loaded = loadLayout(layoutId);
    setLayout(loaded);
    // 记住上次访问的布局 id(仅当布局存在时写入,避免 notFound 跳转污染 lastUsed)
    if (loaded) {
      setLastUsedLayoutId(layoutId);
    }
    setLoading(false);
  }, [layoutId]);

  // 卸载时清定时器,避免内存泄漏
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  /** 通用持久化:setLayout 同步 + saveLayout debounce 500ms */
  const persistLayout = useCallback((next: WorkspaceLayout) => {
    setLayout(next);
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      saveLayout(next);
    }, 500);
  }, []);

  /** 拖拽重排 */
  const handleLayoutChange = useCallback((next: WorkspaceLayout) => {
    persistLayout(next);
  }, [persistLayout]);

  /** panel 类型切换 */
  const handleSlotChange = useCallback((idx: number, newId: PanelId) => {
    if (!layout) return;
    const nextPanels = [...layout.panels];
    nextPanels[idx] = { ...nextPanels[idx], id: newId };
    persistLayout({ ...layout, panels: nextPanels });
  }, [layout, persistLayout]);

  /** panel resize(w/h 像素值) */
  const handleSlotResize = useCallback((idx: number, w: number, h: number) => {
    if (!layout) return;
    const nextPanels = [...layout.panels];
    nextPanels[idx] = { ...nextPanels[idx], w, h };
    persistLayout({ ...layout, panels: nextPanels });
  }, [layout, persistLayout]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-ink-4 text-sm">
        {tr("加载中…")}
      </div>
    );
  }

  if (!layout) {
    notFound();
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-6 py-3">
        <div className="flex items-center gap-3">
          <Link
            href="/workspace"
            className="text-sm text-ink-3 hover:text-ink-1"
          >
            ← Workspace
          </Link>
          <span className="text-ink-4">/</span>
          <h1 className="text-sm font-medium text-ink-1">{tr(layout.name)}</h1>
        </div>
        <span className="text-xs text-ink-4">{layout.panels.length} {tr("面板")} {tr("· 拖拽重排 · 右下角 resize")}</span>
      </header>
      <div className="flex-1 overflow-auto p-4">
        <WorkspaceGrid
          layout={layout}
          onLayoutChange={handleLayoutChange}
          onSlotChange={handleSlotChange}
          onSlotResize={handleSlotResize}
        />
      </div>
    </div>
  );
}
