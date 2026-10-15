"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ensurePresetsSeeded,
  loadLayout,
  type WorkspaceLayout,
} from "@/lib/workspace/workspace-layouts";
import { WorkspaceGrid } from "@/components/workspace/workspace-grid";

/**
 * /workspace/signal — 信号 → 下单 预设 workspace
 *
 * Query params:
 * - symbol: BTCUSDT / BTC-USDT-SWAP / BTC/USDT 等(原样传入 OrderForm)
 * - source: gex / liqmap / sig / kf / vchart(决定左侧信号源 panel)
 *
 * 布局:左 watch 信号源,右 order_form(已填 symbol)
 * 固定用 "signal" 预设布局(不读 layoutId)
 */
const SOURCE_TO_PANEL: Record<string, "watch_vchart" | "watch_gex" | "watch_liqmap" | "watch_sig" | "watch_kf"> = {
  vchart: "watch_vchart",
  gex: "watch_gex",
  liqmap: "watch_liqmap",
  sig: "watch_sig",
  kf: "watch_kf",
};

export default function WorkspaceSignalPage() {
  const searchParams = useSearchParams();
  const [layout, setLayout] = useState<WorkspaceLayout | null>(null);
  const [loading, setLoading] = useState(true);

  const symbol = searchParams?.get("symbol") || "BTC/USDT";
  const source = searchParams?.get("source") || "vchart";

  useEffect(() => {
    ensurePresetsSeeded();
    setLayout(loadLayout("signal"));
    setLoading(false);
  }, []);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-ink-4 text-sm">
        加载中…
      </div>
    );
  }

  if (!layout) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <p className="text-sm text-ink-3">未找到 signal 预设布局</p>
        <Link href="/workspace" className="text-sm text-ink-2 hover:text-ink-1">
          ← 返回 Workspace 首页
        </Link>
      </div>
    );
  }

  // 根据 source 动态调整左侧 panel(若 layout 有 watch_* panel,替换为 source 对应的)
  const sourcePanelId = SOURCE_TO_PANEL[source] ?? "watch_vchart";
  const adjustedLayout: WorkspaceLayout = {
    ...layout,
    panels: layout.panels.map((p) => {
      if (p.id.startsWith("watch_") && p.id !== sourcePanelId) {
        return { id: sourcePanelId };
      }
      if (p.id === "order_form") {
        return { ...p, props: { ...p.props, symbol } };
      }
      return p;
    }),
  };

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
          <h1 className="text-sm font-medium text-ink-1">{layout.name}</h1>
          <span className="text-xs text-ink-4">
            · symbol={symbol} · source={source}
          </span>
        </div>
      </header>
      <div className="flex-1 overflow-auto p-4">
        <WorkspaceGrid layout={adjustedLayout} />
      </div>
    </div>
  );
}
