"use client";

import { useMemo, useState } from "react";

export interface Candle {
  open: number;
  high: number;
  low: number;
  close: number;
  up: boolean;
  volume?: number;
  /** Unix 秒（R1 契约），用于头部最新日期标签 */
  t?: number;
}

function genCandles(seed: number, n: number): Candle[] {
  const out: Candle[] = [];
  let price = 63000 + (seed % 7) * 50;
  for (let i = 0; i < n; i++) {
    const drift = Math.sin(i * 0.4 + seed) * 80 + (Math.cos(i * 0.17 + seed) * 40);
    const open = price;
    const close = price + drift + (i % 3) * 8 - 12;
    const high = Math.max(open, close) + 20 + Math.abs(Math.sin(i * 0.6)) * 40;
    const low = Math.min(open, close) - 20 - Math.abs(Math.cos(i * 0.5)) * 40;
    out.push({ open, high, low, close, up: close >= open });
    price = close;
  }
  return out;
}

function ma(arr: number[], n: number): (number | null)[] {
  const out: (number | null)[] = [];
  for (let i = 0; i < arr.length; i++) {
    if (i < n - 1) { out.push(null); continue; }
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += arr[j];
    out.push(s / n);
  }
  return out;
}

interface Props {
  sym: string;
  tf: string;
  bars?: Candle[];
  /** 头部标题（默认 `${sym}-PERP`） */
  label?: string;
  /** 周期 chips 的原始 tf 值（内部小写契约，如 ["1m","1d"]）；默认 5 个设计稿档位 */
  tfOptions?: string[];
  /** tf 值 → 展示文案（小写内部、大写展示）；未命中则原样显示 */
  tfLabels?: Record<string, string>;
  /** false 时空 bars 渲染"无数据"空态，不生成 genCandles 模拟 K 线（super-chart 专用） */
  allowFallback?: boolean;
  /** 传入后周期 chips 可点击切换（super-chart 用）；不传保持静态展示 */
  onTfChange?: (tf: string) => void;
}

