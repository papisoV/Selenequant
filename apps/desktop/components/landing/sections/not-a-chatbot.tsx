"use client";

/**
 * ③ NOT A CHATBOT —— 显式反模式对照(学 Elicit 的 "More than chat")。
 *
 * 不贬低对手,只讲机制差异:一次性对话 vs 持久问题。
 * 版面:一张两列对照面(不是两张等大卡片)——左列灰、右列亮,
 * 逐轴对齐,分隔线贯穿整面。对照表在这里是真·对照,不是卡片栅格的反射。
 * 数据逐字来自 lib/landing/real-data.ts 的 CONTRAST_ROWS。
 */

import { X, Check } from "lucide-react";
import { CONTRAST_ROWS } from "@/lib/landing/real-data";
import { Reveal } from "../primitives";

export function NotAChatbot() {
  return (
    <section id="not-a-chatbot" className="lp-section-tight lp-hair-b relative">
      <div className="lp-container">
        <Reveal>
          <h2 className="lp-h2 max-w-[26ch] text-ink">
            普通 AI 每个问题都从头开始。
            <br className="hidden sm:block" />
            Selene 的每个问题都带着历史。
          </h2>
        </Reveal>

        <Reveal delay={0.1}>
          <div className="mt-14 grid grid-cols-1 sm:grid-cols-2">
            {/* 列头 */}
            <div className="flex items-center gap-2.5 pb-4 sm:pr-10">
              <X className="size-4 text-ink-3" strokeWidth={2} />
              <span className="text-[0.9375rem] font-semibold text-ink-3">
                普通 AI 研究助手
              </span>
            </div>
            <div className="hidden items-center gap-2.5 pb-4 sm:flex sm:pl-10">
              <Check className="size-4 text-accent" strokeWidth={2.5} />
              <span className="text-[0.9375rem] font-semibold text-ink">
                Selene
              </span>
            </div>

            {/* 逐轴对照行 —— 单面贯穿分隔线,非两张独立卡 */}
            {CONTRAST_ROWS.map((row, i) => (
              <div key={row.axis} className="contents">
                <div
                  className={`flex flex-col gap-1.5 py-5 sm:pr-10 ${
                    i > 0 ? "border-t border-[var(--lp-hair)]" : ""
                  }`}
                >
                  <span className="num text-[0.75rem] tracking-[0.03em] text-ink-3">
                    {row.axis}
                  </span>
                  <span className="text-[1rem] leading-relaxed text-ink-3">
                    {row.chatbot}
                  </span>
                </div>
                <div
                  className={`flex flex-col gap-1.5 py-5 sm:border-l sm:border-[var(--lp-hair)] sm:pl-10 ${
                    i > 0 ? "border-t border-[var(--lp-hair)]" : "border-t border-[var(--lp-hair)] sm:border-t-0"
                  }`}
                >
                  <span className="num sr-only text-[0.75rem] tracking-[0.03em] text-accent/90">
                    {row.axis}
                  </span>
                  <span className="text-[1rem] font-medium leading-relaxed text-ink">
                    {row.selene}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Reveal>

        <Reveal delay={0.18}>
          <div className="mt-12 grid grid-cols-1 gap-8 border-t border-[var(--lp-hair)] pt-10 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
            <p className="text-[0.875rem] leading-relaxed text-ink-3">
              在这套机制里,你不是等答案的人。
            </p>
            <p className="text-[1.125rem] leading-relaxed text-ink">
              你提出问题、追问证据、推翻解释、叫停跑偏的方向,
              <span className="text-ink-2">
                它负责让每一次你做出的改动,都被历史接住。
              </span>
            </p>
          </div>
        </Reveal>

        <Reveal delay={0.24}>
          <p className="lp-meta mt-8">
            区别不在「有没有记忆」,在于历史会被
            <span className="text-ink"> 自动取回</span>,不要求你记得去翻。
          </p>
        </Reveal>
      </div>
    </section>
  );
}
