"use client";

import { useEffect, useRef, useState } from "react";

// 周期 → 时长(ms)。键集 = KfChart 两种 tf 来源的并集:pa-panel SUPPORTED_TFS
// {1m,5m,15m,30m,1h,4h,1d} ∪ symbol-bar TFS {1m,5m,15m,1h,4h,1d,1w} —— 缺任一个,
// 该周期上倒计时就静默消失。未收录的周期不猜数字 → closeAtFrom 返回 null → 不渲染。
// (别跟着 lib/hooks/use-klines.ts 的 getIntervalMs 学 `?? 60_000` 兜底——那是猜。)
const INTERVAL_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000,
  "30m": 1_800_000,
  "1h": 3_600_000,
  "4h": 14_400_000,
  "1d": 86_400_000,
  "1w": 604_800_000,
};

/**
 * 末根 K 线开盘时刻(ms)+ 周期 → 该根收盘时刻(ms)。缺任一或非有限数 → null(不编造)。
 *
 * 单位铁律:lastBarTsMs 必须是**毫秒**(与 klines.db timestamp 列、summary.window_end_iso
 * 同源)。KfRow.time 是 epoch **秒**(kf-chart 的 toRows 已除过 1000),传入前须 ×1000。
 * 传错单位不报错:closeAt 会落到过去 → 倒计时 remaining<=0 → 组件返回 null → 静默不显示。
 */
export function closeAtFrom(
  lastBarTsMs: number | null | undefined,
  interval: string | null | undefined,
): number | null {
  if (typeof lastBarTsMs !== "number" || !Number.isFinite(lastBarTsMs)) return null;
  const intervalMs = interval ? INTERVAL_MS[interval] : undefined;
  if (!intervalMs) return null;
  return lastBarTsMs + intervalMs;
}

export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

// 倒计时归零后返回 null(已收盘就不报时)。now 状态自持:挂载时初始化 + setInterval 回调内
// setState(满足 react-hooks/set-state-in-effect 与 purity:渲染体内不调 Date.now)。
// 调用方按 closeAt 作 key 重挂载,新 bar 形成时重新起算。
// autoSubmit 开启时,归零在 interval 回调内触发 onElapsed 一次(firedRef 防重复)。
export function CloseCountdown({
  closeAt,
  autoSubmit = false,
  onElapsed,
}: {
  closeAt: number;
  autoSubmit?: boolean;
  onElapsed?: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  const onElapsedRef = useRef(onElapsed);
  useEffect(() => {
    onElapsedRef.current = onElapsed;
  }, [onElapsed]);
  const firedRef = useRef(false);
  useEffect(() => {
    const id = window.setInterval(() => {
      const next = Date.now();
      setNow(next);
      if (autoSubmit && !firedRef.current && closeAt - next <= 0) {
        firedRef.current = true;
        onElapsedRef.current?.();
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, [autoSubmit, closeAt]);
  const remainingMs = Math.max(0, closeAt - now);
  if (remainingMs <= 0) return null;
  return (
    <p className="text-xs text-muted-foreground">
      {formatCountdown(remainingMs)} 后收盘（{new Date(closeAt).toLocaleTimeString()}）
    </p>
  );
}
