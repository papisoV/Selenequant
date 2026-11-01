import { useSyncExternalStore } from "react";

/**
 * 读取当前 URL 的 query 参数（客户端首帧即得，且不触发 react-hooks/set-state-in-effect）。
 *
 * 用途：跨页深链，例如 kf 图表工具栏把当前品种带给回测页
 * （/trade/backtests?symbol=BTC%2FUSDT）。
 *
 * 走 useSyncExternalStore 而非 useState+useEffect：后者会在 effect 里同步 setState，
 * 命中本仓的 react-hooks/set-state-in-effect；且 getServerSnapshot 恒定返回 null，
 * 服务端渲染与客户端首帧（hydration）读到的都是 null，不会出现水合不一致。
 * popstate 订阅让浏览器前进/后退时参数跟着更新。
 */
function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}

export function useQueryParam(name: string): string | null {
  return useSyncExternalStore(
    subscribe,
    () => {
      if (typeof window === "undefined") return null;
      return new URLSearchParams(window.location.search).get(name);
    },
    () => null,
  );
}
