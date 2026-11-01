"use client";

import type { ReactNode } from "react";

/**
 * 看盘块头部的数据新鲜度标注。
 *
 * 与 `intelligence-market-pulse-history-page.tsx:36` 的 `StatusBadge` 同一套
 * 视觉规则（`text-gold` / `text-ink-4` 加方括号小字），区别是数据源不同：
 * 那边读的是行情服务自带的 `data_status: live|stale|unavailable`，
 * 这里 A 股看盘台没有那个字段，能拿到的只有响应自带的日期。
 *
 * **不新增颜色、不新增布局**：一条内联 `[……]`，与页面右上的日期并排。
 *
 * 三种状态：
 *   · ok      —— 无标注（与 StatusBadge 的 live 行为一致：新鲜是常态，不必说）
 *   · stale   —— 响应里的会话日不是今天，说明看到的是历史帧
 *   · unknown —— 端点返回了数据却没给任何会话日期
 *
 * `sessionDate` 为 `undefined` 表示**这块还没有数据**（首帧未回 / 请求失败），
 * 此时不标注：给一个还没加载的块挂「无时标」是在报假问题，首屏会闪一下。
 * 要标「无时标」，调用方须显式传 `null`（确实拿到了数据、但响应里没日期）。
 */
export function AshareFreshness({
  /** 响应自带的会话日，`YYYYMMDD` 或 `YYYY-MM-DD`；`undefined` = 这块还没有数据。 */
  sessionDate,
  /** 今天（北京时间）的 `YYYYMMDD`。由调用方从 /session 的 as_of 截取。 */
  today,
  className = "",
}: {
  sessionDate: string | null | undefined;
  today: string | null;
  className?: string;
}) {
  // 无数据 → 不标注。只有在"拿到了数据"的前提下才谈得上新鲜度。
  if (sessionDate === undefined) return null;
  const badge = freshnessBadge(sessionDate, today);
  if (badge === null) return null;
  return <span className={`text-[10px] ${badge.cls} ${className}`}>{badge.text}</span>;
}

/**
 * 纯函数部分 —— 可独立测试，不必渲染。
 *
 * 返回 null 表示「不必标注」。`today` 缺失时不判 stale：没有可信的"今天"，
 * 说"过期"就是瞎猜。`undefined` 与 `null` 同样返回"无时标"：调用方若已
 * 决定要标，两种缺失都只能说明响应里没有日期。
 */
export function freshnessBadge(
  sessionDate: string | null | undefined,
  today: string | null
): { text: string; cls: string } | null {
  const compact = compactDate(sessionDate);
  if (compact === null) return { text: "[无时标]", cls: "text-ink-4" };
  if (today !== null && compact !== today) {
    return { text: `[${compact}]`, cls: "text-gold" };
  }
  return null;
}

function compactDate(v: string | null | undefined): string | null {
  if (v == null) return null;
  const digits = String(v).replace(/\D/g, "");
  return digits.length >= 8 ? digits.slice(0, 8) : null;
}

/** 把 `/session` 的 `as_of`（`2026-10-21T18:08:44+08:00`）截成 `20261021`。 */
export function todayFromSessionAsOf(asOf: string | null | undefined): string | null {
  if (!asOf) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(asOf);
  return m ? `${m[1]}${m[2]}${m[3]}` : null;
}

/** 卡片头部的 `right` 槽：日期在左、新鲜度在右，空值直接不渲染。 */
export function CardRight({
  date,
  children,
}: {
  date?: string | null;
  children?: ReactNode;
}) {
  const hasDate = date != null && date !== "";
  if (!hasDate && children == null) return null;
  return (
    <span className="flex items-center gap-1">
      {hasDate ? <span className="text-[10px] text-ink-4">{date}</span> : null}
      {children}
    </span>
  );
}
