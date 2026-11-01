"use client";

import { useEffect, useRef, useState } from "react";
import { AgentChatPanel } from "./agent-chat/agent-chat-panel";
import { useUiText } from "@/components/i18n/use-ui-text"

// 设计稿 L3570-3571 Agent Chat FAB 展开/关闭
// FAB onClick → toggle expanded state
// 展开时弹出聊天面板（复用 AgentChatPanel 渲染逻辑）
// 支持拖拽：mousedown + mousemove，位移 < 5px 视为点击

export function AgentFab() {
  const tr = useUiText()

  const [expanded, setExpanded] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const dragState = useRef<{
    startX: number;
    startY: number;
    fabX: number;
    fabY: number;
    moved: boolean;
    dragging: boolean;
  } | null>(null);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const onMouseDown = (e: React.MouseEvent) => {
    const fab = e.currentTarget as HTMLElement;
    const rect = fab.getBoundingClientRect();
    dragState.current = {
      startX: e.clientX,
      startY: e.clientY,
      fabX: rect.left,
      fabY: rect.top,
      moved: false,
      dragging: true,
    };
    document.body.classList.add("dragging");
    e.preventDefault();
  };

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      const st = dragState.current;
      if (!st || !st.dragging) return;
      const dx = e.clientX - st.startX;
      const dy = e.clientY - st.startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) st.moved = true;
      const newX = st.fabX + dx;
      const newY = st.fabY + dy;
      const fabW = 44;
      const fabH = 44;
      const clampedX = Math.max(4, Math.min(window.innerWidth - fabW - 4, newX));
      const clampedY = Math.max(4, Math.min(window.innerHeight - fabH - 4, newY));
      setPos({ x: clampedX, y: clampedY });
    };
    const onMouseUp = () => {
      const st = dragState.current;
      if (st && !st.moved) {
        setExpanded((v) => !v);
      }
      if (st) st.dragging = false;
      dragState.current = null;
      document.body.classList.remove("dragging");
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  const fabStyle: React.CSSProperties = pos.x === 0 && pos.y === 0
    ? {}
    : { left: pos.x, top: pos.y, right: "auto", bottom: "auto" };

  // 面板自适应展开方向：根据 FAB 在视口的位置选择上下左右
  // 面板尺寸 320×420，FAB 44×44，间距 8
  const PANEL_W = 320;
  const PANEL_H = 420;
  const FAB = 44;
  const GAP = 8;

  const panelStyle: React.CSSProperties = (() => {
    if (pos.x === 0 && pos.y === 0) {
      // 默认位置：CSS right:20 bottom:340
      return {};
    }
    const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
    const vh = typeof window !== "undefined" ? window.innerHeight : 720;

    // 水平：FAB 中心在右半屏 → 面板向左展开（right 锚定到 FAB 右边）
    const fabCenterX = pos.x + FAB / 2;
    const openLeft = fabCenterX > vw / 2;

    // 垂直：FAB 中心在下半屏 → 面板向上展开（bottom 锚定到 FAB 顶）
    const fabCenterY = pos.y + FAB / 2;
    const openUp = fabCenterY > vh / 2;

    const style: React.CSSProperties = { right: "auto", bottom: "auto" };

    if (openLeft) {
      // 面板右边贴 FAB 左边 - 8px gap，但不超出视口左侧
      const right = vw - pos.x - FAB - GAP;
      style.right = Math.max(4, right);
      style.left = "auto";
    } else {
      // 面板左边贴 FAB 右边 + 8px gap，但不超出视口右侧
      const left = pos.x + FAB + GAP;
      style.left = Math.min(left, vw - PANEL_W - 4);
      style.right = "auto";
    }

    if (openUp) {
      // 面板底边贴 FAB 顶 - 8px gap，但不超出视口顶部
      const bottom = vh - pos.y - GAP;
      style.bottom = Math.max(4, bottom);
      style.top = "auto";
    } else {
      // 面板顶边贴 FAB 底 + 8px gap，但不超出视口底部
      const top = pos.y + FAB + GAP;
      style.top = Math.min(top, vh - PANEL_H - 4);
      style.bottom = "auto";
    }

    return style;
  })();

  return (
    <>
      <div
        className="agent-fab"
        role="complementary"
        aria-label={tr("Agent 助手")}
        title={tr("Agent 助手 (可拖动)")}
        onMouseDown={onMouseDown}
        style={fabStyle}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 2a10 10 0 0 1 10 10c0 5.52-4.48 10-10 10H7l-3 3v-4.5A9.96 9.96 0 0 1 2 12C2 6.48 6.48 2 12 2z" />
          <circle cx="8" cy="12" r="1" fill="currentColor" />
          <circle cx="12" cy="12" r="1" fill="currentColor" />
          <circle cx="16" cy="12" r="1" fill="currentColor" />
        </svg>
      </div>
      {expanded && (
        <div className="agent-fab-panel" style={panelStyle}>
          <AgentChatPanel onClose={() => setExpanded(false)} />
        </div>
      )}
    </>
  );
}
