"use client";

/**
 * 记忆回流可视化 —— "知识被取回"这一动作的仪器化表达。
 *
 * 数据 = MEMORY_ENTRIES 真实反向索引。两条有下游引用的知识(rev-f812…/rev-85e8…)
 * 各有一条真实回流路径:知识条目 →(取回)→ 引用它的下游版本。
 * 流动粒子沿 SVG path 用 SMIL animateMotion 前进(无 JS 循环、无内联 opacity);
 * reduced-motion / 无 SMIL 时线仍在,只是不流动。
 *
 * 布局:三条知识为三个源点,横线汇入右侧"下一次判断"节点;
 * usedCount=0 的行如实画为断线(灰,无粒子)—— 军规⑤"空显示为空"。
 */

import { MEMORY_ENTRIES } from "@/lib/landing/real-data";
import { useUiText } from "@/components/i18n/use-ui-text"

const ROW_H = 72;
const W = 560;

export function MemoryFlow() {
  const tr = useUiText()

  const rows = MEMORY_ENTRIES.slice(0, 3);
  const H = rows.length * ROW_H + 16;

  return (
    <div
      className="mx-auto mt-10 max-w-2xl"
      role="img"
      aria-label={tr("记忆回流示意:两条知识被下游回答各取回 1 次,一条尚无引用")}
    >
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full min-w-[520px]"
          aria-hidden
        >
          <defs>
            <linearGradient id="mf-flow" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="rgba(167,139,250,0)" />
              <stop offset="0.5" stopColor="rgba(167,139,250,0.55)" />
              <stop offset="1" stopColor="rgba(167,139,250,0)" />
            </linearGradient>
          </defs>

          {rows.map((e, i) => {
            const y = i * ROW_H + ROW_H / 2;
            const recalled = e.usedCount > 0;
            // 被取回的行汇入右侧中心汇点;未取回的画断头线
            const sinkY = H / 2;
            const mid = W * 0.62;
            const d = recalled
              ? `M 150 ${y} C ${mid} ${y}, ${mid} ${sinkY}, ${W - 130} ${sinkY}`
              : `M 150 ${y} H ${mid}`;
            return (
              <g key={e.revisionId}>
                {/* 知识源点 */}
                <circle cx={150} cy={y} r={4} fill="rgba(167,139,250,0.75)" />
                {/* 回流路径:被引用 = 流光线;未被引用 = 灰虚线断头 */}
                <path
                  d={d}
                  fill="none"
                  stroke={recalled ? "url(#mf-flow)" : "rgba(167,139,250,0.12)"}
                  strokeWidth={recalled ? 1.5 : 1}
                  strokeDasharray={recalled ? "none" : "3 5"}
                />
                {/* 流动粒子(SMIL,循环;断线无粒子)
                    lp-smil-dot:reduced-motion 下由 CSS 隐藏(media query 覆盖不到 SMIL) */}
                {recalled && (
                  <circle r={2.6} fill="#a78bfa" className="lp-smil-dot">
                    <animateMotion
                      dur="3.2s"
                      repeatCount="indefinite"
                      begin={`${i * 0.9}s`}
                      path={d}
                    />
                  </circle>
                )}
                {/* 左标签:revision id */}
                <text
                  x={136}
                  y={y + 4}
                  textAnchor="end"
                  fontSize={11}
                  fill="rgba(214,221,230,0.55)"
                  fontFamily="var(--font-mono, monospace)"
                >
                  {e.revisionId.slice(0, 12)}
                </text>
              </g>
            );
          })}

          {/* 右侧汇点:下一次回答(两条被取回知识的共同去向) */}
          <circle cx={W - 130} cy={H / 2} r={5} fill="none" stroke="rgba(167,139,250,0.8)" strokeWidth={1.5} />
          <circle cx={W - 130} cy={H / 2} r={10} fill="none" stroke="rgba(167,139,250,0.25)" strokeWidth={1} />
          <text
            x={W - 114}
            y={H / 2 + 4}
            fontSize={12}
            fill="rgba(214,221,230,0.85)"
            fontFamily="var(--font-mono, monospace)"
          >
            {tr("下一次回答 q-e31d… · v2")}
          </text>
        </svg>
      </div>
      <p className="num mt-3 text-center font-mono text-[0.6875rem] text-ink-3">
        {tr("虚线 = 已入账但尚无下游引用(如实画断) · 流点 = 真实取回事件")}
      </p>
    </div>
  );
}
