"use client";

import { useEffect, useState } from "react";
import { getHeartbeatStatus, type HeartbeatStatus } from "@/lib/api/decision-workflow";
import { cn } from "@/lib/utils";

// 数据每 60s 拉一次(状态本身分钟级变化);倒计时每秒本地重算,不靠轮询驱动。
const POLL_MS = 60_000;
const TICK_MS = 1_000;

function fmtRemaining(ms: number): string {
  if (ms <= 0) return "窗口已开";
  const total = Math.floor(ms / 1000);
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${total % 60} 秒`;
  return `${total} 秒`;
}

function fmtAgo(epochSeconds?: number | null): string {
  if (typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds)) return "—";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分前`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86_400)} 天前`;
}

/**
 * 心跳时间语义条:下次诊断窗口 / 上轮结果 / 今日预算。
 *
 * 心跳未开启(enabled=false)时整块不渲染 —— 关闭的特性不该在页面上留一行
 * 空壳,更不该显示它永远不会发生的倒计时。
 */
export function HeartbeatStrip({ className }: { className?: string }) {
  const [data, setData] = useState<HeartbeatStatus | null>(null);
  const [, setNowTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = () => {
      getHeartbeatStatus()
        .then((next) => {
          if (alive) setData(next);
        })
        .catch(() => {
          // 状态端点不可达不该打断研究页 —— 保持上一帧(或空态)即可。
        });
    };
    load();
    const poll = setInterval(load, POLL_MS);
    const tick = setInterval(() => setNowTick((n) => n + 1), TICK_MS);
    return () => {
      alive = false;
      clearInterval(poll);
      clearInterval(tick);
    };
  }, []);

  if (!data?.enabled) return null;

  const outcomes = data.last_round?.outcomes ?? {};
  const remainingMs =
    typeof data.next_fire_at === "number" ? data.next_fire_at * 1000 - Date.now() : null;

  return (
    <div
      data-testid="heartbeat-strip"
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border/60 bg-background/40 px-2.5 py-1.5 text-[10px]",
        className,
      )}
    >
      <span className="font-semibold tracking-[0.08em] text-muted-foreground">PA 心跳</span>
      {data.busy && (
        <span data-testid="heartbeat-busy" className="flex items-center gap-1 text-gold">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
          诊断中
        </span>
      )}
      {remainingMs != null && (
        <span className="text-muted-foreground">
          下次诊断窗口{" "}
          <span className={cn("font-mono", remainingMs <= 0 ? "text-gold" : "text-foreground")}>
            {fmtRemaining(remainingMs)}
          </span>
        </span>
      )}
      {data.last_round && (
        <span className="text-muted-foreground">
          上轮 {fmtAgo(data.last_round.at)} · 成功 {outcomes.ok ?? 0} / 跳过{" "}
          {outcomes.skipped ?? 0} / 失败 {outcomes.failed ?? 0}
        </span>
      )}
      <span className="ml-auto font-mono text-muted-foreground">
        预算 {data.attempts_today ?? 0}/{data.config?.daily_max ?? "—"}
      </span>
    </div>
  );
}
