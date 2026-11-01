import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { KILLZONE_COLORS, type KillzoneBarPoint } from "./ict-structure-overlay";

/**
 * 连续的 killzone 段。`from`/`to` 都是**落在时段内**的 bar 时间，
 * 首尾相接的 1 会合成一段，中间任意一根 0 就断段。
 */
export interface KillzoneBand {
  from: UTCTimestamp;
  to: UTCTimestamp;
}

/**
 * 0/1 逐 bar 状态 → 连续时段段。
 *
 * 分段而不是逐 bar 上色：连续 20 根 bar 的渐变色块在视觉上就是一块，
 * 逐 bar 画等于把同一次重绘做二十遍。
 */
export function buildKillzoneBands(points: readonly KillzoneBarPoint[]): KillzoneBand[] {
  const bands: KillzoneBand[] = [];
  let start: UTCTimestamp | null = null;
  let prev: UTCTimestamp | null = null;
  for (const p of points) {
    if (!Number.isFinite(p.time)) continue;
    if (p.value === 1) {
      if (start === null) start = p.time;
      prev = p.time;
    } else if (start !== null) {
      bands.push({ from: start, to: prev ?? start });
      start = null;
      prev = null;
    }
  }
  if (start !== null) bands.push({ from: start, to: prev ?? start });
  return bands;
}

type RenderTarget = Parameters<NonNullable<IPrimitivePaneRenderer["drawBackground"]>>[0];

class KillzonePaneRenderer implements IPrimitivePaneRenderer {
  private _rects: { x: number; width: number }[] = [];
  private _fill: string = KILLZONE_COLORS.band;

  update(rects: { x: number; width: number }[], fill?: string) {
    this._rects = rects;
    if (fill !== undefined) this._fill = fill;
  }

  // 只在 background 通道绘制；`draw` 明明是必填，给空实现让 LWC 调。
  draw() {}

  drawBackground(target: RenderTarget) {
    if (this._rects.length === 0) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save();
      ctx.fillStyle = this._fill;
      for (const r of this._rects) {
        const x0 = Math.max(0, r.x);
        const x1 = Math.min(mediaSize.width, r.x + r.width);
        if (x1 > x0) ctx.fillRect(x0, 0, x1 - x0, mediaSize.height);
      }
      ctx.restore();
    });
  }
}

class KillzonePaneView implements IPrimitivePaneView {
  private _renderer = new KillzonePaneRenderer();

  // 背景层：画在 K 线之下，否则蓝底会盖掉实体颜色。
  zOrder() {
    return "bottom" as const;
  }

  renderer() {
    return this._renderer;
  }

  setRects(rects: { x: number; width: number }[], fill?: string) {
    this._renderer.update(rects, fill);
  }
}

/**
 * 把 killzone 段落画成主图背景竖带。
 *
 * 不用独立窗格：时段是"时间轴上的底色"，不是第二个量纲。开窗格要么被
 * 压成几十像素的一条（读不出东西），要么抢走主图一半高度。
 *
 * ``fill`` 由调用方传入（可调不透明度）；缺省用模块常量。
 */
export class KillzonePrimitive implements ISeriesPrimitive<Time> {
  private _view = new KillzonePaneView();
  private _bands: KillzoneBand[];
  private _chart: SeriesAttachedParameter<Time>["chart"] | null = null;
  private _getTimes: () => readonly number[];
  private _fill: string | undefined;

  constructor(bands: KillzoneBand[], getTimes: () => readonly number[], fill?: string) {
    this._bands = bands;
    this._getTimes = getTimes;
    this._fill = fill;
  }

  attached(param: SeriesAttachedParameter<Time>) {
    this._chart = param.chart;
    this._recalc();
  }

  detached() {
    this._chart = null;
  }

  updateAllViews() {
    this._recalc();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this._view];
  }

  private _recalc() {
    const chart = this._chart;
    if (!chart || this._bands.length === 0) {
      this._view.setRects([], this._fill);
      return;
    }
    const ts = chart.timeScale();

    // 相邻 bar 的像素间距取中位数，作为单根宽度。
    const times = this._getTimes();
    const steps: number[] = [];
    let prev: number | null = null;
    for (const t of times) {
      const x = ts.timeToCoordinate(t as Time);
      if (x == null) {
        prev = null;
        continue;
      }
      if (prev != null && x > prev) steps.push(x - prev);
      prev = x;
    }
    if (steps.length === 0) {
      this._view.setRects([], this._fill);
      return;
    }
    steps.sort((a, b) => a - b);
    const barWidth = steps[Math.floor(steps.length / 2)];

    const rects: { x: number; width: number }[] = [];
    for (const band of this._bands) {
      const x0 = ts.timeToCoordinate(band.from as Time);
      const x1 = ts.timeToCoordinate(band.to as Time);
      if (x0 == null || x1 == null) continue;
      // 段两端各向外扩半根，让边界落在 bar 边缘而不是 bar 中心。
      const left = Math.min(x0, x1) - barWidth / 2;
      const right = Math.max(x0, x1) + barWidth / 2;
      rects.push({ x: left, width: right - left });
    }
    this._view.setRects(rects, this._fill);
  }
}

/** 由不透明度档位生成底带颜色。色相沿用 KILLZONE_COLORS.inside 的蓝色。 */
export function killzoneBandFill(opacity: number): string {
  const a = Number.isFinite(opacity) ? Math.min(0.4, Math.max(0.05, opacity)) : 0.13;
  return `rgba(95, 182, 243, ${a.toFixed(3)})`;
}
