"use client";

import { EXCHANGE_TABS } from "@/lib/assets/types";
import { useAssetsStore } from "@/lib/store/assets";

export function ExchangeTabs() {
  const active = useAssetsStore((s) => s.activeExchange);
  const setActive = useAssetsStore((s) => s.setActiveExchange);
  // 不复用 .feed-tabs：那个类是给底栏(feed-bar)设计的，带 `height: 100%`
  // ——在固定高度的底栏里让 tab 条填满，但在高度由内容决定的卡片里，这个
  // 100% 会解析成整个容器的高度，把后面的兄弟节点挤出卡片外。
  return (
    <div className="flex shrink-0 items-center gap-0.5 border-b border-border-soft pb-1">
      {EXCHANGE_TABS.map((t) => (
        <button
          key={t.key}
          className={`feed-tab${active === t.key ? " active" : ""}`}
          onClick={() => setActive(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
