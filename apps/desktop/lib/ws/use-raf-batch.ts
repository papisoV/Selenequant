"use client";

import { useCallback, useEffect, useRef } from "react";

// RAF 批处理工具（参考旧代码 use-market-ws.ts:91-144 的 flushRAF + scheduleRAF 模式）
// 高频 WS 消息（ticker/orderbook/kline）积到 requestAnimationFrame 一次回调，避免逐条 setState re-render
//
// 用法：
//   const { schedule, flush } = useRafBatch<TItem>((items) => {
//     // 一帧内积累的 items 一次性处理
//     setMergedData(items);
//   });
//   ws.onmessage = (ev) => {
//     const item = parse(ev.data);
//     schedule(item);  // 积到下一帧
//   };

export function useRafBatch<T>(
  onFlush: (items: T[]) => void,
): {
  schedule: (item: T) => void;
  flush: () => void;
  cancel: () => void;
} {
  const onFlushRef = useRef(onFlush);
  onFlushRef.current = onFlush;
  const pendingRef = useRef<T[]>([]);
  const rafIdRef = useRef<number | null>(null);

  const flush = useCallback(() => {
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    const items = pendingRef.current;
    pendingRef.current = [];
    if (items.length === 0) return;
    onFlushRef.current(items);
  }, []);

  const schedule = useCallback((item: T) => {
    pendingRef.current.push(item);
    if (rafIdRef.current === null) {
      rafIdRef.current = requestAnimationFrame(() => {
        rafIdRef.current = null;
        const items = pendingRef.current;
        pendingRef.current = [];
        if (items.length > 0) {
          onFlushRef.current(items);
        }
      });
    }
  }, []);

  const cancel = useCallback(() => {
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    pendingRef.current = [];
  }, []);

  // 卸载时清理
  useEffect(() => {
    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
      pendingRef.current = [];
    };
  }, []);

  return { schedule, flush, cancel };
}
