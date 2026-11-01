"use client";

/**
 * VersionChain —— Landing 的视觉主角。
 *
 * 表达的唯一命题:"上一次的判断,被这一次引用了。"
 * 视觉语言直接来自产品数据结构(revision.seq / revision.cited),不另造:
 * - 左侧版本轴(v1 ●──v2 ●)= revision.seq;
 * - 版本间连接器上的「它用了 v{n}」= revision.cited_json 的真实指向;
 * - actor 徽章(selene=accent / 人=gold)= 真实 actor 字段。
 *
 * 动效只服务于"引用关系"这一个语义:进入视口时连线先画出来,再落引用标签。
 * 默认(无 JS / reduced-motion)全部静态可见。
 */

import { useEffect, useRef, useState } from "react";
import type { RealRevision } from "@/lib/landing/real-data";
import { useUiText } from "@/components/i18n/use-ui-text"

/** 引用连接器:从上一版画一条线下来,标注这一版引用了它。 */
function CitationConnector({
  toSeq,
  active,
}: {
  toSeq: number;
  active: boolean;
}) {
  const tr = useUiText()

  return (
    <div className="relative flex items-stretch gap-3 pl-[15px]">
      {/* 竖线 = 版本轴延续;scaleY 画线(不用 height,避免布局动画) */}
      <div className="relative w-px shrink-0 bg-[var(--lp-hair-strong)]">
        <span
          className={`lp-cite-draw absolute left-0 top-0 h-full w-px origin-top bg-gold/70 transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] ${
            active ? "scale-y-100" : "scale-y-0"
          }`}
        />
      </div>
      {/* 引用标签 */}
      <div
        className="flex items-center gap-1.5 py-2"
        style={{
          opacity: active ? 1 : 0,
          transition: "opacity 0.35s ease 0.4s",
        }}
      >
        <svg width="11" height="9" viewBox="0 0 11 9" fill="none" aria-hidden>
          <path
            d="M1 4.5h8M6 1.5 8.5 4.5 6 7.5"
            stroke="currentColor"
            className="text-accent"
            strokeWidth="1.1"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className="text-[0.8125rem] font-medium text-accent">
          {tr("它用了 v")}{toSeq}
        </span>
      </div>
    </div>
  );
}

function RevisionBlock({
  rev,
  prevSeq,
  isLast,
  reveal,
  diffSpans,
}: {
  rev: RealRevision;
  /** 本版引用的上一版 seq(取 cited 里真实存在的最大 seq;空则无连接器) */
  prevSeq: number | null;
  isLast: boolean;
  reveal: boolean;
  /** 可选:这一版相对上一版的关键差异短语(由数据侧传入,组件不掺内容) */
  diffSpans?: string[];
}) {
  const tr = useUiText()

  /* 正文按 diffSpans 切片:命中短语点亮(增量可追溯 = 引用机制的价值) */
  const segments = diffSpans?.length
    ? rev.content.split(new RegExp(`(${diffSpans.map(escapeRe).join("|")})`))
    : [rev.content];

  return (
    <div>
      {prevSeq !== null && <CitationConnector toSeq={prevSeq} active={reveal} />}
      <div className="flex gap-3">
        {/* 版本轴节点 */}
        <div className="relative flex w-[31px] shrink-0 justify-center">
          <span className="absolute inset-y-0 w-px bg-[var(--lp-hair-strong)]" />
          <span className="relative mt-4 inline-flex size-[10px] items-center justify-center rounded-full border border-gold/60 bg-surface-2">
            <span className="size-[3.5px] rounded-full bg-gold" />
          </span>
        </div>

        {/* 版本内容:靠留白分层,不用嵌套卡 */}
        <div
          className="min-w-0 flex-1 pb-1"
          style={{
            opacity: reveal ? 1 : 0.45,
            transition: "opacity 0.45s ease",
          }}
        >
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="num rounded bg-surface-3 px-2 py-0.5 font-mono text-[0.8125rem] font-semibold text-ink">
              v{rev.seq}
            </span>
            <span
              className={`text-[0.8125rem] ${
                rev.actor === "selene" ? "text-accent" : "text-gold"
              }`}
            >
              {rev.actor === "selene" ? "Selene" : tr("人")}
            </span>
            <span className="num ml-auto text-[0.8125rem] text-ink-3">
              {rev.createdAt}
            </span>
          </div>
          <p className="mt-2 text-[0.9375rem] leading-relaxed text-ink-2">
            {segments.map((seg, si) =>
              diffSpans?.includes(seg) ? (
                <mark
                  key={si}
                  className="bg-gold/[0.18] text-ink [box-decoration-break:clone] [text-decoration:underline] decoration-gold/45 decoration-2 underline-offset-4"
                >
                  {seg}
                </mark>
              ) : (
                <span key={si}>{seg}</span>
              ),
            )}
          </p>
          {rev.cited.length > 0 && (
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-[var(--lp-hair)] pt-2.5">
              <span className="text-[0.8125rem] text-ink-3">{tr("它用了")}</span>
              {rev.cited.map((c) => (
                <span
                  key={c}
                  className="num rounded border border-accent/30 bg-accent/[0.07] px-1.5 py-px font-mono text-[0.75rem] text-accent"
                >
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
      {!isLast && <div className="h-1.5" />}
    </div>
  );
}

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function VersionChain({
  revisions,
  className = "",
  diffSpans,
}: {
  revisions: RealRevision[];
  className?: string;
  /** 按 revision id 索引的关键差异短语(如 "rev-532cd8241a19": ["成色存疑"]) */
  diffSpans?: Record<string, string[]>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [reveal, setReveal] = useState(true);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setReveal(true);
      return;
    }
    // 与 Reveal 同纪律:SSR 默认可见;只对挂载时在视口下方的实例启用画线动画。
    if (el.getBoundingClientRect().top <= window.innerHeight) {
      setReveal(true);
      return;
    }
    setReveal(false);
    const obs = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setReveal(true);
          obs.disconnect();
        }
      },
      { rootMargin: "-60px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const seqById = new Map(revisions.map((r) => [r.id, r.seq]));

  return (
    <div ref={ref} className={className}>
      {revisions.map((rev, i) => {
        // 本版引用的上游中,取真实存在且 seq 最小的(通常 = 上一版)
        const citedSeqs = rev.cited
          .map((cid) => seqById.get(cid))
          .filter((s): s is number => s !== undefined);
        const prevSeq = citedSeqs.length ? Math.min(...citedSeqs) : null;
        return (
          <RevisionBlock
            key={rev.id}
            rev={rev}
            prevSeq={prevSeq}
            isLast={i === revisions.length - 1}
            reveal={reveal}
            diffSpans={diffSpans?.[rev.id]}
          />
        );
      })}
    </div>
  );
}
