"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ensurePresetsSeeded,
  getLastUsedLayoutId,
  listLayouts,
  loadLayout,
  type WorkspaceLayout,
} from "@/lib/workspace/workspace-layouts";
import { WorkspaceLayoutManager } from "@/components/workspace/workspace-layout-manager";

export default function WorkspaceHomePage() {
  const router = useRouter();
  const [layouts, setLayouts] = useState<WorkspaceLayout[]>([]);
  const [mounted, setMounted] = useState(false);
  const [redirected, setRedirected] = useState(false);

  const refresh = useCallback(() => {
    setLayouts(listLayouts());
  }, []);

  useEffect(() => {
    ensurePresetsSeeded();
    refresh();
    setMounted(true);
    // 记住上次布局:有 lastUsed 且该布局仍存在 → 跳转
    const lastId = getLastUsedLayoutId();
    if (lastId && loadLayout(lastId)) {
      setRedirected(true);
      router.replace(`/workspace/${lastId}`);
    }
  }, [refresh, router]);

  if (!mounted || redirected) {
    return (
      <div className="flex h-full items-center justify-center text-ink-4 text-sm">
        加载中…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl p-8">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold text-ink-1">Workspace</h1>
        <p className="mt-2 text-sm text-ink-3">
          多面板工作台 — 同屏显示研究 / 下单 / 监控,避免频繁切换 sidebar
        </p>
      </header>

      <section>
        <h2 className="mb-4 text-sm font-medium text-ink-2">已存布局</h2>
        {layouts.length === 0 ? (
          <p className="text-sm text-ink-4">暂无布局</p>
        ) : (
          <WorkspaceLayoutManager layouts={layouts} onChange={refresh} />
        )}
      </section>
    </div>
  );
}
