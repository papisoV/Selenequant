"use client";

import { useUiText } from "@/components/i18n/use-ui-text";

/**
 * 设置页顶部导航条 —— 分组 chip + 搜索框。
 *
 * 为什么要有:设置页有 19 个面板,代理原先在第 13 位(76% 屏深处),滚三屏
 * 才能找到"数据拉不到"时最该先看的那一栏。chip 直接跳转,搜索按可见文案过滤。
 *
 * chip 横向滚动而非换行:窄屏下 19 个 chip 换行会吃掉半屏高度,把要看的
 * 内容挤出视口 —— 与"帮助定位"的目的正好相反。
 */

export interface SettingsNavItem {
  /** 目标卡片的 DOM id。 */
  id: string;
  /** 中文源串,由本组件 `tr()`。 */
  label: string;
}

export function SettingsNav({
  items,
  activeId,
  onJump,
  query,
  onQueryChange,
}: {
  items: SettingsNavItem[];
  activeId: string;
  onJump: (id: string) => void;
  query: string;
  onQueryChange: (next: string) => void;
}) {
  const tr = useUiText();

  return (
    <div
      className="min-[900px]:col-span-2 min-w-0 sticky top-0 z-10 rounded border border-border-soft bg-surface-2/95 p-1.5 backdrop-blur"
      role="navigation"
      aria-label={tr("跳转到分组")}
    >
      <div className="flex items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder={tr("搜索设置")}
          aria-label={tr("搜索设置")}
          className="set-input w-40 shrink-0"
        />
        <div className="flex flex-1 gap-1 overflow-x-auto">
          {items.map((item) => {
            const active = item.id === activeId;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onJump(item.id)}
                aria-current={active ? "true" : undefined}
                className={`set-btn shrink-0 whitespace-nowrap rounded border px-1.5 text-[11px] ${
                  active
                    ? "border-gold bg-surface-3 text-gold"
                    : "border-border-soft bg-surface-2 text-ink-3"
                }`}
              >
                {tr(item.label)}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
