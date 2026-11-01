"use client";

import { create } from "zustand";

import { type FibGrid, type FibGridMode, type LevelOverride } from "@/lib/chart/fib-object";
import { EXTENSION_LEVELS } from "@/lib/chart/fib-levels";
import { type DrawingToolKind } from "@/lib/chart/tool-registry";
import { parseDrawingsJson, serializeDrawings } from "@/lib/chart/drawing-persist";
import {
  DRAWING_PREFS_KEY,
  type DrawingPrefs,
  type MagnetMode,
  DEFAULT_DRAWING_PREFS,
  parseDrawingPrefsJson,
  serializeDrawingPrefs,
} from "@/lib/chart/drawing-prefs";
import {
  CHART_APPEARANCE_KEY,
  type ChartAppearance,
  DEFAULT_CHART_APPEARANCE,
  cloneAppearance,
  parseChartAppearanceJson,
  serializeChartAppearance,
} from "@/lib/chart/chart-appearance";
import {
  EMPTY_TREE,
  addGrid,
  addLevel,
  addGroup,
  clearSelection,
  createGrid,
  duplicateGrid,
  moveAnchor,
  removeGrid,
  removeGrids,
  removeGroup,
  removeLevel,
  renameGrid,
  resetLevels,
  selectAllInRange,
  selectGrid,
  selectRange,
  setGridGroup,
  setLevelStyle,
  setLockedOf,
  setVisibilityOf,
  toggleAutoExtend,
  toggleExtendSide,
  toggleGroupCollapsed,
  toggleLevel,
  toggleLock,
  toggleSelection,
  toggleVisibility,
  updateGrid,
  type DrawingGroup,
  type DrawingTree,
} from "@/lib/chart/drawing-tree";

// 手绘对象树 —— zustand + localStorage。
//
// 为什么挂在共享 store 而不是 KfChart 内部 state:
// kf 页最多同时开 9 个图,同标的多格布局下"这条线应该出现在每一格里"。
// 每个图表自己存一份会立刻分叉。
//
// 为什么先落 localStorage 而不是后端:
// 后端今天没有任何 annotation 表(查过 migrations_pkg),新建表+端点要动 4 个
// 汇聚点。先用 localStorage 把交互跑通,后端落库作为下一步,迁移时
// drawing-persist.ts 的 schema 版本号就是切点。

const STORAGE_KEY = "selenespace-fib-drawings";

/**
 * 当前激活的绘图工具。取值域由 tool-registry 的 TOOLS 决定,
 * 加工具时只改那张表,不改这里。
 *
 * "select" 是默认态:可以点选、拖锚点,但不会新建对象。
 * 其余值是"下一次点击开始画一个该类型的对象"。
 */
export type DrawingTool = "select" | DrawingToolKind;

/** 逐档样式补丁:字段省略 = 不动;空串 / null = 清掉该项覆盖。 */
export type LevelStylePatch = Omit<LevelOverride, "width"> & { width?: number | null };

