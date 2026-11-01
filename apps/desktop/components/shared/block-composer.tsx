"use client";

import { useMemo, useState } from "react";
import type { PageMeta, RenderMode } from "@/lib/page-manifest";
import { ALL_PAGES } from "@/lib/page-manifest";
import { useQueryParam } from "@/lib/hooks/use-query-param";
import { PageRenderer } from "./page-renderer";
import { cn } from "@/lib/utils";
import { useUiText } from "@/components/i18n/use-ui-text";

export interface BlockDef {
  slug: string;
  label: string;
  /** panel 在 grid 中跨几列(默认 1);onchain-hub 总览用 2 */
  span?: number;
  /** 无组件注册的块(slug 不在 page-renderer WIDGET_LOADERS)需指定,决定 fallback 渲染器 */
  renderMode?: RenderMode;
  /** 同族视图切换(segmented control,不占 tab 名额,判定表 r5 §tab上限):爆仓族 明细/统计/热区 等并列视图 */
  views?: BlockDef[];
}

interface Props {
  /** 常驻并排显示的块;空数组 = 纯 tab 模式(hub 全部侧面收 tab,如衍生品面 7 tab) */
  panels: BlockDef[];
  /** Tab 切换显示的块 */
  tabs?: BlockDef[];
  /** 默认激活 tab;无 tabs 时忽略 */
  defaultTab?: string;
  /** 面板头右侧状态区(判定表 r5 node 层,如研究代理就绪);无则不渲染 */
  node?: React.ReactNode;
}

function Block({ def }: { def: BlockDef }) {
  const tr = useUiText();
  const [viewSlug, setViewSlug] = useState(def.views?.[0]?.slug ?? "");
  const current = def.views?.find((v) => v.slug === viewSlug) ?? def.views?.[0] ?? def;
  const meta = useMemo<PageMeta | undefined>(() => {
    const found = ALL_PAGES.find((p) => p.slug === current.slug);
    if (found) return found;
    return {
      slug: current.slug,
      title: current.label,
      scene: "watch",
      category: "",
      renderMode: current.renderMode ?? "list",
      removable: true,
      origin: "design",
    };
  }, [current]);
  return (
    <div className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded border border-[color:var(--color-border-soft)]">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-[color:var(--color-border-soft)] bg-[color:var(--color-surface-2)] px-2 py-1">
        <span className="size-1.5 rounded-full bg-gold/70" aria-hidden />
        <span className="truncate text-[10px] font-medium text-ink-2">{tr(current.label)}</span>
        {def.views && def.views.length > 1 && (
          <div className="ml-auto flex gap-0.5" role="group">
            {def.views.map((v) => (
              <button
                key={v.slug}
                aria-pressed={current.slug === v.slug}
                onClick={() => setViewSlug(v.slug)}
                className={cn(
                  "rounded px-1.5 py-0.5 text-[9px] transition-colors",
                  current.slug === v.slug
                    ? "bg-gold/20 text-gold"
                    : "text-ink-3 hover:bg-surface-2 hover:text-ink-2",
                )}
              >
                {tr(v.label)}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <PageRenderer meta={meta} />
      </div>
    </div>
  );
}

/**
 * 拼装页容器 — 把 manifest 里的积木页组合成一个工作台
 * panels 并排常驻，tabs 切换；块身份仍是 manifest slug（PageRenderer 直取），不复制组件。
 */
export function BlockComposer({ panels, tabs = [], defaultTab, node }: Props) {
  const tr = useUiText();
  // ?tab=<slug> 深链:外部(如 kf 工具栏「情绪」)可直接落到某个 tab。
  // defaultTab 可能指向 panel slug(如 pos 页 "pos")而非 tabs 成员 —— 此时 tabs
  // 无激活项,让 panel 独占整行;命中 tab 才激活,未指定才回退到首个 tab。
  // 优先级:用户点击 > 深链 > defaultTab。全部用派生值而非 effect —— 首帧就是
  // 正确的 tab,不会先渲染 defaultTab 再跳一下。
  // userTab 仅表示"用户点过";空串是合法用户态(点掉激活),故用 ?? 而非 ||。
  const deepLinkTab = useQueryParam("tab");
  const [userTab, setUserTab] = useState<string | null>(null);
  const active =
    userTab ??
    (deepLinkTab && tabs.some((t) => t.slug === deepLinkTab)
      ? deepLinkTab
      : defaultTab
        ? tabs.some((t) => t.slug === defaultTab)
          ? defaultTab
          : panels.some((p) => p.slug === defaultTab)
            ? ""
            : (tabs[0]?.slug ?? "")
        : (tabs[0]?.slug ?? ""));
  const hasActive = tabs.some((t) => t.slug === active);

  return (
    <div className="flex h-full flex-col gap-2 p-2">
      {tabs.length > 0 && (
        <div className="flex shrink-0 items-center gap-1" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.slug}
              role="tab"
              aria-selected={active === t.slug}
              onClick={() => setUserTab(active === t.slug && panels.length > 0 ? "" : t.slug)}
              className={cn(
                "rounded px-2 py-1 text-[10px] transition-colors",
                active === t.slug
                  ? "bg-gold/20 text-gold"
                  : "text-ink-3 hover:bg-surface-2 hover:text-ink-2",
              )}
            >
              {tr(t.label)}
            </button>
          ))}
          {node && <div className="ml-auto">{node}</div>}
        </div>
      )}
      <div
        className="grid min-h-0 flex-1 gap-2"
        style={{
          gridTemplateColumns: `repeat(${Math.max(panels.length, 1)}, minmax(0, 1fr))`,
          gridTemplateRows: tabs.length > 0 && panels.length > 0 && hasActive ? "minmax(0, 1fr) minmax(0, 1fr)" : "minmax(0, 1fr)",
        }}
      >
        {panels.map((p) => (
          <div
            key={p.slug}
            style={{ gridColumn: p.span && p.span > 1 ? `span ${p.span}` : undefined }}
            className="grid min-h-0 min-w-0 overflow-hidden"
          >
            <Block def={p} />
          </div>
        ))}
        {/* tab 内容:未激活时不占格,激活时独占第二行——不与 panels 平分 50% */}
        {tabs.length > 0 && hasActive && (
          <div
            className={
              panels.length > 0
                ? "col-span-full grid min-h-0 overflow-hidden"
                : "grid min-h-0 overflow-hidden"
            }
            style={panels.length > 0 ? undefined : { gridColumn: `1 / -1`, gridRow: "1 / -1" }}
          >
            <Block def={tabs.find((t) => t.slug === active) ?? tabs[0]} />
          </div>
        )}
      </div>
    </div>
  );
}
