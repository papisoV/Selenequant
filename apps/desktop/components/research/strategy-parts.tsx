"use client";

import type { Provenance } from "./strategy-types";

/** 错误恢复路径文案 — 429 限流(60s 自动重试)与后端挂(重启 local-api)分开说 */
export function errorHintText(status: number | undefined): string {
  return status === 429
    ? "请求被限流 — 正在 60 秒后自动重试,无需操作。"
    : "后端不可用 — 确认 local-api 已启动(localhost:8088)";
}

/** 全宽错误态块 — 视觉稿 neg 边框工艺,带 429/后端挂区分 */
export function ErrorHint({ status }: { status: number | undefined }) {
  return (
    <div className="w-full rounded-lg border border-neg/40 bg-surface-2 p-3.5 text-[11px] leading-relaxed text-ink-2">
      {errorHintText(status)}
    </div>
  );
}

/** 面板头 — 与 mission-parts ColHead 同工艺 */
export function PanelHead({
  name,
  role,
  anchor,
}: {
  name: string;
  role?: string;
  anchor?: string;
}) {
  return (
    <div className="flex items-baseline gap-2 border-b border-border-soft pb-2">
      <span className="text-[11px] font-semibold tracking-wider text-ink">{name}</span>
      {role && <span className="text-[10px] text-ink-3">{role}</span>}
      {anchor && <span className="num ml-auto text-[9px] text-ink-4">{anchor}</span>}
    </div>
  );
}

/** 块标签 — 视觉稿 .blk-label */
export function BlkLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-4">{children}</div>
  );
}

/** provenance 徽章 — RECORDED 绿 / RECONSTRUCTED 蓝 / NOT_AVAILABLE 灰黄 */
const PROV_STYLE: Record<Provenance, string> = {
  RECORDED: "border-pos/40 bg-pos/10 text-pos",
  RECONSTRUCTED: "border-accent/50 bg-accent/10 text-accent",
  NOT_AVAILABLE: "border-warn/40 bg-warn/10 text-warn",
};

export function ProvChip({ p, label }: { p: Provenance; label?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded border px-1 py-px text-[8.5px] font-semibold tracking-wide ${PROV_STYLE[p]}`}
    >
      {label ?? p}
    </span>
  );
}

/** 空态容器 — 视觉稿 .empty-feed / .mem-empty 家族 */
export function EmptyFeed({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-md border border-dashed border-ink-5 px-3 py-3.5 text-center text-[10.5px] leading-relaxed text-ink-4 ${className}`}
    >
      {children}
    </div>
  );
}

/** NOW 条定性行 — 视觉稿 .cond-row */
export function CondRow({
  k,
  state,
  stateTone,
  v,
}: {
  k: string;
  state: string;
  stateTone: "warn" | "neg" | "muted" | "off";
  v: string;
}) {
  const tone =
    stateTone === "warn"
      ? "text-warn"
      : stateTone === "neg"
        ? "text-neg"
        : stateTone === "off"
          ? "text-ink-4"
          : "text-ink-3";
  return (
    <div className="grid grid-cols-[112px_1fr_auto] items-baseline gap-2 border-b border-border-soft py-[5px] last:border-b-0">
      <span className="text-[10px] text-ink-3">{k}</span>
      <span className={`text-[11px] font-semibold ${tone}`}>{state}</span>
      <span className="num text-right text-[10px] text-ink-2">{v}</span>
    </div>
  );
}

function fmtDT(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

/** 时间格式化(短) */
export function fmtEpTime(iso: string): string {
  return fmtDT(iso);
}

/** 百分比格式化:0.0385 → +3.85% */
export function fmtPct(x: number | null | undefined, digits = 2): string {
  if (x == null || Number.isNaN(x)) return "—";
  const sign = x > 0 ? "+" : "";
  return `${sign}${(x * 100).toFixed(digits)}%`;
}
