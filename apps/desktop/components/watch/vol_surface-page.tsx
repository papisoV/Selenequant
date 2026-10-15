"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 设计稿扩展页 — 波动率曲面
// 端点: /api/v1/options/vol-surface → { underlying, ts, points: [{expiry, strike, iv, iv_change?, volume?}], expiries[], strikes[] }
interface VolPoint {
  expiry: string;
  strike: number;
  iv: number;
  iv_change?: number | null;
  volume?: number | null;
}
interface VolSurfaceData {
  underlying?: string;
  ts?: number;
  points?: VolPoint[];
  expiries?: string[];
  strikes?: number[];
}

const W = 560, H = 220, PAD_L = 44, PAD_R = 12, PAD_T = 14, PAD_B = 28;
const COLORS = ["#6b8eff", "#4fd1c5", "#f6c343", "#ff8a3d", "#f56565"];
const fmtIv = (v: number) => `${(v * 100).toFixed(1)}%`;
const fmtStrike = (v: number) => (v >= 1000 ? v.toFixed(0) : v.toFixed(2));

function buildCurves(points: VolPoint[]) {
  const byStrike = new Map<string, Map<string, number>>();
  const byExpiry = new Map<string, Map<number, number>>();
  for (const p of points) {
    if (!p.iv || p.iv <= 0) continue;
    const sk = fmtStrike(p.strike);
    if (!byStrike.has(sk)) byStrike.set(sk, new Map());
    byStrike.get(sk)!.set(p.expiry, p.iv);
    if (!byExpiry.has(p.expiry)) byExpiry.set(p.expiry, new Map());
    byExpiry.get(p.expiry)!.set(p.strike, p.iv);
  }
  return { byStrike, byExpiry };
}

// 最多 5 条曲线,均匀采样
function pickKeys<T>(arr: T[]): T[] {
  if (arr.length <= 5) return arr;
  const out: T[] = [arr[0]];
  const step = (arr.length - 1) / 4;
  for (let i = 1; i < 4; i++) out.push(arr[Math.round(step * i)]);
  out.push(arr[arr.length - 1]);
  return out;
}

function linePath(xs: string[], ivMap: Map<string, number>, yMin: number, yMax: number): string {
  if (xs.length === 0 || yMax <= yMin) return "";
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const pts = xs
    .map((x, i) => {
      const iv = ivMap.get(x);
      if (iv == null) return null;
      const px = PAD_L + (xs.length === 1 ? innerW / 2 : (i / (xs.length - 1)) * innerW);
      const py = PAD_T + innerH - ((iv - yMin) / (yMax - yMin)) * innerH;
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    })
    .filter((p): p is string => p !== null);
  return pts.length ? `M${pts.join(" L")}` : "";
}

function AxisGrid({ yTicks }: { yTicks: number[] }) {
  const innerH = H - PAD_T - PAD_B;
  return (
    <>
      {yTicks.map((t, i) => {
        const py = PAD_T + innerH - (i / (yTicks.length - 1)) * innerH;
        return (
          <g key={i}>
            <line x1={PAD_L} y1={py} x2={W - PAD_R} y2={py} stroke="var(--border, #2a2d3a)" strokeWidth={0.5} strokeDasharray="2 3" />
            <text x={PAD_L - 6} y={py + 3} textAnchor="end" fontSize={9} fill="var(--ink-3, #6b7180)" fontFamily="monospace">{fmtIv(t)}</text>
          </g>
        );
      })}
    </>
  );
}

function XLabels({ xs, trim }: { xs: string[]; trim?: boolean }) {
  const innerW = W - PAD_L - PAD_R;
  return (
    <>
      {xs.map((x, i) => {
        const px = PAD_L + (xs.length === 1 ? innerW / 2 : (i / (xs.length - 1)) * innerW);
        const label = trim && x.length > 8 ? x.slice(5) : x;
        return (
          <text key={i} x={px} y={H - PAD_B + 14} textAnchor="middle" fontSize={9} fill="var(--ink-3, #6b7180)" fontFamily="monospace">{label}</text>
        );
      })}
    </>
  );
}

