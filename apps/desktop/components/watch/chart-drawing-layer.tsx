"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";
import type { IChartApi, ISeriesApi } from "lightweight-charts";

import { paintScene } from "@/lib/chart/drawing-canvas";
import { buildDrawingScene, hitTestScene } from "@/lib/chart/drawing-scene";
import { logicalIndexForTime, timeForLogicalIndex } from "@/lib/chart/drawing-projection";
import { type FibGrid, type FibGridMode } from "@/lib/chart/fib-object";
import { defaultLevelsForMode } from "@/lib/chart/drawing-tree";
import { magnetSnapPrice, type OhlcBar } from "@/lib/chart/magnet";
import type { MagnetMode } from "@/lib/chart/drawing-prefs";
import { drawingKeyOwner, planDrawingKey } from "@/lib/chart/drawing-keys";
import { isDrawingTool, TOOLS, type DrawingToolKind } from "@/lib/chart/tool-registry";

// 绘图图层 —— 盖在 lightweight-charts 上的一块绝对定位 canvas + 一层指针捕获。
//
// 为什么不把绘图塞进 LWC 的 series primitive:
//   primitive 只能画在某个 series 的坐标系里,而且不替我们处理"拖动锚点"这类交互。
//   自己管一块 canvas,重绘时机和命中检测都在手里,调试只看一帧场景。
//
// 坐标系换算(每条线都在算,别搞混):
//   时间 → x:先换成"分数逻辑索引"(槽位语义,见 drawing-projection.ts),
//            再交给 timeScale().logicalToCoordinate。
//   价格 → y:直接 series.priceToCoordinate。
//
// 重绘时机 = 视区变化 ∪ 尺寸变化 ∪ 数据帧结束(外部调 redraw) ∪ 对象/选中态变化。
// 数据帧那条**故意不走 React state**:WS 每 ~1s 注入一次,把 slotTimes 挂进
// state 会让整棵 KfChart 每秒重渲染一次。改为 kf-chart 在数据 effect 末尾
// 调 redraw(),图层自己去 slotRef 取最新槽位。

export interface DrawingLayerHandle {
  /** 数据帧写入后调用。价格轴随最后一根 close 变化,锚点 y 必须跟着重算。 */
  redraw: () => void;
}

export interface DrawingLayerProps {
  /** 图表容器(LWC 的宿主 div)。指针事件挂在它上面。 */
  container: HTMLDivElement | null;
  chart: IChartApi | null;
  series: ISeriesApi<"Candlestick"> | null;
  /** 图上实际排布的时间槽(秒,含补出来的空白槽)—— 由 kf-chart 在数据 effect 里写。 */
  slotRef: { current: { times: readonly number[]; stepSec: number } };
  grids: readonly FibGrid[];
  selectedIds: ReadonlySet<string>;
  precision: number;
  /** 画图态时下一次拖拽建新对象;其余值不拦截指针(不抢图表的平移/缩放)。 */
  activeTool: "select" | DrawingToolKind;
  /**
   * 磁吸档位。只在**落点/拖锚**时作用于价格,不动时间 —— 见 magnet.ts 文件头。
   */
  magnet?: MagnetMode;
  /**
   * 时间(秒)→ 该槽位的 K 线。走 ref 而不是值:WS 每 ~1s 换一次 Map,
   * 传值会让这块画布跟着整棵树每秒重订阅一次指针监听。
   */
  barsRef?: { current: Map<number, OhlcBar> };
  symbol: string;
  timeframe: string;
  onCreate: (
    start: { time: number; price: number },
    end: { time: number; price: number },
    symbol: string,
    timeframe: string,
    mode: FibGridMode,
  ) => void;
  onMove: (id: string, index: 0 | 1, anchor: { time: number; price: number }) => void;
  /**
   * 画完一个对象之后调用(仅"真的建成了"时才调,误触判据刷掉的不算)。
   * 由调用方决定要不要退出绘图态 —— 连续绘制是全局偏好,不是本层的知识。
   */
  onPlaced: () => void;
  /**
   * 点选。`mods.shift` 时 `onSelect` 的语义是"从这个对象扩选到上一次点中的对象",
   * 见 `onRangeSelect`;`mods.toggle`(⌘/Ctrl)时是加选/减选。
   */
  onSelect: (id: string | null, mods?: { shift: boolean; toggle: boolean }) => void;
  /** Delete / Backspace 删除选中(已剔除锁定对象,调用方直接删)。 */
  onDelete: (ids: readonly string[]) => void;
  /** Escape 的第三级:清空选中。前两级(半成品 / 工具)本层自己处理。 */
  onClearSelection: () => void;
  /** Escape 的第二级:卸下当前工具,回到选择态。 */
  onCancelTool: () => void;
}

