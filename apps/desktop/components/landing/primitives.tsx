"use client";

/**
 * Landing 原语 —— 与产品同一套视觉语言(深底 / 冷色 / 等宽数字 / 细线),
 * 但按 brand register 的尺度重建。
 *
 * 与旧 primitives 的差别:
 * - 删 GlassCard(玻璃拟态)、发光 SectionDivider(装饰动效);
 * - 删"每节一条 eyebrow"的营销脚手架 —— 改为 SectionMarker:一条仪器读数
 *   (小节名 + 右侧真实计数),只在有真实计数可报的章节出现,不每节反射式出现;
 * - Reveal 默认可见(SSR / 无 JS / 隐藏标签页都不空白),仅在能跑动效时增强:
 *   SSR 输出可见;挂载后只有「当时在视口下方」的元素才隐藏并等待 IntersectionObserver。 */

import { useEffect, useRef, useState } from "react";

/* ── Reveal:进入视口轻微上移。默认可见,动效只做增强 ─────────────────────── */

export function Reveal({
  children,
  delay = 0,
  className = "",
  y = 16,
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
  y?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(true);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(true);
      return;
    }
    // 只隐藏挂载时刻仍在视口下方的元素;首屏元素永不隐藏 → 无闪烁。
    if (el.getBoundingClientRect().top <= window.innerHeight) {
      setShown(true);
      return;
    }
    setShown(false);
    const obs = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setShown(true);
          obs.disconnect();
        }
      },
      { rootMargin: "-40px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: shown ? 1 : 0,
        transform: shown ? "none" : `translateY(${y}px)`,
        transition: `opacity 0.6s cubic-bezier(0.16,1,0.3,1) ${delay}s, transform 0.6s cubic-bezier(0.16,1,0.3,1) ${delay}s`,
      }}
    >
      {children}
    </div>
  );
}

/* ── SectionMarker:一条仪器读数(小节名 + 真实计数) ─────────────────────
   不是营销 eyebrow —— 只承载"这一节是什么 + 有多少条真实读数"。
   没有计数可报的章节不用它。 */

export function SectionMarker({
  label,
  count,
}: {
  label: string;
  count?: string;
}) {
  return (
    <div className="lp-hair-b flex items-baseline gap-4 pb-3">
      <span className="num text-[0.8125rem] font-medium tracking-[0.02em] text-ink-2">
        {label}
      </span>
      <span className="h-px flex-1 translate-y-[-0.25em] bg-[var(--lp-hair)]" />
      {count && <span className="num text-[0.8125rem] text-ink-3">{count}</span>}
    </div>
  );
}

/* ── SectionDivider:章节之间的仪器分隔线 ──────────────────────────────────
   Round-5 恢复旧 Landing 的分隔词汇,但收敛到 brand register:
   透明度克制在 0.05-0.12 一档(见 landing-scale.css),变体三选一:
   - glow:中央光晕(主章节之间,全页 ≤ 4 处)
   - dot :中央呼吸点(副章节之间)
   - line:纯渐隐线(密集段落之间) */

export function SectionDivider({
  variant = "line",
  className = "",
  hue,
}: {
  variant?: "glow" | "dot" | "line";
  className?: string;
  hue?: string;
}) {
  const v = variant === "glow" ? "lp-divider--glow" : variant === "dot" ? "lp-divider--dot" : "";
  return (
    <div
      aria-hidden
      className={`lp-divider ${v} ${className}`}
      style={hue ? ({ "--lp-div-glow": hue } as React.CSSProperties) : undefined}
    />
  );
}

/* ── Mono:等宽 + tabular-nums(全站数字纪律) ────────────────────────────── */

export function Mono({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span className={`num tabular-nums ${className}`}>{children}</span>
  );
}
