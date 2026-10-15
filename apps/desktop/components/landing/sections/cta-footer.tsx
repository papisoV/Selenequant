"use client";

/**
 * ⑦ CTA + Footer —— 双门 CTA(本地跑起来为主,看一次完整研究为辅)。
 * Footer 放可验证 artifact(版本 / license / repo),不放假计数。
 * CTA 用满幅景深收束全页,不是又一张卡片。
 */

import { useLastPageHref } from "@/lib/hooks/use-last-page-href";
import { ButtonPrimary, ButtonGhost } from "./shared";

export function CtaSection() {
  const href = useLastPageHref("/research/today");
  return (
    <section className="relative overflow-hidden">
      <div className="lp-field" />
      <div className="lp-container relative py-24 lg:py-28">
        <div
          className="absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, rgba(48,156,251,0.35), rgba(167,139,250,0.28), rgba(247,160,125,0.4), transparent)",
          }}
        />
        <h2 className="lp-h2 max-w-[20ch] text-ink">
          下一次判断,应该建立在上一次真实发生过的结果之上。
        </h2>
        <p className="lp-lead lp-measure mt-5">
          在本地跑起来,或者先看一次完整研究怎么走完一轮。
        </p>
        <div className="mt-10 flex flex-wrap items-center gap-3.5">
          <ButtonPrimary href={href}>在本地跑起来</ButtonPrimary>
          <ButtonGhost href="#the-loop">看一次完整研究</ButtonGhost>
        </div>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-[var(--lp-hair)]">
      <div className="lp-container flex flex-col gap-3 py-8 sm:flex-row sm:items-center">
        <div className="flex items-center gap-2.5">
          <span className="text-[0.9375rem] font-semibold text-ink">
            SeleneQuant
          </span>
          <span className="text-[0.8125rem] text-ink-3">
            AGPL-3.0 · Community Edition
          </span>
        </div>
        <div className="flex items-center gap-6 text-[0.8125rem] text-ink-3 sm:ml-auto">
          <a
            href="https://github.com/papisoV/selenequant"
            target="_blank"
            rel="noopener noreferrer"
            className="num inline-flex min-h-6 items-center font-mono transition-colors hover:text-ink-2"
          >
            GitHub
          </a>
          <span>Local-first</span>
          <span>Open source</span>
        </div>
      </div>
    </footer>
  );
}