interface Pending {
  start: { time: number; price: number };
  current: { time: number; price: number };
  /** 视口坐标,只用于判断"是不是真的拖了"。 */
  fromX: number;
  fromY: number;
  moved: boolean;
}

/** 预览网格的固定 id —— 不能走 createGrid,那会每帧吃掉一个计数器号。 */
const PREVIEW_ID = "__preview__";

function previewGrid(pending: Pending, mode: FibGridMode): FibGrid {
  const single = mode === "horizontal" || mode === "vertical";
  return {
    id: PREVIEW_ID,
    groupId: null,
    // 单点工具边拖边预览时,几何只认当前点 —— 起点跟着走,线才不会闪回原处。
    start: single ? pending.current : pending.start,
    end: pending.current,
    mode,
    levels: defaultLevelsForMode(mode),
    autoExtend: false,
    extendLeft: false,
    extendRight: false,
    visible: true,
    locked: false,
    timeframe: "",
    symbol: "",
    label: "",
  };
}

/**
 * 把拖拽中的锚点替换进网格列表(仅本帧渲染用)。
 *
 * 拖拽为什么不在 pointermove 里直接写 store:每次写都会 serialize 整棵树落
 * localStorage,60fps 下是每秒 60 次全量序列化 + 一整棵 React 树重渲染。
 * 拖动期间只在本地覆盖这一个数,松手时提交一次。
 */
function withDraggedAnchor(
  grids: readonly FibGrid[],
  drag: { gridId: string; index: 0 | 1; anchor: { time: number; price: number } } | null,
): readonly FibGrid[] {
  if (!drag) return grids;
  return grids.map((g) =>
    g.id === drag.gridId
      ? drag.index === 0
        ? { ...g, start: drag.anchor }
        : { ...g, end: drag.anchor }
      : g,
  );
}