interface DrawingsState extends DrawingTree {
  /** 是否已从 localStorage 恢复 —— 避免 SSR/首帧用空树覆盖已存数据。 */
  hydrated: boolean;
  activeTool: DrawingTool;
  setActiveTool: (tool: DrawingTool) => void;
  /**
   * 对象树侧栏是否展开。默认收起(只留 24px 竖条),不落盘:
   * 与 `activeTool` 同理,重开应用应回到一个不占画布的图表。
   */
  dockOpen: boolean;
  setDockOpen: (open: boolean) => void;
  /**
   * 绘图全局偏好(连续绘制 / 磁吸)。**落盘**,与 `activeTool` / `dockOpen` 相反 ——
   * 原因见 drawing-prefs.ts 文件头。偏好从 localStorage 恢复的时机与绘图对象
   * 相同(hydrate),但走独立的 key,坏掉一边不会拖垮另一边。
   */
  prefs: DrawingPrefs;
  setPrefs: (patch: Partial<DrawingPrefs>) => void;
  setKeepDrawing: (on: boolean) => void;
  setMagnet: (mode: MagnetMode) => void;
  /**
   * 图表外观(背景 / 网格 / K 线配色)。同样**落盘**,但走第三个 key ——
   * 它跟"怎么画线"是两件事,用户分开调;坏一份不影响另外两份。
   * 见 chart-appearance.ts 文件头。
   */
  appearance: ChartAppearance;
  /** 浅合并顶层字段;`background` / `candles` 是整体替换,要改就传完整对象。 */
  setAppearance: (patch: Partial<ChartAppearance>) => void;
  hydrate: () => void;
  createFromDrag: (input: {
    start: { time: number; price: number };
    end: { time: number; price: number };
    symbol: string;
    timeframe: string;
    mode?: FibGridMode;
  }) => string;
  remove: (id: string) => void;
  /** 批量删除。树里的"删除选中"走这里,一条路径统一落盘。 */
  removeMany: (ids: readonly string[]) => void;
  /** 单选(替换整个选中集合)。 */
  select: (id: string | null) => void;
  /** ⌘/Ctrl+点击。 */
  toggleSelect: (id: string) => void;
  /** Shift+点击。`order` = 树当前可见顺序。 */
  selectRangeTo: (order: readonly string[], anchorId: string, targetId: string) => void;
  /** 全选当前范围内。 */
  selectAllIn: (order: readonly string[]) => void;
  clearSelection: () => void;
  /** 批量显隐(绝对值,不是 toggle)。 */
  setVisibleOf: (ids: readonly string[], visible: boolean) => void;
  /** 批量锁定。 */
  setLockedOf: (ids: readonly string[], locked: boolean) => void;
  move: (id: string, index: 0 | 1, anchor: { time: number; price: number }) => void;
  patch: (id: string, patch: Partial<Omit<FibGrid, "id">>) => void;
  toggleVisible: (id: string) => void;
  toggleLockOf: (id: string) => void;
  toggleExtend: (id: string) => void;
  /** 档位线横向延伸:left/right 各自独立,默认都关(线段)。 */
  toggleExtendSideOf: (id: string, side: "left" | "right") => void;
  toggleLevelOf: (id: string, level: number) => void;
  /** 加一条任意比例的自定义档位(对象树输入框)。已存在则为空操作。 */
  addLevelOf: (id: string, level: number) => void;
  /** 删一条档位,连同它的样式覆盖。 */
  removeLevelOf: (id: string, level: number) => void;
  resetLevelsOf: (id: string) => void;
  /** 改某一档的颜色/线宽/虚线/别名;字段传 undefined 表示恢复默认。 */
  setLevelStyleOf: (id: string, coeff: number, patch: LevelStylePatch) => void;
  rename: (id: string, label: string) => void;
  duplicate: (id: string, offset?: { time: number; price: number }) => void;
  addGroupNamed: (name: string) => void;
  removeGroupById: (id: string) => void;
  assignGroup: (gridId: string, groupId: string | null) => void;
  collapseGroup: (id: string) => void;
  /** 测试专用:清空并重置。 */
  resetForTest: () => void;
}

function persist(tree: DrawingTree): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(serializeDrawings(tree.grids)));
  } catch {
    // 配额满 / 隐私模式:内存里仍然有效,只是不落盘
  }
}

/** 偏好落盘。与绘图对象分开 key,单边写坏不互相拖累。 */
function persistPrefs(prefs: DrawingPrefs): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(DRAWING_PREFS_KEY, serializeDrawingPrefs(prefs));
  } catch {
    // 同上:存不下就只在本次会话内生效
  }
}

/** 外观落盘。第三个 key —— 与对象树、绘图偏好三者各写各的。 */
function persistAppearance(appearance: ChartAppearance): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(CHART_APPEARANCE_KEY, serializeChartAppearance(appearance));
  } catch {
    // 同上
  }
}

/** 落盘 + 返回新树。所有变更操作都经这里,避免有路径忘了存。 */
function commit(set: (fn: (s: DrawingsState) => Partial<DrawingsState>) => void, reduce: (t: DrawingTree) => DrawingTree) {
  set((state) => {
    const next = reduce(state);
    if (next !== state) persist(next);
    return { grids: next.grids, groups: next.groups, selectedIds: next.selectedIds };
  });
}

