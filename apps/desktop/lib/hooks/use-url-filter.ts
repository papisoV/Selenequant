"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * 列表页筛选状态 ↔ URL query 的双向绑定(刷新/分享/前进后退都还原筛选)。
 *
 * 为什么不用 next/navigation 的 router.replace:
 *  - 它会走一次真实导航,整棵页面树重渲染。搜索框每敲一个字都要写 URL,
 *    导航式的写法会把输入卡成幻灯片。
 *  - history.replaceState 不导航、不新增历史条目,只改地址栏。
 *  - 代价:replaceState 不会通知 useSearchParams(它只订阅 Next 自己的导航),
 *    所以这里自带一张模块级订阅表,变更时广播给同页所有消费者。
 *
 * 默认值不写进 URL:干净地址 = 默认态,书签和分享链接天然可用。
 * 服务端与 hydration 首帧一律读 fallback —— 与 useQueryParam 同一套
 * useSyncExternalStore 手法,水合不会打架。
 */

const listeners = new Set<() => void>();

function emit() {
  for (const fn of listeners) fn();
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  listeners.add(onChange);
  // 浏览器前进/后退换了 query:监听器要跟着变
  window.addEventListener("popstate", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("popstate", onChange);
  };
}

function readParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}

/** 写一个 query 参数;value 为 null / 空串时删除该参数。 */
export function setUrlParam(name: string, value: string | null): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  const next = value ?? "";
  if (next === (url.searchParams.get(name) ?? "")) return; // 没变就别惊动订阅者
  if (next === "") url.searchParams.delete(name);
  else url.searchParams.set(name, next);
  window.history.replaceState(
    window.history.state,
    "",
    url.pathname + url.search + url.hash,
  );
  emit();
}

/**
 * 读一个参数并归一化:
 * - 缺省 / 空串 → fallback
 * - allowed 给了且不在其中 → fallback(手改地址栏塞进来垃圾值时兜底)
 */
export function useUrlFilter<T extends string>(
  name: string,
  fallback: T,
  allowed?: readonly string[],
): [T, (value: T) => void] {
  const raw = useSyncExternalStore(
    subscribe,
    () => readParam(name),
    () => null,
  );

  const value = (
    raw !== null && raw !== "" && (!allowed || allowed.includes(raw))
      ? raw
      : fallback
  ) as T;

  const set = useCallback(
    (next: T) => setUrlParam(name, next === fallback ? null : next),
    [name, fallback],
  );

  return [value, set];
}
