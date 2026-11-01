"use client";

/**
 * 进度导轨 —— 固定右侧的章节导航(仅 lg+ 显示)。
 *
 * 设计纪律:不做成"花哨进度条"。细刻度 + 当前章节高亮,语义色全部走
 * --lp-* 变量(暗亮双主题自动适配);prefers-reduced-motion 下无过渡。
 * IntersectionObserver 只做"当前章节"判定,不控制任何可见性(Reveal 管那个)。
 */

import { useEffect, useState } from "react";
import { useUiText } from "@/components/i18n/use-ui-text"

const ANCHORS = [
  { id: "hero", label: "主张" },
  { id: "council", label: "调查会" },
  { id: "the-loop", label: "研究回路" },
  { id: "backtests", label: "回测" },
  { id: "bench", label: "实验台" },
  { id: "not-a-chatbot", label: "不是什么" },
  { id: "memory", label: "记忆" },
  { id: "audit", label: "审计" },
  { id: "open-local", label: "开源本地" },
] as const;

export function ProgressRail() {
  const tr = useUiText()

  const [active, setActive] = useState<string>("hero");

  useEffect(() => {
    const sections = ANCHORS.map(({ id }) =>
      document.getElementById(id),
    ).filter((el): el is HTMLElement => el !== null);
    if (sections.length === 0) return;

    const obs = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(entry.target.id);
        }
      },
      { rootMargin: "-35% 0px -55% 0px" },
    );
    for (const s of sections) obs.observe(s);
    return () => obs.disconnect();
  }, []);

  return (
    <nav
      aria-label={tr("页面章节")}
      className="fixed top-1/2 right-6 z-40 hidden -translate-y-1/2 lg:block"
    >
      <ul className="flex flex-col gap-1">
        {ANCHORS.map(({ id, label }) => {
          const isActive = active === id;
          return (
            <li key={id}>
              <a
                href={`#${id}`}
                aria-current={isActive ? "true" : undefined}
                className="group flex items-center justify-end gap-2.5 py-1.5"
              >
                <span
                  className={`text-[0.75rem] transition-opacity duration-200 motion-reduce:transition-none ${
                    isActive
                      ? "text-ink-2 opacity-100"
                      : "text-ink-3 opacity-0 group-hover:opacity-100"
                  }`}
                >
                  {tr(label)}
                </span>
                <span
                  className={`h-px transition-all duration-200 motion-reduce:transition-none ${
                    isActive
                      ? "w-6 bg-accent"
                      : "w-3 bg-[var(--lp-hair-strong)] group-hover:w-5"
                  }`}
                />
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
