"use client";

import { preload } from "swr";
import { useApi, fetcher } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 业务域插入页 — 清算热力图 (Z-score 归一化密度网格)
// 后端: GET /api/v1/heatmap/current → HeatmapMatrix
// { exchange, symbol, leverage_type, cells:[{price_level,time_offset,z_score,raw_density}],
//   price_min, price_max, price_step, time_step_ms, window_count, timestamp }
// 无数据时后端返回 {message: "Insufficient data..."} 或 {message: "Heatmap aggregator not available"}

interface HeatmapCell {
  price_level?: number;
  time_offset?: number;
  z_score?: number;
  raw_density?: number;
}
interface HeatmapMatrix {
  exchange?: string;
  symbol?: string;
  leverage_type?: string;
  cells?: HeatmapCell[];
  price_min?: number;
  price_max?: number;
  price_step?: number;
  time_step_ms?: number;
  window_count?: number;
  timestamp?: number;
  message?: string;
}

// z_score → RGB 颜色 (正=绿, 负=红, 强度按 |z| 线性映射)
function zScoreColor(z: number): string {
  const zc = Math.max(-3, Math.min(3, z));
  const intensity = Math.min(1, Math.abs(zc) / 3);
  if (zc >= 0) {
    // 绿色色阶 0 → 深绿
    const g = Math.round(40 + intensity * 175);
    return `rgb(20, ${g}, 60)`;
  }
  // 红色色阶 0 → 深红
  const r = Math.round(60 + intensity * 195);
  return `rgb(${r}, 30, 40)`;
}

function fmtPrice(p: number | undefined): string {
  if (p == null || !isFinite(p)) return "—";
  if (p >= 1000) return p.toFixed(0);
  if (p >= 1) return p.toFixed(2);
  return p.toFixed(4);
}

// hover 预载:预热静态端点进 SWR global cache
// preload 不做 catch，预载失败必须自己认领，否则成为 unhandledRejection。
export function preloadData() {
  preload("/api/v1/heatmap/current", fetcher).catch(() => {});
}

export function HeatmapPage() {
  const tr = useUiText()

  const { data } = useApi<HeatmapMatrix | null>("/api/v1/heatmap/current");
  const m = data ?? null;

  const cells = m?.cells ?? [];
  const windowCount = m?.window_count ?? 0;
  const hasGrid = cells.length > 0 && (m?.price_step ?? 0) > 0 && windowCount > 0;

  // 计算 Y 轴行数 (价格档位)
  const rowKeys = new Set<number>();
  for (const c of cells) rowKeys.add(c.price_level ?? 0);
  const rows = Array.from(rowKeys).sort((a, b) => b - a); // 高价在上
  const rowCount = rows.length;

  // 价格档位 → 行索引
  const rowIdx = new Map<number, number>();
  rows.forEach((p, i) => rowIdx.set(p, i));

  // 列数 = window_count, time_offset 0..N-1
  const colCount = windowCount;

  // SVG 几何
  const cellW = 14;
  const cellH = 14;
  const padL = 64;  // 左侧价格标签
  const padB = 24;  // 底部时间标签
  const padT = 8;
  const padR = 8;
  const svgW = padL + colCount * cellW + padR;
  const svgH = padT + rowCount * cellH + padB;

  // 最大密度 (用于 tooltip 文本)
  const maxDensity = cells.reduce((mx, c) => Math.max(mx, c.raw_density ?? 0), 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("清算热力图")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock
            label={tr("交易对")}
            value={m?.symbol ?? "—"}
            cls="text-gold"
          />
          <StatBlock label={tr("交易所")} value={m?.exchange ?? "—"} />
          <StatBlock label={tr("杠杆类型")} value={m?.leverage_type ?? "—"} />
          <StatBlock
            label={tr("价格区间")}
            value={hasGrid ? `${fmtPrice(m?.price_min)} ~ ${fmtPrice(m?.price_max)}` : "—"}
          />
        </div>
      </V17Card>

      <V17Card title={tr("密度网格 (Z-score 归一化)")}>
        {!hasGrid && (
          <div className="text-center text-ink-4 text-[12px] py-6">
            {m?.message ?? tr("暂无数据 (需要 2+ LiqMap 快照)")}
          </div>
        )}
        {hasGrid && (
          <div className="overflow-x-auto border border-border-soft bg-surface rounded p-2">
            <svg
              width={svgW}
              height={svgH}
              style={{ display: "block", minWidth: "100%" }}
              role="img"
              aria-label="liquidation heatmap density grid"
            >
              {/* 单元格 */}
              {cells.map((c, i) => {
                const r = rowIdx.get(c.price_level ?? 0) ?? 0;
                const col = c.time_offset ?? 0;
                const x = padL + col * cellW;
                const y = padT + r * cellH;
                const z = c.z_score ?? 0;
                return (
                  <rect
                    key={i}
                    x={x}
                    y={y}
                    width={cellW - 1}
                    height={cellH - 1}
                    fill={zScoreColor(z)}
                  >
                    <title>
                      {tr("价格 {p0} | t-{col} | z={p2} | 密度 {p3}", { p0: fmtPrice(c.price_level), col: col, p2: z.toFixed(2), p3: (c.raw_density ?? 0).toFixed(0) })}
                    </title>
                  </rect>
                );
              })}

              {/* Y 轴价格标签 (首/中/末) */}
              {rowCount > 0 && [0, Math.floor(rowCount / 2), rowCount - 1].map((ri, i) => (
                <text
                  key={`y${i}`}
                  x={padL - 4}
                  y={padT + ri * cellH + cellH / 2 + 3}
                  textAnchor="end"
                  fontSize={10}
                  fill="#7a8294"
                  fontFamily="monospace"
                >
                  {fmtPrice(rows[ri])}
                </text>
              ))}

              {/* X 轴时间标签 (首/中/末) */}
              {colCount > 0 && [0, Math.floor(colCount / 2), colCount - 1].map((col, i) => (
                <text
                  key={`x${i}`}
                  x={padL + col * cellW + cellW / 2}
                  y={svgH - padB + 14}
                  textAnchor="middle"
                  fontSize={10}
                  fill="#7a8294"
                  fontFamily="monospace"
                >
                  {`t-${col}`}
                </text>
              ))}
            </svg>

            {/* 色阶图例 */}
            <div className="flex items-center gap-2 mt-2 text-[10px] text-ink-3 font-mono">
              <span>{tr("清算压力")}</span>
              <span style={{ color: "rgb(20, 215, 60)" }}>{tr("强多")}</span>
              <div
                style={{
                  width: 120,
                  height: 8,
                  background: "linear-gradient(to right, rgb(255,30,40), rgb(60,40,40), rgb(20,80,60), rgb(20,215,60))",
                  border: "1px solid #2a2f3a",
                }}
              />
              <span style={{ color: "rgb(255, 50, 60)" }}>{tr("强空")}</span>
              <span className="ml-3">{tr("峰值密度")} {maxDensity.toFixed(0)}</span>
            </div>
          </div>
        )}
      </V17Card>
    </div>
  );
}