function Legend({ items, prefix }: { items: string[]; prefix?: string }) {
  return (
    <div className="flex flex-wrap gap-2 mt-1">
      {items.map((it, idx) => (
        <span key={it} className="flex items-center gap-1 text-[9px] text-ink-3 font-mono">
          <span className="inline-block w-2 h-2 rounded-sm" style={{ background: COLORS[idx % COLORS.length] }} />
          {prefix}{it}
        </span>
      ))}
    </div>
  );
}

export function VolSurfacePage() {
  const { data } = useApi<VolSurfaceData | null>("/api/v1/options/vol-surface");
  const d = data ?? null;
  const points = d?.points ?? [];
  const expiries = d?.expiries ?? [];
  const strikes = d?.strikes ?? [];
  const hasData = points.length > 0 && expiries.length > 0 && strikes.length > 0;

  const ivs = points.map((p) => p.iv).filter((v) => v > 0);
  const avgIv = ivs.length ? ivs.reduce((a, b) => a + b, 0) / ivs.length : 0;
  const maxIv = ivs.length ? Math.max(...ivs) : 0;
  const minIv = ivs.length ? Math.min(...ivs) : 0;
  const totalVol = points.reduce((s, p) => s + (p.volume ?? 0), 0);

  const { byStrike, byExpiry } = buildCurves(points);
  const strikeKeys = pickKeys(Array.from(byStrike.keys()).sort((a, b) => parseFloat(a) - parseFloat(b)));
  const expiryKeys = pickKeys(Array.from(byExpiry.keys()).sort());

  const yMin = minIv > 0 ? Math.max(0, minIv * 0.9) : 0;
  const yMax = maxIv > 0 ? maxIv * 1.05 : 1;
  const yTicks = [yMin, yMin + (yMax - yMin) * 0.5, yMax];
  const strXs = strikes.map((s) => fmtStrike(s));

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="波动率概览">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="标的" value={d?.underlying ?? "—"} cls="text-gold" />
          <StatBlock label="数据点数" value={hasData ? String(points.length) : "—"} />
          <StatBlock label="IV 均值" value={avgIv > 0 ? fmtIv(avgIv) : "—"} />
          <StatBlock label="IV 区间" value={hasData ? `${fmtIv(minIv)} - ${fmtIv(maxIv)}` : "—"} />
        </div>
        {!hasData && (
          <div className="text-center text-ink-4 text-[11px] py-2 mt-1 border border-dashed border-border rounded">
            数据不足,显示降级视图 — 等待期权链 ticker 接入
          </div>
        )}
      </V17Card>

      <V17Card title="期限结构 (Term Structure)">
        <div className="flex flex-col gap-1">
          {!hasData || strikeKeys.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          ) : (
            <>
              <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
                <AxisGrid yTicks={yTicks} />
                <XLabels xs={expiries} trim />
                {strikeKeys.map((sk, idx) => {
                  const path = linePath(expiries, byStrike.get(sk)!, yMin, yMax);
                  return path ? (
                    <path key={sk} d={path} fill="none" stroke={COLORS[idx % COLORS.length]} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" />
                  ) : null;
                })}
              </svg>
              <Legend items={strikeKeys} prefix="K=" />
              <div className="text-[9px] text-ink-4 mt-0.5">X: 到期日 · Y: 隐含波动率 · 每条线 = 一个 strike</div>
            </>
          )}
        </div>
      </V17Card>

      <V17Card title="偏度 (Skew)">
        <div className="flex flex-col gap-1">
          {!hasData || expiryKeys.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          ) : (
            <>
              <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
                <AxisGrid yTicks={yTicks} />
                <XLabels xs={strXs} />
                {expiryKeys.map((ex, idx) => {
                  const raw = byExpiry.get(ex)!;
                  const labeled = new Map<string, number>();
                  for (const [k, v] of raw) labeled.set(fmtStrike(k), v);
                  const path = linePath(strXs, labeled, yMin, yMax);
                  return path ? (
                    <path key={ex} d={path} fill="none" stroke={COLORS[idx % COLORS.length]} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" />
                  ) : null;
                })}
              </svg>
              <Legend items={expiryKeys} />
              <div className="text-[9px] text-ink-4 mt-0.5">
                X: 行权价 · Y: 隐含波动率 · 每条线 = 一个到期日 · 总成交 {totalVol.toLocaleString()}
              </div>
            </>
          )}
        </div>
      </V17Card>
    </div>
  );
}
