"use client";

import { useCallback, useEffect, useRef } from "react";
import { api, apiPut } from "@/lib/api-client";
import {
  applyMergedLayouts,
  listLayouts,
  mergeLayouts,
  readTombstones,
  subscribeLayouts,
  type LayoutSyncState,
  type LayoutTombstones,
  type WorkspaceLayout,
} from "@/lib/workspace/workspace-layouts";

/**
 * 工作台布局跨设备同步 —— 浏览器 localStorage 只是缓存,后端才是跨设备的真相。
 *
 * 流程:
 * 1. 启动:GET /settings → 取 workspace_layouts → 与本地合并 → 写回本地 + 推回后端。
 *    (合并结果可能含本地新增/更新的布局,必须推回,否则另一台设备看不到。)
 * 2. 运行期:任何布局变更(订阅 workspace-layouts 的广播)去抖 800ms 后整体 PUT。
 * 3. 后端不可用:静默降级为纯本地模式,不弹错 —— 布局是辅助功能,不该挡在看盘前面。
 *
 * 去抖而非逐次提交:拖拽/resize 会连续触发 saveLayout,逐次 PUT 等于把后端当
 * 事件日志使;整份集合天然幂等,最后一次写就够。
 */

const SYNC_DEBOUNCE_MS = 800;
const SETTINGS_URL = "/api/v1/settings";

interface SettingsResponse {
  workspace_layouts?: {
    layouts?: WorkspaceLayout[];
    tombstones?: LayoutTombstones;
  };
}

/** 模块级一次性锁:React 18 StrictMode 双挂载 / 多页面同时挂载只同步一次 */
let syncStarted = false;

/** 测试专用:重置同步锁(生产代码勿调) */
export function resetLayoutSyncForTest(): void {
  syncStarted = false;
}

function localState(): LayoutSyncState {
  return { layouts: listLayouts(), tombstones: readTombstones() };
}

async function pushState(state: LayoutSyncState): Promise<void> {
  await apiPut<unknown>(SETTINGS_URL, {
    workspace_layouts: { layouts: state.layouts, tombstones: state.tombstones },
  });
}

async function fetchRemoteState(): Promise<LayoutSyncState | null> {
  const data = await api<SettingsResponse>(SETTINGS_URL);
  const section = data?.workspace_layouts;
  if (!section) return null;
  return {
    layouts: Array.isArray(section.layouts) ? section.layouts : [],
    tombstones:
      section.tombstones && typeof section.tombstones === "object"
        ? section.tombstones
        : {},
  };
}

export function useWorkspaceLayoutSync(): void {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inflightRef = useRef(false);

  const schedulePush = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      // 上一次 PUT 还没回 → 丢弃这次;整份集合,丢了也不丢状态
      if (inflightRef.current) return;
      inflightRef.current = true;
      void pushState(localState())
        .catch(() => {
          // 后端不可用 = 纯本地模式;变更留在 localStorage,下次启动合并时再推
        })
        .finally(() => {
          inflightRef.current = false;
        });
    }, SYNC_DEBOUNCE_MS);
  }, []);

  useEffect(() => {
    // 订阅每次挂载都要建立(StrictMode 的第二次挂载也要),但首次拉取只做一次
    const unsubscribe = subscribeLayouts(schedulePush);

    if (syncStarted) {
      return () => {
        unsubscribe();
        if (timerRef.current) clearTimeout(timerRef.current);
      };
    }
    syncStarted = true;

    let cancelled = false;
    void (async () => {
      try {
        const remote = await fetchRemoteState();
        if (cancelled || remote === null) return;
        const merged = mergeLayouts(localState(), remote);
        applyMergedLayouts(merged);
        // applyMergedLayouts 已广播一次变更(去抖队列里会再发一次)。这里显式推一次
        // 是为了让首次合并的结果立刻落库 —— 另一台设备不该等下一次本地改动。
        await pushState(merged);
      } catch {
        // 后端不可用:保持本地状态不动
      }
    })();

    return () => {
      cancelled = true;
      unsubscribe();
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [schedulePush]);
}
