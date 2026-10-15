"use client";

import type { MissionSummary } from "./missions-types";

/** 相对时间 — "Xh ago" 式,hover title 显精确时间(军规:我离开之后发生了什么) */
export function relTime(iso: string, now = Date.now()): { label: string; title: string } {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return { label: "—", title: iso };
  const mins = Math.max(0, Math.round((now - t) / 60000));
  if (mins < 1) return { label: "刚刚", title: fmtAbs(iso) };
  if (mins < 60) return { label: `${mins}m ago`, title: fmtAbs(iso) };
  const hours = Math.round(mins / 60);
  if (hours < 48) return { label: `${hours}h ago`, title: fmtAbs(iso) };
  return { label: `${Math.round(hours / 24)}d ago`, title: fmtAbs(iso) };
}

function fmtAbs(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** NEEDS YOU 容量规则:最多突出 3 条,其余收进溢出提示(军规:防 TODAY 变 Inbox) */
export function capNeedsYou(list: MissionSummary[]): { shown: MissionSummary[]; overflow: number } {
  const shown = list.slice(0, 3);
  return { shown, overflow: Math.max(0, list.length - shown.length) };
}

/** 事件头一行结论句 — 先结论后解释;全部来自投影真值字段 */
export function eventLine(m: MissionSummary): string {
  return `${m.title} — ${m.question}`;
}
