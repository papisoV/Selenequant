"use client";

/**
 * Research graph 环境层:hero 面板后的一张"研究系统底图"。
 * 节点类型 = 真实 DB 对象类型配色(question=amber 假设 / evidence=cyan 研究
 * / answer=blue 数据 / memory=violet 记忆),不是装饰撒点。
 * aria-hidden + pointer-events none;CSS 动画,RM 静止。
 */

const NODES: { x: number; y: number; r: number; rgb: string }[] = [
  // 问题节点(amber)
  { x: 62, y: 34, r: 3.2, rgb: "247, 160, 125" },
  { x: 200, y: 18, r: 2.6, rgb: "247, 160, 125" },
  { x: 348, y: 42, r: 2.4, rgb: "247, 160, 125" },
  // 证据节点(cyan)
  { x: 18, y: 96, r: 2.6, rgb: "34, 211, 238" },
  { x: 118, y: 120, r: 3, rgb: "34, 211, 238" },
  { x: 262, y: 92, r: 2.4, rgb: "34, 211, 238" },
  { x: 402, y: 118, r: 2.6, rgb: "34, 211, 238" },
  // 回答节点(blue)
  { x: 72, y: 186, r: 3, rgb: "48, 156, 251" },
  { x: 218, y: 168, r: 2.6, rgb: "48, 156, 251" },
  { x: 356, y: 196, r: 3.2, rgb: "48, 156, 251" },
  // 记忆节点(violet)
  { x: 148, y: 246, r: 2.6, rgb: "167, 139, 250" },
  { x: 300, y: 258, r: 2.2, rgb: "167, 139, 250" },
];

// 边:question→evidence→answer→memory 的真实链型
const EDGES: [number, number][] = [
  [0, 4],
  [0, 6],
  [1, 5],
  [2, 6],
  [3, 6],
  [4, 7],
  [5, 8],
  [6, 8],
  [6, 9],
  [7, 10],
  [8, 10],
  [9, 11],
  [8, 11],
];

const FLOW_EDGES = new Set([4, 7, 10]);

export function ResearchGraph({ className = "" }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 420 280"
      className={`lp-rgraph ${className}`}
      fill="none"
    >
      {EDGES.map(([a, b], i) => (
        <line
          key={i}
          x1={NODES[a].x}
          y1={NODES[a].y}
          x2={NODES[b].x}
          y2={NODES[b].y}
          className={FLOW_EDGES.has(i) ? "lg-flow" : undefined}
          style={
            FLOW_EDGES.has(i)
              ? { animationDelay: `${(i % 5) * -0.7}s` }
              : undefined
          }
        />
      ))}
      {NODES.map((n, i) => (
        <g key={i}>
          <circle cx={n.x} cy={n.y} r={n.r} fill={`rgb(${n.rgb} / 0.55)`} className="lg-node" stroke={`rgb(${n.rgb} / 0.25)`} />
          {/* 大节点加一圈呼吸晕(CSS transform,RM 静止) */}
          {n.r >= 3 && (
            <circle
              cx={n.x}
              cy={n.y}
              r={n.r + 4}
              fill="none"
              stroke={`rgb(${n.rgb} / 0.14)`}
              className="lp-cross-breathe"
            />
          )}
        </g>
      ))}
    </svg>
  );
}
