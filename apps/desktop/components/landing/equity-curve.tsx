"use client";

/**
 * EquityCurve —— 用真实回测的 equity_curve_json 画的动画曲线。
 *
 * 这是旧 Landing 有、重构时被我错删的东西:动画曲线 / 扫描线 / 发光 / 端点脉冲。
 * 区别在数据源:旧版曲线是硬编码的假数组(EQUITY_DATA_A),这里是
 * `selenespace.db` backtests 表里真实的 equity_curve_json。
 *
 * 动效纪律(impeccable brand register):
 * - 曲线"画出来"的过程服务于语义:这条曲线是真跑出来的,不是贴图;
 * - prefers-reduced-motion 时直接静态呈现,不做任何动画;
 * - 动画只碰 stroke-dashoffset / transform / opacity,不触发布局。
 */

import { useEffect, useMemo, useRef, useState } from "react";

/** Catmull-Rom → 三次贝塞尔,把折线变成顺滑曲线。 */
function smoothPath(points: { x: number; y: number }[]): string {
  if (points.length < 2) return "";
  const d: string[] = [`M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`];
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(i - 1, 0)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(i + 2, points.length - 1)];
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d.push(
      `C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2.x.toFixed(2)},${p2.y.toFixed(2)}`,
    );
  }
  return d.join(" ");
}

export function EquityCurve({
  data,
  color,
  id,
  height = 160,
  width = 420,
  baseline,
  showScan = true,
  className = "",
}: {
  data: number[];
  color: string;
  /** SVG gradient/filter id 前缀,页面内需唯一 */
  id: string;
  height?: number;
  width?: number;
  /** 对比基准线(如买入持有),可选 */
  baseline?: number[];
  showScan?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [started, setStarted] = useState(true);
  const pad = 6;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setStarted(true);
      return;
    }
    // 与 Reveal 同纪律:SSR 默认完整呈现;只对挂载时在视口下方的实例播放画线。
    if (el.getBoundingClientRect().top <= window.innerHeight) {
      setStarted(true);
      return;
    }
    setStarted(false);
    const obs = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setStarted(true);
          obs.disconnect();
        }
      },
      { rootMargin: "-50px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const { linePath, areaPath, basePath, last } = useMemo(() => {
    if (data.length < 2) {
      return { linePath: "", areaPath: "", basePath: "", last: { x: 0, y: 0 } };
    }
    const all = baseline && baseline.length ? [...data, ...baseline] : data;
    const min = Math.min(...all);
    const max = Math.max(...all);
    const range = max - min || 1;
    const toPts = (arr: number[]) =>
      arr.map((v, i) => ({
        x: pad + (i / Math.max(arr.length - 1, 1)) * (width - pad * 2),
        y: height - pad - ((v - min) / range) * (height - pad * 2),
      }));
    const pts = toPts(data);
    const lp = smoothPath(pts);
    const lastPt = pts[pts.length - 1];
    const ap = `${lp} L${lastPt.x.toFixed(2)},${height - pad} L${pad},${height - pad} Z`;
    const bp =
      baseline && baseline.length ? smoothPath(toPts(baseline)) : "";
    return { linePath: lp, areaPath: ap, basePath: bp, last: lastPt };
  }, [data, baseline, width, height, pad]);

  return (
    <div ref={ref} className={className}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <linearGradient id={`area-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
          <linearGradient id={`stroke-${id}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={color} stopOpacity="0.35" />
            <stop offset="45%" stopColor={color} stopOpacity="1" />
            <stop offset="100%" stopColor={color} stopOpacity="0.95" />
          </linearGradient>
          <filter id={`glow-${id}`} x="-20%" y="-40%" width="140%" height="180%">
            <feGaussianBlur stdDeviation="3.5" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* 基准网格 */}
        {[0.25, 0.5, 0.75].map((r) => (
          <line
            key={r}
            x1={pad}
            y1={height * r}
            x2={width - pad}
            y2={height * r}
            stroke="rgba(255,255,255,0.035)"
            strokeWidth="0.5"
          />
        ))}

        {/* 对比基准(买入持有) */}
        {basePath && (
          <path
            d={basePath}
            fill="none"
            stroke="rgba(255,255,255,0.22)"
            strokeWidth="1"
            strokeDasharray="3 3"
            style={{
              opacity: started ? 1 : 0,
              transition: "opacity 0.9s ease 0.5s",
            }}
          />
        )}

        {/* 面积 */}
        <path
          d={areaPath}
          fill={`url(#area-${id})`}
          style={{
            opacity: started ? 1 : 0,
            transition: "opacity 1.1s ease 0.35s",
          }}
        />

        {/* 主曲线:pathLength 归一化 → dashoffset 1→0 即"画出来" */}
        <path
          d={linePath}
          fill="none"
          stroke={`url(#stroke-${id})`}
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          filter={`url(#glow-${id})`}
          pathLength={1}
          strokeDasharray={1}
          style={{
            strokeDashoffset: started ? 0 : 1,
            transition: "stroke-dashoffset 1.7s cubic-bezier(0.22,1,0.36,1)",
          }}
        />

        {/* 扫描线:沿线扫过,强调"这是跑出来的" */}
        {showScan && (
          <rect
            x={pad}
            y={pad}
            width={44}
            height={height - pad * 2}
            fill={color}
            opacity="0.07"
            style={{
              transform: started
                ? `translateX(${width - pad * 2 + 44}px)`
                : "translateX(-44px)",
              transition: started
                ? "transform 1.9s cubic-bezier(0.5,0,0.2,1)"
                : "none",
            }}
          />
        )}

        {/* 端点:脉冲光环 + 十字准星 */}
        <g
          style={{
            opacity: started ? 1 : 0,
            transition: "opacity 0.5s ease 1.5s",
          }}
        >
          <circle cx={last.x} cy={last.y} r="7" fill="none" stroke={color} strokeWidth="0.6" opacity="0.3" className="lp-smil-dot">
            <animate attributeName="r" values="6;13;6" dur="2.4s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.3;0;0.3" dur="2.4s" repeatCount="indefinite" />
          </circle>
          <circle cx={last.x} cy={last.y} r="2.6" fill={color} />
          <line x1={last.x - 9} y1={last.y} x2={last.x - 5} y2={last.y} stroke={color} strokeWidth="0.7" opacity="0.5" />
          <line x1={last.x + 5} y1={last.y} x2={last.x + 9} y2={last.y} stroke={color} strokeWidth="0.7" opacity="0.5" />
          <line x1={last.x} y1={last.y - 9} x2={last.x} y2={last.y - 5} stroke={color} strokeWidth="0.7" opacity="0.5" />
          <line x1={last.x} y1={last.y + 5} x2={last.x} y2={last.y + 9} stroke={color} strokeWidth="0.7" opacity="0.5" />
        </g>
      </svg>
    </div>
  );
}