export const useDrawingsStore = create<DrawingsState>((set) => ({
  ...EMPTY_TREE,
  hydrated: false,
  activeTool: "select",
  dockOpen: false,
  prefs: { ...DEFAULT_DRAWING_PREFS },
  appearance: cloneAppearance(DEFAULT_CHART_APPEARANCE),

  // 工具是**每张图各自的交互态**,不进 localStorage:重开应用应当回到"选择"。
  setActiveTool: (tool) => set({ activeTool: tool }),
  setDockOpen: (open) => set({ dockOpen: open }),

  // 偏好相反:必须记住。改一项就把整包写下(只有两个字段,不值得做增量)。
  setPrefs: (patch) =>
    set((state) => {
      const prefs: DrawingPrefs = { ...state.prefs, ...patch };
      persistPrefs(prefs);
      return { prefs };
    }),
  setKeepDrawing: (on) => {
    set((state) => {
      const prefs: DrawingPrefs = { ...state.prefs, keepDrawing: on };
      persistPrefs(prefs);
      return { prefs };
    });
  },
  setMagnet: (mode) => {
    set((state) => {
      const prefs: DrawingPrefs = { ...state.prefs, magnet: mode };
      persistPrefs(prefs);
      return { prefs };
    });
  },

  // 外观:三层字段(background / candles)是整对象,浅合并就够 —— 调用方
  // 拿 cloneAppearance 铺开改一个色再整块传进来,避免两份状态共享引用。
  setAppearance: (patch) =>
    set((state) => {
      const appearance: ChartAppearance = { ...state.appearance, ...patch };
      persistAppearance(appearance);
      return { appearance };
    }),

  hydrate: () => {
    if (typeof window === "undefined") return;
    let grids: FibGrid[] = [];
    let prefs: DrawingPrefs = { ...DEFAULT_DRAWING_PREFS };
    try {
      grids = parseDrawingsJson(window.localStorage.getItem(STORAGE_KEY));
    } catch {
      grids = [];
    }
    try {
      prefs = parseDrawingPrefsJson(window.localStorage.getItem(DRAWING_PREFS_KEY));
    } catch {
      prefs = { ...DEFAULT_DRAWING_PREFS };
    }
    let appearance = cloneAppearance(DEFAULT_CHART_APPEARANCE);
    try {
      appearance = parseChartAppearanceJson(window.localStorage.getItem(CHART_APPEARANCE_KEY));
    } catch {
      appearance = cloneAppearance(DEFAULT_CHART_APPEARANCE);
    }
    // 偏好与对象树各自判断"是否已被别处 hydrate 过":一个标的多格同时挂载时,
    // 第二个图表不该把偏好重置回它自己读到的那份旧值。
    set((state) => ({
      ...(state.hydrated && state.grids.length > 0 ? {} : { grids }),
      hydrated: true,
      prefs: state.hydrated ? state.prefs : prefs,
      appearance: state.hydrated ? state.appearance : appearance,
    }));
  },

  createFromDrag: (input) => {
    const mode = input.mode ?? "price";
    // 单点工具(水平线 / 竖线)只认落笔那一处:把两个锚点压成同一点,
    // 否则拖了一下就会留下一条"从拖到松"的腿,而几何根本用不到它。
    const single = mode === "horizontal" || mode === "vertical";
    const grid = createGrid({
      ...input,
      mode,
      start: single ? { ...input.end } : input.start,
      end: { ...input.end },
    });
    commit(set, (t) => addGrid(t, grid));
    return grid.id;
  },

  remove: (id) => commit(set, (t) => removeGrid(t, id)),
  removeMany: (ids) => commit(set, (t) => removeGrids(t, ids)),
  select: (id) => commit(set, (t) => selectGrid(t, id)),
  toggleSelect: (id) => commit(set, (t) => toggleSelection(t, id)),
  selectRangeTo: (order, anchorId, targetId) => commit(set, (t) => selectRange(t, order, anchorId, targetId)),
  selectAllIn: (order) => commit(set, (t) => selectAllInRange(t, order)),
  clearSelection: () => commit(set, (t) => clearSelection(t)),
  setVisibleOf: (ids, visible) => commit(set, (t) => setVisibilityOf(t, ids, visible)),
  setLockedOf: (ids, locked) => commit(set, (t) => setLockedOf(t, ids, locked)),
  move: (id, index, anchor) => commit(set, (t) => moveAnchor(t, id, index, anchor)),
  patch: (id, p) => commit(set, (t) => updateGrid(t, id, p)),
  toggleVisible: (id) => commit(set, (t) => toggleVisibility(t, id)),
  toggleLockOf: (id) => commit(set, (t) => toggleLock(t, id)),
  toggleExtend: (id) => commit(set, (t) => toggleAutoExtend(t, id, EXTENSION_LEVELS)),
  toggleExtendSideOf: (id, side) => commit(set, (t) => toggleExtendSide(t, id, side)),
  toggleLevelOf: (id, level) => commit(set, (t) => toggleLevel(t, id, level)),
  addLevelOf: (id, level) => commit(set, (t) => addLevel(t, id, level)),
  removeLevelOf: (id, level) => commit(set, (t) => removeLevel(t, id, level)),
  resetLevelsOf: (id) => commit(set, (t) => resetLevels(t, id)),
  setLevelStyleOf: (id, coeff, patch) => commit(set, (t) => setLevelStyle(t, id, coeff, patch)),
  rename: (id, label) => commit(set, (t) => renameGrid(t, id, label)),
  duplicate: (id, offset) => commit(set, (t) => duplicateGrid(t, id, offset)),
  addGroupNamed: (name) => commit(set, (t) => addGroup(t, name)),
  removeGroupById: (id) => commit(set, (t) => removeGroup(t, id)),
  assignGroup: (gridId, groupId) => commit(set, (t) => setGridGroup(t, gridId, groupId)),
  collapseGroup: (id) => commit(set, (t) => toggleGroupCollapsed(t, id)),

  resetForTest: () => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.removeItem(STORAGE_KEY);
        window.localStorage.removeItem(DRAWING_PREFS_KEY);
        window.localStorage.removeItem(CHART_APPEARANCE_KEY);
      } catch {
        // ignore
      }
    }
    set({
      ...EMPTY_TREE,
      hydrated: false,
      grids: [],
      groups: [],
      selectedIds: new Set<string>(),
      dockOpen: false,
      prefs: { ...DEFAULT_DRAWING_PREFS },
      // 三份落盘状态的第三份。漏掉它会跨用例泄漏"上次改过的背景色",
      // 表现为后面用例一开局「恢复默认」就是可点的。
      appearance: cloneAppearance(DEFAULT_CHART_APPEARANCE),
    });
  },
}));

