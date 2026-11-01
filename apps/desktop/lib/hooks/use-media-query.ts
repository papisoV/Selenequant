"use client";

import { useEffect, useState } from "react";

/**
 * 匹配 CSS 媒体查询并随视口变化更新。
 *
 * SSR 首帧返回 false(无 window),挂载后立刻用真实 matchMedia 校正 —— 与既有
 * `prefers-reduced-motion` 的写法一致(equity-curve/primitives 等)。
 *
 * ⚠️ 断点数值必须与 globals.css 的 `@media (max-width: 768px)` 保持同步:
 * 这里是 JS 侧的单一真源,组件用它决定"渲染抽屉态还是常驻 sidebar",
 * CSS 用同一条 query 决定"侧栏列折成覆盖层"。两边一旦漂移,
 * 就会出现"JS 认为是窄屏、CSS 认为不是"的错配(抽屉被 display:flex 撑在流内)。
 */
export const NARROW_QUERY = "(max-width: 768px)";

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia(query);
    setMatches(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** 窄屏(≤768px)。数值见 {@link NARROW_QUERY},须与 globals.css 同步。 */
export function useIsNarrow(): boolean {
  return useMediaQuery(NARROW_QUERY);
}
