"use client";

import { useEffect, useState } from "react";

// 定时轮询一个 JSON 端点(行为: 立即拉一次 + 每 intervalMs 再拉;
// 失败把 error 设成消息串)。从 market-regime-indicator 的内联 effect 抽出。

export interface PolledJson<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/**
 * 轮询 `url`(取 `body.data ?? body`)。`url` 为 null 时清空 data 且不轮询。
 */
export function usePolledJson<T>(url: string | null, intervalMs: number): PolledJson<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setData(null);
      return;
    }
    const fetchOnce = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = await res.json();
        setData((body?.data ?? body) as T);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    };
    void fetchOnce();
    const id = setInterval(fetchOnce, intervalMs);
    return () => clearInterval(id);
  }, [url, intervalMs]);

  return { data, loading, error };
}
