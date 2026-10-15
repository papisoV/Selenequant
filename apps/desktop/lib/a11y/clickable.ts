"use client";

import type { KeyboardEvent } from "react";

/**
 * 让非语义元素(div/span)获得键盘可达性。
 *
 * 站点里大量交互控件是 `<div onClick>`:鼠标能用,键盘完全够不着(WCAG 2.1.1 失败)。
 * 全站已统一 `:focus-visible` 金 outline(globals.css 末尾),所以补上 role/tabIndex 后
 * 焦点环自动出现,无需额外样式。
 *
 * 用法:`<div className="sb-int" {...clickable(() => setTf(tf))}>{tf}</div>`
 *
 * 不改成 `<button>` 是刻意的:这些类名带着大量既有 CSS(display:flex / 定宽高 / 无边框),
 * 换标签要逐个重置浏览器默认样式,回归面远大于收益。角色靠 ARIA 表达,行为不变。
 */
export function clickable(onClick?: () => void, role: "button" | "switch" = "button") {
  return {
    role,
    tabIndex: 0,
    onClick,
    onKeyDown: (e: KeyboardEvent) => {
      // 空格键要拦默认行为,否则页面会跟着向下滚。
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onClick?.();
      }
    },
  };
}
