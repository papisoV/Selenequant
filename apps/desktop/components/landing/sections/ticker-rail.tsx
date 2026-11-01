"use client";

/**
 * 真实刻度条 —— 取代旧 Landing 的假行情跑马灯。
 *
 * 旧版的 TickerMarquee 滚动的是硬编码假价格(BTC 67,828)。这里滚动的是
 * **研究库里真实可数的事实**:策略数、回测数、问题数,以及真回测的
 * symbol + 区间 + 真实收益。数字可打开库核对。
 */

import { useEffect, useRef, useState } from "react";
import { REAL_BACKTESTS, SCALE_FACTS } from "@/lib/landing/real-data";
import { useUiText } from "@/components/i18n/use-ui-text";

const SCALE_ITEMS = [
  { label: "策略", value: String(SCALE_FACTS.strategies) },
  { label: "回测", value: String(SCALE_FACTS.backtests) },
  { label: "研究问题", value: String(SCALE_FACTS.questions) },
  { label: "证据", value: String(SCALE_FACTS.evidence) },
  { label: "界面", value: String(SCALE_FACTS.interfaces) },
];

export function TickerRail() {
  const tr = useUiText()
  const ref = useRef<HTMLDivElement>(null);
  const [animate, setAnimate] = useState(false);
  /** 周期刷新:每 2.4s 让一个真实数字闪一下(旧版的"活数据"手感,数据仍真) */
  const [flick, setFlick] = useState(-1);

  useEffect(() => {
    // 内容默认可见(揭示动画绝不 gate 可见性)。
    // 滚动只决定"要不要动",不决定"能不能看见"。
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setAnimate(false);
      return;
    }
    setAnimate(true);

    const n = SCALE_ITEMS.length;
    let i = 0;
    const t = setInterval(() => {
      setFlick(i % n);
      i += 1;
    }, 2400);
    return () => clearInterval(t);
  }, []);

  const cells = [
    ...SCALE_ITEMS.map((f) => ({ kind: "fact" as const, ...f })),
    ...REAL_BACKTESTS.slice(0, 10).map((b) => ({
      kind: "bt" as const,
      symbol: b.symbol,
      interval: b.interval,
      ret: b.ret,
    })),
  ];
  const loop = [...cells, ...cells];

  return (
    <div
      ref={ref}
      className="relative overflow-hidden border-y border-[var(--lp-hair)] bg-surface-1/40 py-3.5"
    >
      {/* 两端渐隐 */}
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-24 bg-gradient-to-r from-bg to-transparent" />
      <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-24 bg-gradient-to-l from-bg to-transparent" />

      <div
        className="flex w-max items-center gap-9"
        style={{
          animation: animate ? "lp-marquee 46s linear infinite" : "none",
        }}
      >
        {loop.map((c, i) => (
          <span
            key={i}
            className={`num flex shrink-0 items-center gap-2 font-mono text-[0.8125rem]${
              // 前半圈列表里命中刷新位的 fact 单元闪一下(loop 有两份,只闪第一份)
              c.kind === "fact" && i === flick ? " lp-flick" : ""
            }`}
          >
            {c.kind === "fact" ? (
              <>
                <span className="text-ink-3">{tr(c.label)}</span>
                <span className="font-semibold text-ink">{c.value}</span>
              </>
            ) : (
              <>
                <span className="text-ink-3">
                  {c.symbol} {c.interval}
                </span>
                <span className={c.ret >= 0 ? "text-pos" : "text-neg"}>
                  {c.ret > 0 ? "+" : ""}
                  {c.ret}%
                </span>
              </>
            )}
            <span aria-hidden className="text-[var(--lp-hair-strong)]">│</span>
          </span>
        ))}
      </div>
    </div>
  );
}
