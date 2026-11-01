"use client";

import { useEffect, useState } from "react";
import { getHeartbeatStatus, type HeartbeatStatus } from "@/lib/api/decision-workflow";
import { RESEARCH_POLL_SLOW_MS, pollWhenVisible } from "@/lib/hooks/research-poll";
import { cn } from "@/lib/utils";
import { useUiText } from "@/components/i18n/use-ui-text"
import { identityTranslate, type Translate } from "@/lib/i18n/types"

// 数据分钟级变化(节拍统一见 research-poll);倒计时每秒本地重算,不靠轮询驱动。
const POLL_MS = RESEARCH_POLL_SLOW_MS;
const TICK_MS = 1_000;

function fmtRemaining(ms: number, tr: Translate = identityTranslate): string {
  if (ms <= 0) return tr("窗口已开");
  const total = Math.floor(ms / 1000);
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return tr("{d} 天 {h} 小时", { d: days, h: hours });
  if (hours > 0) return tr("{h} 小时 {m} 分", { h: hours, m: minutes });
  if (minutes > 0) return tr("{m} 分 {s} 秒", { m: minutes, s: total % 60 });
  return tr("{s} 秒", { s: total });
}

function fmtAgo(epochSeconds?: number | null, tr: Translate = identityTranslate): string {
  if (typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds)) return "—";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  if (seconds < 60) return tr("{n} 秒前", { n: seconds });
  if (seconds < 3600) return tr("{n} 分前", { n: Math.floor(seconds / 60) });
  if (seconds < 86_400) return tr("{n} 小时前", { n: Math.floor(seconds / 3600) });
  return tr("{n} 天前", { n: Math.floor(seconds / 86_400) });
}

/**
 * 心跳时间语义条:下次诊断窗口 / 上轮结果 / 今日预算。
 *
 * 心跳未开启(enabled=false)时整块不渲染 —— 关闭的特性不该在页面上留一行
 * 空壳,更不该显示它永远不会发生的倒计时。
 */
export function HeartbeatStrip({ className }: { className?: string }) {
  const tr = useUiText()

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
    const poll = setInterval(() => pollWhenVisible(load), POLL_MS);
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
      <span className="font-semibold tracking-[0.08em] text-muted-foreground">{tr("PA 心跳")}</span>
      {data.busy && (
        <span data-testid="heartbeat-busy" className="flex items-center gap-1 text-gold">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
          {tr("诊断中")}
        </span>
      )}
      {remainingMs != null && (
        <span className="text-muted-foreground">
          {tr("下次诊断窗口")}{" "}
          <span className={cn("font-mono", remainingMs <= 0 ? "text-gold" : "text-foreground")}>
            {fmtRemaining(remainingMs, tr)}
          </span>
        </span>
      )}
      {data.last_round && (
        <span className="text-muted-foreground">
          {tr("上轮")} {fmtAgo(data.last_round.at, tr)} {tr("· 成功")} {outcomes.ok ?? 0} {tr("/ 跳过")}{" "}
          {outcomes.skipped ?? 0} {tr("/ 失败")} {outcomes.failed ?? 0}
        </span>
      )}
      <span className="ml-auto font-mono text-muted-foreground">
        {tr("预算")} {data.attempts_today ?? 0}/{data.config?.daily_max ?? "—"}
      </span>
    </div>
  );
}
