"use client";

import { useEffect, useState } from "react";

// Returning User 恢复 — DashboardLayout 每次路由变化写 selenespace-last-page,
// landing CTA 读之:有记录→回到上次页面,无记录→fallback(研策驾驶舱)。
// localStorage 同步读,比 pageview IndexedDB(dwell 分析用)轻。
const KEY = "selenespace-last-page";

export function useLastPageHref(fallback: string): string {
  const [href, setHref] = useState(fallback);
  useEffect(() => {
    try {
      const last = window.localStorage.getItem(KEY);
      if (last && last.startsWith("/")) setHref(last);
    } catch {
      // ignore storage error
    }
  }, []);
  return href;
}
