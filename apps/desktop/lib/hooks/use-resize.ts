"use client";

import { useCallback, useRef, useState } from "react";

// 设计稿 L8446-8454 列宽 / L8456-8457 drawer 高度拖拽 + dblclick 重置
// 拖拽中给 :root 加 .dragging class（L59 禁用 transition）
// 注意：只在 mousedown 后才写 CSS 变量，未拖拽时保留 :root 里的默认值（如 min(240px, 32vh)）

export interface ResizeSpec {
  target: "vhero-w" | "mid-w" | "drawer-h";
  axis: "x" | "y";
  min: number;
  max: number;
  default: number;
  invert?: boolean;  // drawer 向上拖，delta 反向
}

export function useResize(spec: ResizeSpec) {
  const [val, setVal] = useState(spec.default);
  const draggingRef = useRef(false);
  // 标记是否被拖拽过（未拖拽时不写 CSS 变量，保留 :root 默认 min() 表达式）
  const touchedRef = useRef(false);

  const applyCss = useCallback((v: number) => {
    const root = document.documentElement;
    root.style.setProperty(`--${spec.target}`, `${v}px`);
  }, [spec.target]);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    draggingRef.current = true;
    touchedRef.current = true;
    const start = spec.axis === "x" ? e.clientX : e.clientY;
    const startVal = val;
    document.documentElement.classList.add("dragging");

    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return;
      const cur = spec.axis === "x" ? ev.clientX : ev.clientY;
      let delta = cur - start;
      if (spec.invert) delta = -delta;
      let next = startVal + delta;
      next = Math.max(spec.min, Math.min(spec.max, next));
      setVal(next);
      applyCss(next);
    };
    const onUp = () => {
      draggingRef.current = false;
      document.documentElement.classList.remove("dragging");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    // 鼠标拖出窗口 / alt-tab / 浏览器取消指针时也必须清理,否则 :root.dragging 残留
    // 会导致全局 transition 被禁(globals.css .dragging),整个 app 看起来像僵住
    window.addEventListener("blur", onUp);
    window.addEventListener("pointercancel", onUp);
  }, [spec.axis, spec.invert, spec.min, spec.max, val, applyCss]);

  const onDoubleClick = useCallback(() => {
    touchedRef.current = true;
    setVal(spec.default);
    applyCss(spec.default);
  }, [spec.default, applyCss]);

  return { val, onMouseDown, onDoubleClick };
}