/** 当前图上应显示的对象。同一标的多格共享同一批。 */
export function selectVisibleGrids(state: Pick<DrawingsState, "grids">, symbol: string): FibGrid[] {
  return state.grids.filter((g) => g.visible && g.symbol === symbol);
}

/** 对象树按分组组织后的行(供 UI 渲染)。组内对象按加入顺序。 */
export interface TreeRow {
  group: DrawingGroup | null;
  grids: FibGrid[];
}

export function groupRows(grids: readonly FibGrid[], groups: readonly DrawingGroup[]): TreeRow[] {
  const rows: TreeRow[] = [];
  const ungrouped = grids.filter((g) => g.groupId === null);
  if (ungrouped.length > 0) rows.push({ group: null, grids: ungrouped });
  for (const group of groups) {
    rows.push({ group, grids: grids.filter((g) => g.groupId === group.id) });
  }
  // 指向已删除分组的孤儿对象归到顶层,不让它从树里消失
  const known = new Set(groups.map((g) => g.id));
  const orphans = grids.filter((g) => g.groupId !== null && !known.has(g.groupId));
  if (orphans.length > 0) {
    const top = rows.find((r) => r.group === null);
    if (top) top.grids = [...top.grids, ...orphans];
    else rows.unshift({ group: null, grids: orphans });
  }
  return rows;
}
