"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
} from "lightweight-charts";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { chartColors } from "@/lib/theme/chart-colors";
import {
  appearanceToChartOptions,
  appearanceToSeriesOptions,
  defaultGridColor,
} from "@/lib/chart/chart-appearance";
import { useDrawingsStore } from "@/lib/store/drawings";
import { useUiText, useLocale } from "@/components/i18n/use-ui-text";

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

function ma(arr: number[], n: number): { start: number; values: number[] } {
  const values: number[] = [];
  for (let i = n - 1; i < arr.length; i++) {
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += arr[j];
    values.push(s / n);
  }
  return { start: n - 1, values };
}

/** 无 t 字段时的兜底时间基（固定值，保证同一次渲染内单调且可比）。 */
const FALLBACK_T0 = 1700000000;
const FALLBACK_STEP = 3600;

const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

/**
 * 十字线 tooltip 的 DOM 片段。抽成纯字符串函数：
 * LWC 把图渲染进 canvas，悬停事件不经过 DOM，jsdom 里既没有真指针也没有
 * canvas 后端 —— 只有把这段内容生成逻辑独立出来才能单测，否则 tooltip 的
 * 数字口径（涨跌色、百分比、正负号）会彻底落在测试盲区。
 */
export function buildTipHtml(row: Candle, timeSec: number): string {
  const chg = row.close - row.open;
  const chgPct = row.open !== 0 ? (chg / row.open) * 100 : 0;
  const up = chg >= 0;
  const color = up ? chartColors.up : chartColors.down;
  return (
    `<div class="kf-tip-time">${new Date(timeSec * 1000).toLocaleString("zh-CN", { hour12: false })}</div>`
    + `<div class="kf-tip-row">O <span>${fmt(row.open)}</span></div>`
    + `<div class="kf-tip-row">H <span>${fmt(row.high)}</span></div>`
    + `<div class="kf-tip-row">L <span>${fmt(row.low)}</span></div>`
    + `<div class="kf-tip-row">C <span>${fmt(row.close)}</span></div>`
    + `<div class="kf-tip-chg" style="color:${color}">${up ? "+" : ""}${fmt(chg)} (${up ? "+" : ""}${chgPct.toFixed(2)}%)</div>`
  );
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

/**
 * 多市场 K 线图（非加密：A股/美股/港股/大宗）。
 *
 * 批2.3：渲染层从手绘 SVG 换成 lightweight-charts —— 与加密图（KfChart）同引擎。
 * 换的原因不是"图表库更好看"，而是交互能力差了一整个量级：缩放、平移、原生十字线、
 * 价格轴自动刻度、以及后续"画价格线 → 生成预警"依赖的 createPriceLine，
 * 在 SVG 手绘版里都得自己造一遍。保留全部对外 props，两个消费方零改动即升级。
 */
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
  const tr = useUiText();
  const locale = useLocale();
  const candles = useMemo(
    () => (bars && bars.length ? bars : allowFallback ? genCandles(sym.charCodeAt(0) + tf.length, 60) : []),
    [bars, sym, tf, allowFallback]
  );
  const [layout, setLayout] = useState<"1" | "2" | "4">("1");
  const hostRef = useRef<HTMLDivElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const maRef = useRef<ISeriesApi<"Line"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  /** time → 该根 K 线，供十字线回调 O(1) 取数（LWC 回调只给 time/point）。 */
  const rowsRef = useRef<Map<number, Candle>>(new Map());
  /** 已自适应过视口的「标的|周期」；null = 尚无内容，下一次拿到数据要自适应。 */
  const fittedKeyRef = useRef<string | null>(null);

  // 建图：只做一次。容器常驻（空数据时也渲染），避免 effect 依赖 DOM 出现时机。
  // 外观取 store 快照（不进依赖），变更走下面的 applyOptions effect —— 与 kf-chart 同口径。
  const appearance = useDrawingsStore((s) => s.appearance);
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const look = useDrawingsStore.getState().appearance;
    const chart = createChart(host, {
      width: Math.max(host.clientWidth, 320),
      height: Math.max(host.clientHeight, 240),
      layout: {
        background: { color: look.background.kind === "solid" ? look.background.color : "transparent" },
        textColor: "#a0a8b8",
        fontSize: 9,
      },
      grid: {
        vertLines: { color: look.gridColor || defaultGridColor(), visible: look.gridVisible },
        horzLines: { color: look.gridColor || defaultGridColor(), visible: look.gridVisible },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "rgba(255,255,255,0.08)" },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
    });
    // 蜡烛色跟 store;MA/成交量仍走 chartColors —— 外观面板只管蜡烛,不接管指标配色。
    const series = chart.addSeries(CandlestickSeries, appearanceToSeriesOptions(look));
    const maSeries = chart.addSeries(LineSeries, {
      color: chartColors.gold,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const volSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

    // 原生十字线 + 自绘 tooltip：与加密图（kf-chart）口径一致
    const onCrosshair = (param: Parameters<Parameters<typeof chart.subscribeCrosshairMove>[0]>[0]) => {
      const tip = tipRef.current;
      if (!tip) return;
      const host2 = hostRef.current;
      if (!param.time || !param.point || !host2) {
        tip.style.display = "none";
        return;
      }
      const row = rowsRef.current.get(param.time as number);
      if (!row) {
        tip.style.display = "none";
        return;
      }
      tip.innerHTML = buildTipHtml(row, param.time as number);
      tip.style.display = "block";
      const tipW = 140;
      const left = param.point.x + tipW + 12 > host2.clientWidth ? param.point.x - tipW - 8 : param.point.x + 12;
      tip.style.left = `${Math.max(4, left)}px`;
      tip.style.top = `${Math.max(4, param.point.y - 10)}px`;
    };
    chart.subscribeCrosshairMove(onCrosshair);

    chartRef.current = chart;
    seriesRef.current = series;
    maRef.current = maSeries;
    volRef.current = volSeries;

    const syncSize = () => {
      if (!hostRef.current || !chartRef.current) return;
      chartRef.current.applyOptions({
        width: Math.max(hostRef.current.clientWidth, 50),
        height: Math.max(hostRef.current.clientHeight, 50),
      });
    };
    const ro = new ResizeObserver(syncSize);
    ro.observe(host);
    window.addEventListener("resize", syncSize);

    return () => {
      window.removeEventListener("resize", syncSize);
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      maRef.current = null;
      volRef.current = null;
    };
  }, []);

  // 外观变更 → 就地改选项（不重建图）。
  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;
    chart.applyOptions(appearanceToChartOptions(appearance));
    series.applyOptions(appearanceToSeriesOptions(appearance));
  }, [appearance]);

  // 灌数据：只 setData，不重建图（重建会丢视区与十字线订阅）
  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;

    if (candles.length === 0) {
      series.setData([]);
      maRef.current?.setData([]);
      volRef.current?.setData([]);
      rowsRef.current = new Map();
      // 内容清空了：下一次拿到数据要重新自适应（视为"首次可见"）。
      fittedKeyRef.current = null;
      return;
    }

    const rows = candles.map((c, i) => ({
      row: c,
      time: (c.t ?? FALLBACK_T0 + i * FALLBACK_STEP) as UTCTimestamp,
    }));
    rowsRef.current = new Map(rows.map((r) => [r.time as number, r.row]));

    series.setData(
      rows.map((r) => ({
        time: r.time,
        open: r.row.open,
        high: r.row.high,
        low: r.row.low,
        close: r.row.close,
      }))
    );

    const closes = candles.map((c) => c.close);
    const s = ma(closes, 20);
    maRef.current?.setData(rows.slice(s.start).map((r, i) => ({ time: r.time, value: s.values[i] })));

    const maxV = Math.max(...candles.map((c) => c.volume ?? 0), 1);
    volRef.current?.setData(
      rows.map((r) => ({
        time: r.time,
        value: r.row.volume ?? 0,
        color: r.row.up ? chartColors.upSoft : chartColors.downSoft,
      }))
    );

    // 视口只在"首次可见 / 换标的 / 换周期"时自适应一次。
    // 裸调 fitContent() 会让流式刷新（同标的同周期每秒多一根）把用户刚做的缩放
    // 和平移按回去 —— 症状是"一放手就弹回全览"。刷新数据 ≠ 重置视口。
    const fitKey = `${sym}|${tf}`;
    if (fittedKeyRef.current !== fitKey) {
      chart.timeScale().fitContent();
      fittedKeyRef.current = fitKey;
    }
  }, [candles, sym, tf]);

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

  const empty = candles.length === 0;
  const last = empty ? null : candles[candles.length - 1];
  const lastCls = last && last.up ? "up" : "down";
  const delta = last ? last.close - last.open : 0;

  return (
    <div className="kline" role="img" aria-label={`${label} ${tr("K线图")}`}>
      <div className="kline-cell-head">
        <span className="kline-cell-sym">{label}</span>
        {chips.length > 0 && (
          <span className="kline-cell-tf">
            {tr("主")} · <span className="cell-tf-group">{chips}</span>
          </span>
        )}
        <div className="chart-tool-layout" role="group" aria-label="图表布局">
          {([["1", "▢", "单图"], ["2", "◫", "主+辅 2 图"], ["4", "⌗", "4 宫格"]] as const).map(([l, icon, title]) => (
            <button
              key={l}
              type="button"
              className={`chart-layout-btn ${layout === l ? "active" : ""}`}
              data-layout={l}
              aria-pressed={layout === l}
              title={tr(title)}
              onClick={() => setLayout(l)}
            >
              {icon}
            </button>
          ))}
        </div>
      </div>
      <div className="kline-main">
        <div ref={hostRef} data-testid="kline-canvas" className="kline-canvas" />
        {empty && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-[11px] text-ink-4">{tr("无数据")}</p>
          </div>
        )}
        {last && (
          <div className="kline-ohlc">
            {last.t != null && (
              <span className="kline-last-date text-ink-4">
                {new Date(last.t * 1000).toLocaleString(locale === "en" ? "en-US" : "zh-CN", { hour12: false })}
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
              {delta.toFixed(1)}
            </span>
          </div>
        )}
        {/* 十字线浮层：内容由 buildTipHtml 生成，默认隐藏 */}
        <div
          ref={tipRef}
          className="kf-cross-tip"
          data-testid="kline-cross-tip"
          style={{ position: "absolute", display: "none", zIndex: 20, pointerEvents: "none", minWidth: 128 }}
        />
      </div>
    </div>
  );
}
