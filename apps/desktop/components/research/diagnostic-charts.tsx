"use client";

/** 轻量纯 SVG/div 图表原语（无图表库依赖），供 IC / 回报诊断卡复用。 */

export const fmtNum = (v: number | null | undefined, d = 4): string =>
  v == null || !Number.isFinite(v) ? "N/A" : v.toFixed(d);

export const pct = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? "N/A" : `${(v * 100).toFixed(1)}%`;

/** 纯 SVG 折线图。dates 与 values 等长；null 值断线；支持多序列。 */
export function MiniLineChart({
  dates,
  series,
  height = 100,
  format = fmtNum,
}: {
  dates: string[];
  series: { name: string; values: (number | null)[]; color: string; dashed?: boolean }[];
  height?: number;
  format?: (v: number) => string;
}) {
  const width = 320;
  const padL = 46;
  const padR = 10;
  const padT = 8;
  const padB = 20;
  const allFinite = series.flatMap((s) => s.values.filter((v): v is number => v != null));
  if (dates.length < 2 || allFinite.length === 0) {
    return <p className="py-2 text-xs text-muted-foreground">暂无足够数据绘制</p>;
  }
  let min = Math.min(...allFinite);
  let max = Math.max(...allFinite);
  if (!Number.isFinite(min) || !Number.isFinite(max) || max - min < 1e-12) {
    min = -1;
    max = 1;
  }
  const padY = (max - min) * 0.08;
  min -= padY;
  max += padY;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const x = (i: number) => padL + (i / (dates.length - 1)) * plotW;
  const y = (v: number) => padT + (1 - (v - min) / (max - min)) * plotH;

  const path = (values: (number | null)[]): string => {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v == null || !Number.isFinite(v)) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)} `;
      pen = true;
    });
    return d;
  };

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
        {series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-3" style={{ background: s.color, opacity: 0.9 }} />
            {s.name}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label={series.map((s) => s.name).join(" / ")}>
        <line x1={padL} y1={padT} x2={padL} y2={height - padB} stroke="currentColor" strokeOpacity={0.2} />
        <line x1={padL} y1={height - padB} x2={width - padR} y2={height - padB} stroke="currentColor" strokeOpacity={0.2} />
        <text x={2} y={y(max)} fontSize={9} fill="currentColor" fillOpacity={0.6}>{format(max)}</text>
        <text x={2} y={y(min)} fontSize={9} fill="currentColor" fillOpacity={0.6}>{format(min)}</text>
        <text x={padL} y={height - 6} fontSize={9} fill="currentColor" fillOpacity={0.6}>{dates[0]}</text>
        <text x={width - padR} y={height - 6} fontSize={9} fill="currentColor" fillOpacity={0.6} textAnchor="end">{dates[dates.length - 1]}</text>
        {series.map((s) => (
          <path
            key={s.name}
            d={path(s.values)}
            fill="none"
            stroke={s.color}
            strokeWidth={1.5}
            strokeDasharray={s.dashed ? "4 3" : undefined}
            strokeLinejoin="round"
          />
        ))}
      </svg>
    </div>
  );
}

/** 月度 IC 热图：纯 CSS grid 色块，正值绿 / 负值红 / null 灰。 */
export function HeatmapGrid({
  years,
  months,
  z,
  format = fmtNum,
}: {
  years: string[];
  months: string[];
  z: (number | null)[][];
  format?: (v: number) => string;
}) {
  if (years.length === 0 || months.length === 0) {
    return <p className="py-2 text-xs text-muted-foreground">暂无月度聚合数据</p>;
  }
  const allFinite = z.flat().filter((v): v is number => v != null && Number.isFinite(v));
  const maxAbs = Math.max(1e-9, ...allFinite.map((v) => Math.abs(v)));
  const cellColor = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v)) return "var(--color-border)";
    const alpha = 0.25 + 0.7 * (Math.abs(v) / maxAbs);
    return v >= 0 ? `rgba(22, 163, 74, ${alpha})` : `rgba(220, 38, 38, ${alpha})`;
  };
  return (
    <div className="grid gap-px" style={{ gridTemplateColumns: `auto repeat(${months.length}, minmax(0, 1fr))` }}>
      <div />
      {months.map((m) => (
        <div key={m} className="text-center text-[9px] text-muted-foreground">{m}</div>
      ))}
      {years.flatMap((yr, r) => [
        <div key={`y${yr}`} className="pr-1 text-[9px] text-muted-foreground">{yr}</div>,
        ...months.map((mo, c) => {
          const v = z[r]?.[c];
          return (
            <div
              key={`${yr}-${mo}`}
              title={v != null ? `${yr}-${mo}: ${format(v)}` : undefined}
              className="h-5 rounded-[2px]"
              style={{ background: cellColor(v) }}
            />
          );
        }),
      ])}
    </div>
  );
}

/** 指标小格（label + mono 数值）。 */
export function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="font-mono text-sm">{value}</div>
    </div>
  );
}
