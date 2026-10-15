"use client";

import { useRef, useState } from "react";
import {
  GRID_TRACK_MIN,
  type PanelId,
  type WorkspaceLayout,
} from "@/lib/workspace/workspace-layouts";
import { PANEL_LABELS, PANEL_REGISTRY, renderPanel } from "./panel-registry";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Props {
  layout: WorkspaceLayout;
  /** 拖拽重排回调,父组件同步 state + 持久化 */
  onLayoutChange?: (next: WorkspaceLayout) => void;
  /** panel 类型切换回调 */
  onSlotChange?: (idx: number, newId: PanelId) => void;
  /** panel resize 回调(w/h 像素值) */
  onSlotResize?: (idx: number, w: number, h: number) => void;
}

/**
 * WorkspaceGrid — CSS grid 容器,根据 layout.columns 渲染 panel
 *
 * P7: HTML5 DnD 拖拽重排 + Select 类型切换 + grid 跨度 resize
 * - 每个 panel 顶部 .workspace-panel-header(拖拽手柄 + 类型切换 Select)
 * - dragStart 记录 source idx,dragOver/drop 重排 panels 数组
 * - resize 后用 grid-column: span N(N = floor(w / GRID_TRACK_MIN) clamp 1-3)
 * - onLayoutChange/onSlotChange/onSlotResize 通知父组件,父组件 debounce 500ms saveLayout
 *
 * 不用 react-grid-layout,纯 CSS grid + HTML5 DnD。
 * 列数 1-3 可配,小屏自动 1 列(minmax(480px, 1fr))。
 */
export function WorkspaceGrid({ layout, onLayoutChange, onSlotChange, onSlotResize }: Props) {
  const columns = Math.min(Math.max(layout.columns ?? 2, 1), 3);
  const [dragSourceIdx, setDragSourceIdx] = useState<number | null>(null);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);
  // 面板稳定 key:首次渲染给 slot 对象盖 __instId。父组件 resize/类型切换用 {...slot, w, h}
  // 克隆生成新对象 → __instId 随 spread 保留 → key 不变,面板不重挂丢图表/滚动/订阅状态;
  // 类型切换虽保留 key,但 renderPanel(slot) 换成新组件类型,旧状态自然重置(符合"仅类型切换/新建才 remount")。
  // 不用 slot.id(同类型多面板撞 key);不用 idx(重排改 key 全量 remount 闪烁)。
  const nextInstId = useRef(0);

  function handleDragStart(idx: number) {
    return (e: React.DragEvent) => {
      setDragSourceIdx(idx);
      e.dataTransfer.effectAllowed = "move";
      // Firefox 需要 setData 才能触发 drag
      e.dataTransfer.setData("text/plain", String(idx));
    };
  }

  function handleDragOver(idx: number) {
    return (e: React.DragEvent) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (dragSourceIdx !== null && idx !== dragSourceIdx && dragOverIdx !== idx) {
        setDragOverIdx(idx);
      }
    };
  }

  function handleDragLeave(idx: number) {
    return () => {
      if (dragOverIdx === idx) setDragOverIdx(null);
    };
  }

  function handleDrop(idx: number) {
    return (e: React.DragEvent) => {
      e.preventDefault();
      if (dragSourceIdx === null || dragSourceIdx === idx) {
        setDragSourceIdx(null);
        setDragOverIdx(null);
        return;
      }
      const nextPanels = [...layout.panels];
      const [moved] = nextPanels.splice(dragSourceIdx, 1);
      nextPanels.splice(idx, 0, moved);
      onLayoutChange?.({ ...layout, panels: nextPanels });
      setDragSourceIdx(null);
      setDragOverIdx(null);
    };
  }

  function handleDragEnd() {
    setDragSourceIdx(null);
    setDragOverIdx(null);
  }

  /** resize 后读 boundingClientRect 通知父组件 */
  function handleMouseUp(idx: number) {
    return (e: React.MouseEvent) => {
      const rect = e.currentTarget.getBoundingClientRect();
      if (!onSlotResize) return;
      const slot = layout.panels[idx];
      const wChanged = slot.w !== undefined && Math.abs(slot.w - rect.width) > 1;
      const hChanged = slot.h !== undefined && Math.abs(slot.h - rect.height) > 1;
      if (wChanged || hChanged) {
        onSlotResize(idx, Math.round(rect.width), Math.round(rect.height));
      }
    };
  }

  return (
    <div
      className="workspace-grid"
      style={{
        gridTemplateColumns: `repeat(auto-fit, minmax(${GRID_TRACK_MIN}px, 1fr))`,
        ["--ws-columns" as string]: columns,
      }}
      data-ws-columns={columns}
    >
      {layout.panels.map((slot, idx) => {
        const isDragSource = dragSourceIdx === idx;
        const isDragOver = dragOverIdx === idx;
        const stamped = slot as typeof slot & { __instId?: string };
        const instKey = stamped.__instId ?? (stamped.__instId = `inst-${nextInstId.current++}`);
        // v4: resize 用 grid 跨度,不脱离 grid 流
        const spanCols = slot.w
          ? Math.min(Math.max(Math.floor(slot.w / GRID_TRACK_MIN), 1), 3)
          : undefined;
        const panelStyle: React.CSSProperties = {
          ...(spanCols ? { gridColumn: `span ${spanCols}` } : {}),
          ...(slot.h ? { minHeight: slot.h } : {}),
        };
        return (
          <div
            key={instKey}
            className="workspace-panel"
            data-panel-id={slot.id}
            data-drag-source={isDragSource ? "true" : undefined}
            data-drag-over={isDragOver ? "true" : undefined}
            style={panelStyle}
            onDragOver={handleDragOver(idx)}
            onDragLeave={handleDragLeave(idx)}
            onDrop={handleDrop(idx)}
            onMouseUp={handleMouseUp(idx)}
          >
            <div
              className="workspace-panel-header"
              draggable
              onDragStart={handleDragStart(idx)}
              onDragEnd={handleDragEnd}
              title="拖动以重排面板"
            >
              <span className="workspace-panel-grip" aria-hidden="true">⠿</span>
              <span className="workspace-panel-title">
                {PANEL_LABELS[slot.id] ?? slot.id}
              </span>
              <Select
                value={slot.id}
                onValueChange={(v) => onSlotChange?.(idx, v as PanelId)}
              >
                <SelectTrigger
                  className="workspace-panel-type-switch"
                  aria-label="切换 panel 类型"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(PANEL_REGISTRY) as PanelId[]).map((pid) => (
                    <SelectItem key={pid} value={pid}>
                      {PANEL_LABELS[pid]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="workspace-panel-body">
              {renderPanel(slot)}
            </div>
          </div>
        );
      })}
    </div>
  );
}
