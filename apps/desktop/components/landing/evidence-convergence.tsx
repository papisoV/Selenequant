"use client";

/**
 * 证据汇聚图 —— 四条真实实验 → 一条结论的连线可视化。
 *
 * 放在结论卡上方:四条流线从左侧(实验链方向)汇入右侧单点(结论)。
 * 每条线 = 一个真实证据 id,粒子沿 SMIL animateMotion 流动;
 * hover 联动由父级 TheLoop 的 active 状态驱动(轴节点 ↔ 线 ↔ 引用行)。
 * reduced-motion:线保留,粒子静止(SMIL 由 CSS 侧无法停;此处粒子
 * 挂载后才渲染,且 reduced-motion 时整体不渲染流动层)。
 */

import { useReducedMotion } from "framer-motion";
import { LOOP_EVIDENCE } from "@/lib/landing/real-data";

const W = 400;
const H = 132;
const SINK = { x: 330, y: 66 };

function linePath(y: number) {
  return `M 0 ${y} C 150 ${y}, 210 ${SINK.y}, ${SINK.x} ${SINK.y}`;
}

export function EvidenceConvergence({
  active,
  onActive,
}: {
  active: string | null;
  onActive: (id: string | null) => void;
}) {
  const reduce = useReducedMotion();

  return (
    <div
      className="relative"
      role="img"
      aria-label="四条实验证据汇入一条结论的连线图"
      onMouseLeave={() => onActive(null)}
    >
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" aria-hidden>
        {LOOP_EVIDENCE.map((ev, i) => {
          const y = 18 + i * 32;
          const d = linePath(y);
          const isActive = active === ev.id;
          return (
            <g key={ev.id}>
              <path
                d={d}
                fill="none"
                stroke={
                  isActive ? "rgba(34,211,238,0.75)" : "rgba(34,211,238,0.2)"
                }
                strokeWidth={isActive ? 1.8 : 1}
                className="lp-edge-line"
              />
              {/* 命中区加宽,便于 hover */}
              <path
                d={d}
                fill="none"
                stroke="transparent"
                strokeWidth={14}
                style={{ cursor: "crosshair" }}
                onMouseEnter={() => onActive(ev.id)}
              />
            </g>
          );
        })}

        {/* 汇聚点 */}
        <circle
          cx={SINK.x}
          cy={SINK.y}
          r={5}
          fill="none"
          stroke="rgba(247,160,125,0.85)"
          strokeWidth={1.5}
        />

        {/* 流动粒子(进入视口即流;reduced-motion 不渲染) */}
        {!reduce &&
          LOOP_EVIDENCE.map((ev, i) => (
            <circle key={ev.id} r={2.4} fill="#22d3ee" opacity={0.9}>
              <animateMotion
                dur="2.8s"
                repeatCount="indefinite"
                begin={`${i * 0.7}s`}
                path={linePath(18 + i * 32)}
              />
            </circle>
          ))}

        {/* 汇聚点标签 */}
        <text
          x={SINK.x + 12}
          y={SINK.y + 4}
          fontSize={11}
          fill="rgba(214,221,230,0.7)"
          fontFamily="var(--font-mono, monospace)"
        >
          v1 结论
        </text>
      </svg>
    </div>
  );
}
