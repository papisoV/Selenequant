"use client";

import type { MissionSummary } from "./missions-types";
import type { NeedsYouCardItem } from "./today-types";
import { identityTranslate, type Translate } from "@/lib/i18n/types";

/** 相对时间 — "Xh ago" 式,hover title 显精确时间(军规:我离开之后发生了什么) */
export function relTime(
  iso: string,
  now = Date.now(),
  tr: Translate = identityTranslate,
): { label: string; title: string } {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return { label: "—", title: iso };
  const mins = Math.max(0, Math.round((now - t) / 60000));
  if (mins < 1) return { label: tr("刚刚"), title: fmtAbs(iso) };
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

/**
 * NEEDS YOU 折叠:同一「标的 + 问句」的多条 Mission 合成一张卡。
 *
 * 同一个问句会被不同入口各开一次案(PA 心跳 / CLI 诊断),标题跟着来源各写各的,
 * 按标题分组对它们完全失明。实测库里 26 条 open Mission 是同一个问句、三种标题:
 * NEEDS YOU 窗口里因此出现三对一模一样的待办。后端已折叠;这里再折一次,是为了
 * 老后端 / 测试注入的原始行也能正确显示,而不是靠"后端一定新"这个假设。
 *
 * 组内首领取最新一条(输入已按 updated_at 降序),`duplicates` = 组员数 − 1。
 */
export function foldNeedsYou(list: NeedsYouCardItem[]): NeedsYouCardItem[] {
  const order: string[] = [];
  const byKey = new Map<string, NeedsYouCardItem>();
  for (const m of list) {
    const key = `${m.subject_type}\u0000${m.subject_id}\u0000${m.question}`;
    const existing = byKey.get(key);
    if (existing) {
      const dupIds = existing.duplicate_ids ?? [];
      existing.duplicates = (existing.duplicates ?? 0) + (m.duplicates ?? 0) + 1;
      existing.duplicate_ids = [...dupIds, m.id, ...(m.duplicate_ids ?? [])];
      continue;
    }
    const fresh: NeedsYouCardItem = {
      ...m,
      duplicates: m.duplicates ?? 0,
      duplicate_ids: [...(m.duplicate_ids ?? [])],
    };
    byKey.set(key, fresh);
    order.push(key);
  }
  return order.map((k) => byKey.get(k)!);
}

/**
 * NEEDS YOU 容量规则:最多突出 3 组(折叠后),其余收进溢出提示
 * (军规:防 TODAY 变 Inbox)。溢出按**组**计,不按行 —— 报"还有 19 条"而其中
 * 18 条是同一件事,等于把冗余又数了一遍。
 */
export function capNeedsYou(list: NeedsYouCardItem[]): {
  shown: NeedsYouCardItem[];
  overflow: number;
  totalGroups: number;
} {
  const groups = foldNeedsYou(list);
  const shown = groups.slice(0, 3);
  return {
    shown,
    overflow: Math.max(0, groups.length - shown.length),
    totalGroups: groups.length,
  };
}

/** 事件头一行结论句 — 先结论后解释;全部来自投影真值字段 */
export function eventLine(m: MissionSummary): string {
  return `${m.title} — ${m.question}`;
}
