"use client";

import { useWorkspaceLayoutSync } from "@/lib/hooks/use-workspace-layout-sync";

/**
 * 无渲染的同步挂载点。
 *
 * 放在 workspace 的 layout.tsx 里而不是首页:布局编辑实际发生在
 * /workspace/[layoutId](拖拽/resize 走那边的 debounce saveLayout),
 * 只在首页挂载会让「编辑完直接刷新离开」的那批改动永远推不到后端。
 */
export function WorkspaceLayoutSync() {
  useWorkspaceLayoutSync();
  return null;
}
