// 绘图键盘操作 —— Delete / Backspace 删除选中,Escape 逐级撤销。
//
// 语义直接对齐 TradingView(参考 deepentropy/lightweight-charts-drawing 的
// onKey 实现,行为与本仓库的锁/选中模型一致):
//
//   Delete 或 Backspace  删除**整个选中集合**,但跳过 `locked` 的对象。
//                        选中的全是锁定的 → 什么都不做,也不拦默认行为。
//   Escape               TV 的三级级联:先丢"画了一半的对象",再卸下武装的
//                        工具,最后清空选中。每一级只吃一颗 Esc,不一口气退到底。
//
// 为什么做成纯函数:键盘逻辑的坑几乎全在"该不该管这次按键"上(焦点在输入框、
// 没有选中、全是锁定),把它从 React/DOM 里剥出来就能直接写用例覆盖。
// 真正的监听器在 chart-drawing-layer.tsx 里挂。

import type { FibGrid } from "@/lib/chart/fib-object";
import type { DrawingToolKind } from "@/lib/chart/tool-registry";

/** 与 store 的 DrawingTool 同域,但不 import store —— lib/chart 不反向依赖状态层。 */
export type KeyTool = "select" | DrawingToolKind;

export type DrawingKeyAction =
  | { kind: "delete"; ids: string[] }
  | { kind: "cancel-pending" }
  | { kind: "cancel-tool" }
  | { kind: "clear-selection" };

export interface DrawingKeyContext {
  grids: readonly FibGrid[];
  selectedIds: ReadonlySet<string>;
  activeTool: KeyTool;
  /** 是否有一个"拖了但还没落笔"的对象(预览网格)。 */
  havePending: boolean;
}

/**
 * 焦点在可编辑控件里时,按键属于那个控件(退格删字、方向键移光标),
 * 不能被图表抢走。SELECT 也算:下拉框里按 Delete 没有删对象的语义。
 */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!target || typeof (target as HTMLElement).tagName !== "string") return false;
  const el = target as HTMLElement;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return el.isContentEditable === true;
}

/**
 * 这次按键对绘图意味着什么。`null` = 不归绘图层管,调用方必须放行。
 *
 * 只认无修饰键的 Delete/Backspace/Escape:带 ⌘/Ctrl 的组合留给系统(剪切等),
 * 带 Alt 的留给别处。
 */
export function planDrawingKey(
  e: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "target">,
  ctx: DrawingKeyContext,
): DrawingKeyAction | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  if (isEditableTarget(e.target)) return null;

  if (e.key === "Delete" || e.key === "Backspace") {
    if (ctx.selectedIds.size === 0) return null;
    // 锁定的对象 = "能看到、能选中,但不许拖、不许删"。全锁 → 这键不归我们。
    const deletable = ctx.grids.filter((g) => ctx.selectedIds.has(g.id) && !g.locked).map((g) => g.id);
    return deletable.length ? { kind: "delete", ids: deletable } : null;
  }

  if (e.key === "Escape") {
    if (ctx.havePending) return { kind: "cancel-pending" };
    if (ctx.activeTool !== "select") return { kind: "cancel-tool" };
    if (ctx.selectedIds.size > 0) return { kind: "clear-selection" };
    return null;
  }

  return null;
}

/**
 * 同一时刻只让"最后碰过的那张图"响应键盘。
 *
 * 页面上可能同时挂两张 KfChart(比如研究页的并排对照),选中集合却是全局的 ——
 * 两个监听器一起跑会把同一批 id 删两次(第二次空转,但会多写一次 localStorage)。
 *
 * 归属规则:
 *   - 点过图(`focus`)→ 被点的那张拥有;
 *   - 从没点过(刚刷新、光标还在别处)→ **最后挂载**的那张拥有。这条不能省:
 *     否则单图页面在用户点一下画布之前按 Delete 会完全没反应。
 */
export interface KeyOwner {
  token: object;
  /** 挂载时登记。 */
  register: (token: object) => void;
  /** 卸载时注销;正在聚焦的是它就让出。 */
  unregister: (token: object) => void;
  /** pointerdown 时调用:抢到键盘。 */
  focus: (token: object) => void;
  owns: (token: object) => boolean;
}

export function createKeyOwner(): KeyOwner {
  let focused: object | null = null;
  /** 挂载顺序,末尾 = 最近挂载。 */
  const mounted: object[] = [];
  const drop = (token: object) => {
    const i = mounted.indexOf(token);
    if (i >= 0) mounted.splice(i, 1);
  };
  return {
    token: {},
    register: (token) => {
      drop(token);
      mounted.push(token);
    },
    unregister: (token) => {
      drop(token);
      if (focused === token) focused = null;
    },
    focus: (token) => {
      focused = token;
    },
    owns: (token) => {
      if (focused) return focused === token;
      return mounted.length > 0 && mounted[mounted.length - 1] === token;
    },
  };
}

/**
 * 页面级共享的一份归属令牌。多张图各自 import 到的是同一个对象,
 * 所以"最后点过谁"是全局唯一的事实。
 */
export const drawingKeyOwner: KeyOwner = createKeyOwner();
