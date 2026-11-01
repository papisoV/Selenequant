"use client";

import { useUiText } from "@/components/i18n/use-ui-text";

/**
 * 会话时标：**这块数据说的是哪一时刻/哪一交易日**。
 *
 * 与 `AshareFreshness`（`components/watch/parts/ashare-freshness.tsx`）语义正交：
 *   · `AshareFreshness` 回答「这份数据旧不旧」—— 新鲜时**返回 null 不标注**，
 *     陈旧时用金色方括号 `[20261020]`，颜色归它。
 *   · `SessionStamp` 回答「这份数据截至哪天、是不是盘中暂定」—— **无论新旧都要
 *     显示**，纯中性色，不含涨跌/警示语义。
 *
 * 两者不可互相替代：把日期塞进 `AshareFreshness` 会让今天的数据彻底不显示日期。
 *
 * 此文件放在 `lib/` 而非 `components/watch/`：看盘面的「手写时标归零」判据
 * 按目录 grep 扫描（查 `tr(` 后跟「截至」或「盘中暂定」的中文字面量，
 * 见计划 A1-7），本组件是唯一合法作者；若也落在扫描范围内，判据就永远不会是 0。
 */
export type SessionScope = "settled" | "provisional";

/**
 * 时标的**唯一判定点**：分支只在这里做一次，纯函数版本与组件都读它。
 *
 * 分成两份实现（纯函数自己拼字符串、组件自己走 i18n）会让「删掉 provisional
 * 分支」这种变异只杀死一半测试 —— 变异验证会给出假绿。
 */
export function sessionStampSource(
  asOf: string | null | undefined,
  scope: SessionScope = "settled",
): { key: string; date: string | null } | null {
  // 空串与纯空白等同于「没给日期」—— 后端漏字段时常见 `""`，
  // 渲染出半截「截至 」比不渲染更糟。
  const raw = asOf == null ? "" : String(asOf).trim();
  const date = raw === "" ? null : raw;

  if (scope === "provisional") {
    return date === null
      ? { key: "盘中暂定", date: null }
      : { key: "盘中暂定 · {as_of}", date };
  }
  return date === null ? null : { key: "截至 {as_of}", date };
}

/**
 * 时标的 zh-CN 渲染（纯函数，可独立断言）。
 *
 * 约定：
 *   · 有日期 + settled     → `截至 20241031`
 *   · 有日期 + provisional → `盘中暂定 · 20261015`
 *   · 无日期 + settled     → null（不渲染：没有时标可标）
 *   · 无日期 + provisional → `盘中暂定`（仍要标：这块数据本就不该有收盘日）
 */
export function freshnessLabel(
  asOf: string | null | undefined,
  scope: SessionScope = "settled",
): string | null {
  const source = sessionStampSource(asOf, scope);
  if (source === null) return null;
  return source.date === null
    ? source.key
    : source.key.replace("{as_of}", source.date);
}

/**
 * 会话时标组件。始终中性色（`text-ink-4`）：颜色预算已被涨跌/强弱/研究占满，
 * 「哪天」不属于这三类中的任何一类。
 *
 * **字号由调用方决定**：时标出现的槽（`V17Card` 的 `right`、`BandHead` 的
 * `right`、各面板头部）本来就带 `text-[9px]`/`text-[10px]`，本组件若写死一个，
 * 就会与父级打架 —— 两个都是 arbitrary value，谁赢取决于样式表顺序，不是
 * 属性顺序。只留 `className` 给确需覆盖的调用点。
 */
export function SessionStamp({
  asOf,
  scope = "settled",
  className = "",
}: {
  asOf: string | null | undefined;
  scope?: SessionScope;
  className?: string;
}) {
  const tr = useUiText();
  const source = sessionStampSource(asOf, scope);
  if (source === null) return null;

  const text =
    source.date === null ? tr(source.key) : tr(source.key, { as_of: source.date });
  return <span className={`text-ink-4 ${className}`.trim()}>{text}</span>;
}
