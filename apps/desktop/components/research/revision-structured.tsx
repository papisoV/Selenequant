"use client";

/**
 * 一版回答里「可判定的东西」——置信度 / 反向检验 / 适用条件(v030)。
 *
 * 为什么单独成组件:这三项是**解析出来的**,不是正文。正文里有没有写全,
 * 模型经常不遵守(实测 20 条里只有 3 条写了【反向检验】),所以这里的分支
 * 不是"有值/没值",而是"写没写":
 *  - 至少有一项 → 显示这些项;
 *  - 一项都没有 → 只显示「本版未结构化」,不编造任何占位文案。
 * 缺口本身另有 structured_missing 事件,想看"缺哪段"去活动流。
 */

import type { Revision } from "@/lib/api/research-graph";

function confidenceLabel(value: number | string | undefined): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value === "number") {
    // 0.65 → "65%";模型给 0 也是合法值,不能被 falsy 判断吞掉
    return `${Math.round(value * 100)}%`;
  }
  return value;
}

export function RevisionStructured({
  structured,
  testIdPrefix = "revision-structured",
}: {
  structured: Revision["structured"] | undefined;
  testIdPrefix?: string;
}) {
  const confidence = confidenceLabel(structured?.confidence);
  const falsified = structured?.falsified_when;
  const applies = structured?.applies_when;

  if (!confidence && !falsified && !applies) {
    return (
      <span
        data-testid={`${testIdPrefix}-empty`}
        className="rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[9px] text-ink-4"
        title="这一版没有可机器判定的字段(模型未按五段结构输出)"
      >
        本版未结构化
      </span>
    );
  }

  return (
    <div
      data-testid={testIdPrefix}
      className="mt-3 space-y-1.5 rounded-lg border border-border-soft bg-surface-1 px-3 py-2"
    >
      {confidence && (
        <div className="flex items-baseline gap-2">
          <span className="shrink-0 text-[9px] tracking-[0.08em] text-ink-4">
            置信度
          </span>
          <span
            data-testid={`${testIdPrefix}-confidence`}
            className="font-mono text-[11px] text-ink-2"
          >
            {confidence}
          </span>
        </div>
      )}
      {falsified && (
        <div className="flex items-baseline gap-2">
          <span className="shrink-0 text-[9px] tracking-[0.08em] text-ink-4">
            什么会推翻
          </span>
          <span
            data-testid={`${testIdPrefix}-falsified`}
            className="whitespace-pre-wrap text-[11px] leading-relaxed text-ink-2"
          >
            {falsified}
          </span>
        </div>
      )}
      {applies && (
        <div className="flex items-baseline gap-2">
          <span className="shrink-0 text-[9px] tracking-[0.08em] text-ink-4">
            适用范围
          </span>
          <span
            data-testid={`${testIdPrefix}-applies`}
            className="whitespace-pre-wrap text-[11px] leading-relaxed text-ink-2"
          >
            {applies}
          </span>
        </div>
      )}
    </div>
  );
}