export function KlineChart({
  sym,
  tf,
  bars,
  label = `${sym}-PERP`,
  tfOptions = ["5m", "15m", "1H", "4H", "1D"],
  tfLabels = {},
  allowFallback = true,
  onTfChange,
}: Props) {
  const candles = useMemo(
    () => (bars && bars.length ? bars : allowFallback ? genCandles(sym.charCodeAt(0) + tf.length, 60) : []),
    [bars, sym, tf, allowFallback]
  );
  const [layout, setLayout] = useState<"1" | "2" | "4">("1");

  const chips = tfOptions.map((t) =>
    onTfChange ? (
      <button
        key={t}
        type="button"
        data-testid={`kline-tf-chip-${t}`}
        className={`cell-tf-chip ${t === tf ? "active" : ""}`}
        onClick={() => onTfChange(t)}
      >
        {tfLabels[t] ?? t}
      </button>
    ) : (
      <span key={t} className={`cell-tf-chip ${t === tf ? "active" : ""}`}>
        {tfLabels[t] ?? t}
      </span>
    )
  );

  if (candles.length === 0) {
    return (
      <div className="kline" role="img" aria-label={`${label} K线图`}>
        <div className="kline-cell-head">
          <span className="kline-cell-sym">{label}</span>
          {chips.length > 0 && (
            <span className="kline-cell-tf">
              主 · <span className="cell-tf-group">{chips}</span>
            </span>
          )}
        </div>
        <div className="kline-main">
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-[11px] text-ink-4">无数据</p>
          </div>
        </div>
      </div>
    );
  }

  const W = 800, H = 440, VH = 340;
  const closes = candles.map((c) => c.close);
  const lows = candles.map((c) => c.low);
  const highs = candles.map((c) => c.high);
  const minP = Math.min(...lows);
  const maxP = Math.max(...highs);
  const padP = (maxP - minP) * 0.05;
  const yMin = minP - padP;
  const yMax = maxP + padP;
  const yScale = (p: number) => VH - ((p - yMin) / (yMax - yMin)) * VH;
  const xStep = W / candles.length;
  const cw = xStep * 0.6;

  const ma7 = ma(closes, 7);
  const ma25 = ma(closes, 25);
  const maPath = (arr: (number | null)[]) => {
    let d = "";
    arr.forEach((v, i) => {
      if (v == null) return;
      const x = i * xStep + xStep / 2;
      d += (d ? " L" : "M") + x + " " + yScale(v);
    });
    return d;
  };

  const gridLines = Array.from({ length: 5 }, (_, i) => {
    const y = (i * VH) / 4;
    const p = yMax - ((yMax - yMin) * i) / 4;
    return { y, p };
  });

  const last = candles[candles.length - 1];
  const lastCls = last.up ? "up" : "down";
  const delta = last.close - last.open;

  return (
    <div className="kline" role="img" aria-label={`${label} K线图`}>
      <div className="kline-cell-head">
        <span className="kline-cell-sym">{label}</span>
        {chips.length > 0 && (
          <span className="kline-cell-tf">
            主 · <span className="cell-tf-group">{chips}</span>
          </span>
        )}
        <div className="chart-tool-layout">
          {([["1", "▢", "单图"], ["2", "◫", "主+辅 2 图"], ["4", "⌗", "4 宫格"]] as const).map(([l, icon, title]) => (
            <div
              key={l}
              className={`chart-layout-btn ${layout === l ? "active" : ""}`}
              data-layout={l}
              title={title}
              onClick={() => setLayout(l)}
            >
              {icon}
            </div>
          ))}
        </div>
      </div>
      <div className="kline-main">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: "100%", height: "100%" }}>
          {gridLines.map((g, i) => (
            <g key={i}>
              <line className="kline-grid" x1="0" y1={g.y} x2={W} y2={g.y} stroke="var(--color-border-soft)" strokeWidth="0.5" />
              <text className="kline-axis-lab" x={W - 4} y={g.y + 9} textAnchor="end" fill="var(--color-ink-4)" fontSize="9" fontFamily="var(--font-mono)">
                {g.p.toFixed(0)}
              </text>
            </g>
          ))}
          <path className="kline-ma ma1" d={maPath(ma7)} fill="none" stroke="var(--color-gold)" strokeWidth="1" opacity="0.85" />
          <path className="kline-ma ma2" d={maPath(ma25)} fill="none" stroke="#4ba3f7" strokeWidth="1" opacity="0.7" />
          {candles.map((c, i) => {
            const x = i * xStep + xStep / 2;
            const yO = yScale(c.open);
            const yC = yScale(c.close);
            const yH = yScale(c.high);
            const yL = yScale(c.low);
            const top = Math.min(yO, yC);
            const h = Math.max(1, Math.abs(yO - yC));
            const color = c.up ? "var(--color-pos)" : "var(--color-neg)";
            return (
              <g key={i}>
                <line className={`kline-wick ${c.up ? "up" : "down"}`} x1={x} y1={yH} x2={x} y2={yL} stroke={color} strokeWidth="0.6" />
                <rect
                  className={`kline-candle ${c.up ? "up" : "down"}`}
                  x={x - cw / 2}
                  y={top}
                  width={cw}
                  height={h}
                  fill={color}
                  stroke={color}
                />
              </g>
            );
          })}
          {candles.map((c, i) => {
            const x = i * xStep + xStep / 2;
            const maxV = Math.max(...candles.map((k) => k.volume ?? 0), 1);
            const v = c.volume != null ? (c.volume / maxV) * 100 : 20 + Math.abs(Math.sin(i * 0.7)) * 80;
            const h = (v / 100) * 100;
            return (
              <rect
                key={`v${i}`}
                className={`vol-bar ${c.up ? "" : "down"}`}
                x={x - cw / 2}
                y={H - h}
                width={cw}
                height={h}
                fill={c.up ? "var(--color-pos)" : "var(--color-neg)"}
                opacity="0.35"
              />
            );
          })}
        </svg>
        <div className="kline-ohlc">
          {last.t != null && (
            <span className="kline-last-date text-ink-4">
              {new Date(last.t * 1000).toLocaleString("zh-CN", { hour12: false })}
            </span>
          )}
          <span>
            O <span className={`v ${lastCls}`}>{last.open?.toFixed(1) ?? "—"}</span>
          </span>
          <span>
            H <span className={`v ${lastCls}`}>{last.high?.toFixed(1) ?? "—"}</span>
          </span>
          <span>
            L <span className={`v ${lastCls}`}>{last.low?.toFixed(1) ?? "—"}</span>
          </span>
          <span>
            C <span className={`v ${lastCls}`}>{last.close?.toFixed(1) ?? "—"}</span>
          </span>
          <span className={lastCls}>
            {last.up ? "+" : ""}
            {(delta ?? 0).toFixed(1)}
          </span>
        </div>
      </div>
    </div>
  );
}