export const DrawingLayer = forwardRef<DrawingLayerHandle, DrawingLayerProps>(function DrawingLayer(
  {
    container,
    chart,
    series,
    slotRef,
    grids,
    selectedIds,
    precision,
    activeTool,
    magnet = "off",
    barsRef,
    symbol,
    timeframe,
    onCreate,
    onMove,
    onSelect,
    onPlaced,
    onDelete,
    onClearSelection,
    onCancelTool,
  },
  ref,
) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  /** 正在被拖的锚点(含拖动中的实时位置)。与 pendingRef 的区别:
   *  那是"新建中的对象",这是"已有对象的把手"。 */
  const dragRef = useRef<{ gridId: string; index: 0 | 1; anchor: { time: number; price: number } } | null>(null);
  const sceneRef = useRef<ReturnType<typeof buildDrawingScene> | null>(null);
  /** 键盘归属令牌:最后 pointerdown 的那张图才响应 Delete / Escape。 */
  const keyTokenRef = useRef<object>({});

  // 订阅回调拿不到闭包里的新 props,又不想为了它们重订阅整块画布。
  const propsRef = useRef({ grids, selectedIds, precision, activeTool, symbol, timeframe, magnet });
  propsRef.current = { grids, selectedIds, precision, activeTool, symbol, timeframe, magnet };

  const toAnchor = useCallback(
    (x: number, y: number): { time: number; price: number } | null => {
      const { times, stepSec } = slotRef.current;
      if (!chart || !series || times.length === 0 || !(stepSec > 0)) return null;
      const li = chart.timeScale().coordinateToLogical(x);
      if (li === null) return null;
      const price = series.coordinateToPrice(y);
      if (price === null || !Number.isFinite(price)) return null;
      const time = timeForLogicalIndex(Number(li), times, stepSec);
      if (!Number.isFinite(time)) return null;
      const t = Math.round(time);

      // 磁吸:只改价格,时间保持光标所在槽。`yOfPrice` 用当前 series 的实时投影,
      // 视口平移/缩放后仍然是"屏幕上离光标最近的那根 OHLC"。
      const snapped = magnetSnapPrice(Number(price), barsRef?.current.get(t), propsRef.current.magnet, (p) => {
        const py = series.priceToCoordinate(p);
        return py === null ? null : Number(py);
      });

      return { time: t, price: Number(snapped ?? price) };
    },
    [chart, series, slotRef, barsRef],
  );

  const draw = useCallback(() => {
    if (rafRef.current != null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      const canvas = canvasRef.current;
      const { times, stepSec } = slotRef.current;
      if (!canvas || !chart || !series || times.length === 0 || !(stepSec > 0)) return;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const pane = chart.paneSize();
      const width = pane.width;
      const height = pane.height;
      if (!(width > 0 && height > 0)) return;

      const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
      const bw = Math.max(1, Math.round(width * dpr));
      const bh = Math.max(1, Math.round(height * dpr));
      // 只在尺寸真变时写位图尺寸 —— 重复赋相同值也会清空位图,每帧都写会闪。
      if (canvas.width !== bw) canvas.width = bw;
      if (canvas.height !== bh) canvas.height = bh;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const timeScale = chart.timeScale();
      const projector = {
        width,
        height,
        xOfTime: (t: number): number | null => {
          const x = timeScale.logicalToCoordinate(logicalIndexForTime(t, times, stepSec) as never);
          return x === null ? null : Number(x);
        },
        yOfPrice: (p: number): number | null => {
          const y = series.priceToCoordinate(p);
          return y === null ? null : Number(y);
        },
      };

      // 拖拽中的预览:和真实网格走同一套场景构造,所见即所得。
      const pending = pendingRef.current;
      const drag = dragRef.current;
      const base = withDraggedAnchor(propsRef.current.grids, drag);
      const pendingMode = isDrawingTool(propsRef.current.activeTool)
        ? TOOLS[propsRef.current.activeTool].mode
        : "price";
      const shown: readonly FibGrid[] = pending ? [...base, previewGrid(pending, pendingMode)] : base;

      const scene = buildDrawingScene(shown, projector, {
        precision: propsRef.current.precision,
        width,
        height,
        selectedIds: propsRef.current.selectedIds,
      });
      sceneRef.current = scene;
      paintScene(ctx, scene, { width, height, fontSize: 10 });
    });
  }, [chart, series, slotRef]);

  useImperativeHandle(ref, () => ({ redraw: draw }), [draw]);

  // 视区 / 尺寸变化。视区每帧都在动(拖动、缩放),rAF 合并。
  useEffect(() => {
    if (!chart) return;
    const timeScale = chart.timeScale();
    const onRange = () => draw();
    const onSize = () => draw();
    timeScale.subscribeVisibleLogicalRangeChange(onRange);
    timeScale.subscribeSizeChange(onSize);
    draw();
    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(onRange);
      timeScale.unsubscribeSizeChange(onSize);
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [chart, draw]);

  // 对象 / 选中态 / 精度变化时重绘
  useEffect(() => {
    draw();
  }, [draw, grids, selectedIds, precision, activeTool]);

  // ── 指针交互 ───────────────────────────────────────────────────────────
  // 挂在容器上并用 **capture** 阶段:只有真正命中锚点(或正在画新对象)时才
  // stopPropagation,其余情况一律放行,图表的平移/缩放/十字光标不受影响。
  useEffect(() => {
    if (!container || !chart || !series) return;

    const local = (e: PointerEvent) => {
      const r = container.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      // 点谁谁拿键盘:多张图并排时,Delete 只作用于最后点过的那张。
      drawingKeyOwner.focus(keyTokenRef.current);
      const { x, y } = local(e);
      const tool = propsRef.current.activeTool;

      if (isDrawingTool(tool)) {
        const a = toAnchor(x, y);
        if (!a) return;
        e.stopPropagation();
        e.preventDefault();
        pendingRef.current = { start: a, current: a, fromX: x, fromY: y, moved: false };
        container.setPointerCapture(e.pointerId);
        return;
      }

      // select 模式:只有抓到锚点才截胡,否则把事件让给图表。
      const hit = sceneRef.current ? hitTestScene(sceneRef.current, x, y) : null;
      if (!hit) {
        if (propsRef.current.selectedIds.size > 0) onSelect(null);
        return;
      }
      // 修饰键语义与树里一致:⌘/Ctrl = 加选减选,Shift = 从上次点中的对象扩选。
      const mods = { shift: e.shiftKey, toggle: e.metaKey || e.ctrlKey };
      if (hit.kind !== "anchor") {
        onSelect(hit.gridId, mods);
        return;
      }
      // 拖拽锚点:先取当前锚点作为起点,免得第一帧就把锚点甩到别处。
      const grid = propsRef.current.grids.find((g) => g.id === hit.gridId);
      // 锁定的对象没有可拖的把手 —— 直接放行给图表(平移不受影响),
      // 且**不能**先 setPointerCapture:捕获了却不释放会把后续点击全吞掉。
      if (!grid || grid.locked) return;
      e.stopPropagation();
      e.preventDefault();
      container.setPointerCapture(e.pointerId);
      dragRef.current = {
        gridId: hit.gridId,
        index: hit.index,
        anchor: hit.index === 0 ? grid.start : grid.end,
      };
      onSelect(hit.gridId, mods);
    };

    // 注意:这个局部处理器不能叫 onMove —— 会遮蔽同名 prop,松手时的
    // 提交就会去调指针处理器而不是 store。
    const onPointerMove = (e: PointerEvent) => {
      const { x, y } = local(e);
      const drag = dragRef.current;
      if (drag) {
        // 拖动期间别让图表同时平移 —— 那会把锚点和画面一起带走。
        e.stopPropagation();
        const a = toAnchor(x, y);
        if (!a) return;
        dragRef.current = { ...drag, anchor: a };
        draw();
        return;
      }

      const pending = pendingRef.current;
      if (!pending) return;
      const a = toAnchor(x, y);
      if (!a) return;
      if (!pending.moved) {
        if (Math.hypot(x - pending.fromX, y - pending.fromY) < 3) return;
        pending.moved = true;
      }
      pendingRef.current = { ...pending, current: a };
      draw();
    };

    const finish = (e: PointerEvent) => {
      const pending = pendingRef.current;
      const drag = dragRef.current;
      pendingRef.current = null;
      dragRef.current = null;
      if (container.hasPointerCapture?.(e.pointerId)) {
        try {
          container.releasePointerCapture(e.pointerId);
        } catch {
          /* ignore */
        }
      }

      if (drag) {
        // 拖动期间只在本层预览,松手才写一次 store(见 withDraggedAnchor 注释)。
        onMove(drag.gridId, drag.index, drag.anchor);
        return;
      }
      if (!pending) return;

      const tool = propsRef.current.activeTool;
      if (!isDrawingTool(tool)) return;
      const spec = TOOLS[tool];

      // 单点工具(水平线 / 竖线):单击即建。拖拽也接受 —— 松手处就是那个点。
      // 它们没有"跨度"可量,所以下面那条"至少隔一根 K 线"的误触判据不适用。
      if (spec.pointCount === 1) {
        onCreate(pending.current, pending.current, propsRef.current.symbol, propsRef.current.timeframe, spec.mode);
        onPlaced();
        draw();
        return;
      }

      if (!pending.moved) return; // 两点工具单击不建对象(避免窗口一大片)

      // 起止点太近(时间上 <= 1 根)也视为误触:那样的网格没有一个可读档位。
      const spanSec = Math.abs(pending.current.time - pending.start.time);
      if (spanSec < 1) return;
      onCreate(pending.start, pending.current, propsRef.current.symbol, propsRef.current.timeframe, spec.mode);
      onPlaced();
      draw();
    };

    const onUp = (e: PointerEvent) => finish(e);
    const onCancel = (e: PointerEvent) => {
      pendingRef.current = null;
      dragRef.current = null;
      if (container.hasPointerCapture?.(e.pointerId)) {
        try {
          container.releasePointerCapture(e.pointerId);
        } catch {
          /* ignore */
        }
      }
      draw();
    };

    container.addEventListener("pointerdown", onDown, { capture: true });
    container.addEventListener("pointermove", onPointerMove);
    container.addEventListener("pointerup", onUp);
    container.addEventListener("pointercancel", onCancel);
    return () => {
      container.removeEventListener("pointerdown", onDown, { capture: true });
      container.removeEventListener("pointermove", onPointerMove);
      container.removeEventListener("pointerup", onUp);
      container.removeEventListener("pointercancel", onCancel);
    };
  }, [container, chart, series, toAnchor, draw, onCreate, onMove, onSelect, onPlaced]);

  // activeTool 变化时改光标 —— 画图态用十字准星,一眼看出"现在点下去会画线"。
  useEffect(() => {
    if (!container) return;
    container.style.cursor = isDrawingTool(activeTool) ? "crosshair" : "";
    return () => {
      container.style.cursor = "";
    };
  }, [container, activeTool]);

  // ── 键盘 ───────────────────────────────────────────────────────────────
  // 挂 window:画完线焦点通常还在 canvas 上,图没被聚焦时也得能按 Delete。
  // 归属判定 + 焦点守卫 + 锁定过滤都在 drawing-keys.ts 里(有单测)。
  useEffect(() => {
    if (!container) return;

    drawingKeyOwner.register(keyTokenRef.current);

    const onKey = (e: KeyboardEvent) => {
      if (!drawingKeyOwner.owns(keyTokenRef.current)) return;
      const ctx = {
        grids: propsRef.current.grids,
        selectedIds: propsRef.current.selectedIds,
        activeTool: propsRef.current.activeTool,
        havePending: pendingRef.current !== null,
      };
      const action = planDrawingKey(e, ctx);
      if (!action) return;

      switch (action.kind) {
        case "delete":
          e.preventDefault();
          onDelete(action.ids);
          return;
        case "cancel-pending":
          e.preventDefault();
          pendingRef.current = null;
          draw();
          return;
        case "cancel-tool":
          e.preventDefault();
          onCancelTool();
          return;
        case "clear-selection":
          e.preventDefault();
          onClearSelection();
          return;
      }
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      drawingKeyOwner.unregister(keyTokenRef.current);
    };
  }, [container, draw, onDelete, onCancelTool, onClearSelection]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: "absolute",
        left: 0,
        top: 0,
        zIndex: 3,
        // 画布只出像素;指针交互由上面那层 capture 监听处理。
        pointerEvents: "none",
      }}
      aria-hidden="true"
    />
  );
});

export default DrawingLayer;
