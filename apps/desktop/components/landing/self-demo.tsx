"use client";

/**
 * 自检终端 —— 发布前门禁的真实运行回放(泛化展示)。
 *
 * 逐字回放发布门禁的运行形态:ok=false, tier1/tier2/secrets 有命中,
 * exit 1。**行文本按类别泛化**(不点名具体文件), 只保留结论与计数——
 * 既展示"门拒绝放行"的诚实纪律, 又不暴露内部文件路径。
 *
 * 诚实纪律:**不美化成绿色通过**。门说"还不能发布"就如实打出
 * ok:false —— 这恰恰证明门不是摆设, 处理完重跑, 不许绕过。
 *
 * 动效:JS 逐字打字(进入视口才开始),光标 CSS 闪烁;reduced-motion
 * 一次性渲染全文;打字纯文本追加,不写内联 opacity。
 */

import { useEffect, useRef, useState } from "react";

type Line = { text: string; tone: "cmd" | "dim" | "ok" | "warn" };

/* 真实 stdout 逐行(长行截断,门自身的 "...+N more" 约定沿用;行文本按类别泛化) */
const SCRIPT: Line[] = [
  { text: "$ selene publish-gate --self-check", tone: "cmd" },
  {
    text: '{"ok": false, "log": "<…>/release-gate-selfcheck.json"}',
    tone: "dim",
  },
  {
    text: "  [tier1] 实现标识符探针: 2 处命中(内部工作流文档)",
    tone: "dim",
  },
  {
    text: "  [tier2] 上游标识探针: 15 处命中(详见日志 file:line)",
    tone: "dim",
  },
  { text: "  [tier2] ...+13 more", tone: "dim" },
  {
    text: "  [secrets] 密钥形态探针: 1 处命中(待复核)",
    tone: "warn",
  },
];

const CHAR_MS = 16;
const LINE_PAUSE = 240;

export function SelfDemo() {
  const ref = useRef<HTMLDivElement>(null);
  const [started, setStarted] = useState(false);
  const [done, setDone] = useState(false);
  const [lineIdx, setLineIdx] = useState(0);
  const [charIdx, setCharIdx] = useState(0);

  /* 进入视口才开始打字;reduced-motion 直接全文 */
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setDone(true);
      return;
    }
    const obs = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setStarted(true);
          obs.disconnect();
        }
      },
      { rootMargin: "-60px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  /* 打字循环 */
  useEffect(() => {
    if (!started || done) return;
    if (lineIdx >= SCRIPT.length) {
      setDone(true);
      return;
    }
    const line = SCRIPT[lineIdx].text;
    if (charIdx < line.length) {
      const t = setTimeout(() => setCharIdx((c) => c + 1), CHAR_MS);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => {
      setLineIdx((l) => l + 1);
      setCharIdx(0);
    }, LINE_PAUSE);
    return () => clearTimeout(t);
  }, [started, done, lineIdx, charIdx]);

  const toneCls: Record<Line["tone"], string> = {
    cmd: "text-ink",
    dim: "text-ink-3",
    ok: "text-pos",
    warn: "text-gold",
  };

  const shown = done
    ? SCRIPT
    : [
        ...SCRIPT.slice(0, lineIdx),
        ...(lineIdx < SCRIPT.length
          ? [{ ...SCRIPT[lineIdx], text: SCRIPT[lineIdx].text.slice(0, charIdx) }]
          : []),
      ];

  return (
    <div>
      <div
        ref={ref}
        className="overflow-hidden rounded-lg border border-[var(--lp-hair-strong)] bg-surface-1"
      >
        <div className="flex items-center gap-2 border-b border-[var(--lp-hair)] px-4 py-3">
          <span className="num font-mono text-[0.75rem] text-ink-3">
            selene publish-gate --self-check
          </span>
          <span className="num ml-auto font-mono text-[0.6875rem] text-ink-3">
            真实运行回放 · 行按类别泛化
          </span>
        </div>
        <div
          tabIndex={0}
          role="log"
          aria-label="发布门自检终端回放(ok=false,exit 1)"
          className="h-[14.5rem] overflow-hidden px-5 py-4 font-mono text-[0.8125rem] leading-[1.9]"
        >
          {shown.map((l, i) => (
            <div key={i} className={`truncate ${toneCls[l.tone]}`}>
              {l.text}
              {!done && i === shown.length - 1 && <span className="lp-term-cursor" />}
            </div>
          ))}
        </div>
      </div>
      <p className="num mt-3 font-mono text-[0.6875rem] leading-relaxed text-ink-3">
        exit 1,门拒绝放行:实现标识符 / 上游标识 / 密钥形态三类探针有命中,
        逐条登记;paths / excluded / fixture 三项零命中。处理完重跑,不许绕过。
      </p>
    </div>
  );
}
