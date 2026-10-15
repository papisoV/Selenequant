"use client";

import { useResize } from "@/lib/hooks/use-resize";
import { useIsNarrow } from "@/lib/hooks/use-media-query";

// 设计稿 L8446-8454 (vhero/mid 列宽) + L8456-8457 (drawer 高)
// 3 个 resize handle 绝对定位在列边界，mousedown 拖拽 + dblclick 重置
// handle 位置用 CSS 变量 calc，不依赖 React state（避免 SSR 不同步）

export function LayoutResizer() {
  const vhero = useResize({ target: "vhero-w", axis: "x", min: 40, max: 120, default: 56 });
  const mid = useResize({ target: "mid-w", axis: "x", min: 140, max: 320, default: 200 });
  // 窄屏 sidebar 列已折成覆盖层,handle 的 calc(vhero+mid) 会与 vhero 边界重叠。
  // CSS 已 display:none,但固定定位元素仍在 DOM:`title` tooltip 可悬停触达,
  // 且 mousedown 仍会把 --mid-w 写成内联 var(幽灵交互)→ 组件层直接不渲染。
  const isNarrow = useIsNarrow();

  if (isNarrow) return null;

  return (
    <>
      {/* vhero | sidebar 边界 */}
      <div
        className="layout-resizer resizer v x"
        style={{ left: "calc(var(--vhero-w) - 1px)", top: 0, bottom: 0 }}
        onMouseDown={vhero.onMouseDown}
        onDoubleClick={vhero.onDoubleClick}
        title="拖拽调整 VHero 宽度 · 双击重置"
      />
      {/* sidebar | main 边界 */}
      <div
        className="layout-resizer resizer v x"
        style={{ left: "calc(var(--vhero-w) + var(--mid-w) - 1px)", top: 0, bottom: 0 }}
        onMouseDown={mid.onMouseDown}
        onDoubleClick={mid.onDoubleClick}
        title="拖拽调整 Sidebar 宽度 · 双击重置"
      />
    </>
  );
}
