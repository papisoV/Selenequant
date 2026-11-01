// 绘图对象树的操作 —— 纯函数 + 不可变更新(零 React / 零浏览器 API)。
//
// 所有操作都返回**新数组**,不改入参。理由不只是"好看":画布重绘靠引用变化
// 判断要不要重画,原地 mutate 会让"拖了锚点但画面没动"——这类 bug 极难查。
//
// 选中是**集合**而不是单值:对象树的批量删除/批量显隐/批量锁定都以它为前提。
// 集合恒定不可变 —— 调用方拿到的是 `readonly Set`,想改只能走本文件的函数。
// 语义规定:
//   · 单点选中替换整个集合(点 A 再点 B 只剩 B)—— 单点不是"加选";
//   · 加减选走 toggleSelection / selectRange,由 UI 的修饰键决定用哪个;
//   · 不在树里的 id 一律不入集(避免删除后残留幽灵选中)。

import { type FibGrid, type FibGridMode, hasLevels, type LevelOverride, type LevelStyleEntry, normalizeLevels } from "@/lib/chart/fib-object";
import { defaultVisibleLevels } from "@/lib/chart/fib-levels";
import { defaultTimeLevels } from "@/lib/chart/fib-time-levels";
import { clampLineWidth } from "@/lib/chart/fib-style";

/** 对象树的一个分组节点。分组只做组织,不参与几何。 */
export interface DrawingGroup {
  id: string;
  name: string;
  collapsed: boolean;
}

export interface DrawingTree {
  grids: readonly FibGrid[];
  groups: readonly DrawingGroup[];
  /**
   * 当前选中的对象 id。空集合 = 无选中。
   *
   * 用 `ReadonlySet` 而不是数组:选中的本质是"这个 id 在不在里面",
   * 数组的 includes 在 9 格 × 多对象时是 O(n),而且允许重复值这种非法状态。
   */
  selectedIds: ReadonlySet<string>;
}

export const EMPTY_TREE: DrawingTree = { grids: [], groups: [], selectedIds: new Set<string>() };

let idCounter = 0;

/** 生成 id。不用 Math.random:测试要能预测,并且避免同毫秒撞号。 */
export function nextGridId(prefix = "fib"): string {
  idCounter += 1;
  return `${prefix}-${idCounter}`;
}

/** 测试专用:重置计数器,让 id 断言可复现。 */
export function resetIdCounterForTest(): void {
  idCounter = 0;
}

export interface NewGridInput {
  start: { time: number; price: number };
  end: { time: number; price: number };
  symbol: string;
  timeframe: string;
  mode?: FibGridMode;
  levels?: readonly number[];
  label?: string;
  groupId?: string | null;
  id?: string;
}

/**
 * 某种模式的默认档位族。价格网格用交易者口径,时间网格用斐波那契倍数,
 * 基础几何(趋势线/水平线/竖线/矩形)没有档位概念 —— 返回空数组。
 */
export function defaultLevelsForMode(mode: FibGridMode): number[] {
  if (hasLevels(mode)) return mode === "time" ? defaultTimeLevels() : defaultVisibleLevels();
  return [];
}

/** 新建一条网格。默认档位按模式取(不含腿两端)。 */
export function createGrid(input: NewGridInput): FibGrid {
  const mode = input.mode ?? "price";
  const levelSource = input.levels ?? defaultLevelsForMode(mode);
  return {
    id: input.id ?? nextGridId(),
    groupId: input.groupId ?? null,
    start: { ...input.start },
    end: { ...input.end },
    mode,
    levels: normalizeLevels(levelSource),
    autoExtend: false,
    extendLeft: false,
    extendRight: false,
    visible: true,
    locked: false,
    timeframe: input.timeframe,
    symbol: input.symbol,
    label: input.label ?? "",
  };
}

/** 加入树并选中它 —— 画完一条立刻选中,用户能马上拖。 */
export function addGrid(tree: DrawingTree, grid: FibGrid): DrawingTree {
  if (tree.grids.some((g) => g.id === grid.id)) return tree;
  return { ...tree, grids: [...tree.grids, grid], selectedIds: new Set([grid.id]) };
}

/** 删除一组。被删的若是选中项,选中态一并剔除(保留其余选中)。 */
export function removeGrids(tree: DrawingTree, gridIds: readonly string[]): DrawingTree {
  const doomed = new Set(gridIds);
  if (doomed.size === 0) return tree;
  const grids = tree.grids.filter((g) => !doomed.has(g.id));
  if (grids.length === tree.grids.length) return tree;
  const selectedIds = new Set([...tree.selectedIds].filter((id) => !doomed.has(id)));
  return { ...tree, grids, selectedIds };
}

/** 删除单个。保留成对 API:调用点读起来比 `removeGrids(t, [id])` 更直白。 */
export function removeGrid(tree: DrawingTree, gridId: string): DrawingTree {
  return removeGrids(tree, [gridId]);
}

/** 按 id 局部更新。找不到返回原树(引用不变,调用方据此跳过重绘)。 */
export function updateGrid(
  tree: DrawingTree,
  gridId: string,
  patch: Partial<Omit<FibGrid, "id">>,
): DrawingTree {
  let changed = false;
  const grids = tree.grids.map((g) => {
    if (g.id !== gridId) return g;
    changed = true;
    const next: FibGrid = { ...g, ...patch };
    // levels 一定要重新归一:UI 勾选档位时可能传进乱序或重复
    if (patch.levels) next.levels = normalizeLevels(patch.levels);
    return next;
  });
  return changed ? { ...tree, grids } : tree;
}

/** 移动一个锚点。拖拽是高频操作,只换被拖的那一个对象的引用。 */
export function moveAnchor(
  tree: DrawingTree,
  gridId: string,
  index: 0 | 1,
  anchor: { time: number; price: number },
): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid) return tree;
  if (grid.locked) return tree;
  return updateGrid(tree, gridId, index === 0 ? { start: { ...anchor } } : { end: { ...anchor } });
}

/**
 * 单选 —— 替换整个选中集合。
 *
 * `null` 清空;不存在的 id 等价清空(与旧单值版同语义,免得画布点空白
 * 传进来一个已删的 id 时把"选中不存在的对象"这种非法态存下来)。
 */
export function selectGrid(tree: DrawingTree, gridId: string | null): DrawingTree {
  const next: ReadonlySet<string> =
    gridId !== null && tree.grids.some((g) => g.id === gridId) ? new Set([gridId]) : new Set<string>();
  if (sameSelection(tree.selectedIds, next)) return tree;
  return { ...tree, selectedIds: next };
}

/**
 * 加选 / 减选一个(⌘/Ctrl+点击)。
 *
 * 不在树里的 id 直接忽略:选中一个不存在的对象没有意义,而且会让
 * 批量操作(删除/隐藏)去操作空气,数量显示也对不上。
 */
export function toggleSelection(tree: DrawingTree, gridId: string): DrawingTree {
  if (!tree.grids.some((g) => g.id === gridId)) return tree;
  const next = new Set(tree.selectedIds);
  if (next.has(gridId)) next.delete(gridId);
  else next.add(gridId);
  return { ...tree, selectedIds: next };
}

/**
 * 区间选择(Shift+点击):从锚点一路选到目标。
 *
 * `order` 是**树的可见顺序**(由调用方给出,通常是当前范围内扁平化后的行序),
 * 不是存储顺序 —— 用户在树里看到 A…B 相邻,Shift 选就应该选这一段。
 * 锚点不在当前顺序里(切了范围/删掉了)时退化成单选,不让操作无声失败。
 */
export function selectRange(tree: DrawingTree, order: readonly string[], anchorId: string, targetId: string): DrawingTree {
  const from = order.indexOf(anchorId);
  const to = order.indexOf(targetId);
  if (!tree.grids.some((g) => g.id === targetId)) return tree;
  if (from < 0 || to < 0) return selectGrid(tree, targetId);
  const [lo, hi] = from <= to ? [from, to] : [to, from];
  return { ...tree, selectedIds: new Set(order.slice(lo, hi + 1)) };
}

/** 全选当前范围内的对象。范围外的选中保留 —— 用户在「全部标的」选过的对象不该因切范围而不算数。 */
export function selectAllInRange(tree: DrawingTree, order: readonly string[]): DrawingTree {
  if (order.length === 0) return tree;
  const next = new Set(tree.selectedIds);
  for (const id of order) {
    if (tree.grids.some((g) => g.id === id)) next.add(id);
  }
  return sameSelection(tree.selectedIds, next) ? tree : { ...tree, selectedIds: next };
}

export function clearSelection(tree: DrawingTree): DrawingTree {
  return tree.selectedIds.size === 0 ? tree : { ...tree, selectedIds: new Set<string>() };
}

function sameSelection(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  if (a.size !== b.size) return false;
  for (const id of a) if (!b.has(id)) return false;
  return true;
}

// ── 批量操作 ────────────────────────────────────────────────────────────
// 批量操作**只接受绘图 id**:指标是页面级布尔、不在这个 store 里,
// 由调用方(tree UI)把指标从选中集合里挑出去再调这些函数。

function eachGrid(
  tree: DrawingTree,
  gridIds: readonly string[],
  fn: (g: FibGrid) => Partial<Omit<FibGrid, "id">> | null,
): DrawingTree {
  const wanted = new Set(gridIds);
  if (wanted.size === 0) return tree;
  let changed = false;
  const grids = tree.grids.map((g) => {
    if (!wanted.has(g.id)) return g;
    const patch = fn(g);
    if (patch === null) return g;
    changed = true;
    return { ...g, ...patch };
  });
  return changed ? { ...tree, grids } : tree;
}

/** 批量显隐。显/隐的**绝对值**由调用方定,不做 toggle —— 混选状态下 toggle 会把一半对象改反。 */
export function setVisibilityOf(tree: DrawingTree, gridIds: readonly string[], visible: boolean): DrawingTree {
  return eachGrid(tree, gridIds, (g) => (g.visible === visible ? null : { visible }));
}

/** 批量锁定。锁定后把被锁对象移出选中集:锁着的对象没有可拖的东西,留着选中只会困惑。 */
export function setLockedOf(tree: DrawingTree, gridIds: readonly string[], locked: boolean): DrawingTree {
  const next = eachGrid(tree, gridIds, (g) => (g.locked === locked ? null : { locked }));
  if (!locked || next === tree) return next;
  const nowLocked = new Set(gridIds.filter((id) => next.grids.some((g) => g.id === id && g.locked)));
  const selectedIds = new Set([...next.selectedIds].filter((id) => !nowLocked.has(id)));
  return selectedIds.size === next.selectedIds.size ? next : { ...next, selectedIds };
}

/** 切换可见性。锁定对象也允许隐藏 —— 隐藏是视图操作,不是几何操作。 */
export function toggleVisibility(tree: DrawingTree, gridId: string): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid) return tree;
  return updateGrid(tree, gridId, { visible: !grid.visible });
}

/** 切换锁定。锁定时顺手取消选中——锁着的对象没有可拖的东西。 */
export function toggleLock(tree: DrawingTree, gridId: string): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid) return tree;
  return setLockedOf(tree, [gridId], !grid.locked);
}

/** 切换自动延伸(把延伸位加进来 / 去掉)。注意这是**价格档位**的扩展,不是横向长度。 */
export function toggleAutoExtend(tree: DrawingTree, gridId: string, extensionLevels: readonly number[]): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid) return tree;
  const on = !grid.autoExtend;
  const base = grid.levels.filter((lv) => !extensionLevels.includes(lv));
  const levels = on ? [...base, ...extensionLevels] : base;
  return updateGrid(tree, gridId, { autoExtend: on, levels });
}

/**
 * 切换档位线向某一侧的横向延伸(TradingView 的 `extendLeft` / `extendRight`)。
 * 与 `toggleAutoExtend` 无关:那个改价格档位,这个改线的横向长度。
 */
export function toggleExtendSide(tree: DrawingTree, gridId: string, side: "left" | "right"): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid) return tree;
  return side === "left"
    ? updateGrid(tree, gridId, { extendLeft: !grid.extendLeft })
    : updateGrid(tree, gridId, { extendRight: !grid.extendRight });
}

/** 勾选/取消单个档位。 */
export function toggleLevel(tree: DrawingTree, gridId: string, level: number): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid) return tree;
  const has = grid.levels.includes(level);
  const levels = has ? grid.levels.filter((lv) => lv !== level) : [...grid.levels, level];
  return updateGrid(tree, gridId, { levels });
}

/**
 * 加一条自定义档位(对象树里输入任意比例)。
 *
 * 与 `toggleLevel` 的区别:那个只在**已知档位表**里加减,这个接受任意有限数。
 * 非有限数(空输入被 Number("") 吃成 0 不在此列)一律拒绝 —— 不能往 levels 里
 * 塞 NaN,那会在渲染时变成一条走向无穷的线。
 *
 * 已存在则原样返回(不动引用),调用方据此判断"这条重复了",不会误判为成功。
 */
export function addLevel(tree: DrawingTree, gridId: string, level: number): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid || !Number.isFinite(level)) return tree;
  if (grid.levels.includes(level)) return tree;
  return updateGrid(tree, gridId, { levels: [...grid.levels, level] });
}

/**
 * 删一条档位。**同时清掉它的样式覆盖** —— 留一个指向不存在档位的 override,
 * 用户再加回同一比例时会"莫名其妙带着上次的颜色",而这无处可查。
 */
export function removeLevel(tree: DrawingTree, gridId: string, level: number): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid || !grid.levels.includes(level)) return tree;
  const next = updateGrid(tree, gridId, { levels: grid.levels.filter((lv) => lv !== level) });
  const target = next.grids.find((g) => g.id === gridId);
  if (!target?.levelStyles?.some((s) => s.coeff === level)) return next;
  return updateGrid(next, gridId, {
    levelStyles: target.levelStyles.filter((s) => s.coeff !== level),
  });
}

/** 重命名。裁剪空白;空名允许(表示未命名)。 */
export function renameGrid(tree: DrawingTree, gridId: string, label: string): DrawingTree {
  return updateGrid(tree, gridId, { label: label.trim() });
}

/** 复制一条(offset 用于错开位置,否则两条完全重合根本分不开)。 */
export function duplicateGrid(tree: DrawingTree, gridId: string, offset = { time: 0, price: 0 }): DrawingTree {
  const src = tree.grids.find((g) => g.id === gridId);
  if (!src) return tree;
  const copy: FibGrid = {
    ...src,
    id: nextGridId(),
    start: { time: src.start.time + offset.time, price: src.start.price + offset.price },
    end: { time: src.end.time + offset.time, price: src.end.price + offset.price },
    levels: [...src.levels],
  };
  return addGrid(tree, copy);
}

/**
 * 当前图上可见的网格(按标的分流)。
 *
 * 不做周期过滤:锚点是时间 + 价格,与周期无关 —— 这是"高周期画的线在低周期
 * 重锚"的实现前提。标的不同则不显示,因为价位单位不同,画上去纯属误导。
 */
export function visibleGrids(tree: DrawingTree, symbol: string): FibGrid[] {
  return tree.grids.filter((g) => g.visible && g.symbol === symbol);
}

/**
 * 恢复默认档位(去掉延伸位)。
 *
 * 同时清掉逐档样式:样式是按比例挂的,档位都没了还留着覆盖,
 * 下次勾回同一档时会莫名其妙带上旧颜色 —— 用户会以为"恢复默认没生效"。
 */
export function resetLevels(tree: DrawingTree, gridId: string): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid) return tree;
  return updateGrid(tree, gridId, {
    levels: defaultLevelsForMode(grid.mode),
    levelStyles: [],
  });
}

/**
 * 设置某一档的样式覆盖。patch 里的 `undefined` / 空串表示**清掉该项覆盖**
 * (回到默认桶),而不是写入空值 —— 否则用户无法把颜色改回默认。
 */
export function setLevelStyle(
  tree: DrawingTree,
  gridId: string,
  coeff: number,
  patch: Omit<LevelOverride, "width"> & { width?: number | null },
): DrawingTree {
  const grid = tree.grids.find((g) => g.id === gridId);
  if (!grid || !Number.isFinite(coeff)) return tree;

  const current = grid.levelStyles?.find((s) => s.coeff === coeff) ?? { coeff };
  const merged: LevelStyleEntry = { ...current };

  if (patch.color !== undefined) {
    if (patch.color) merged.color = patch.color;
    else delete merged.color;
  }
  if (patch.dashed !== undefined) merged.dashed = patch.dashed;
  if (patch.text !== undefined) {
    if (patch.text.trim()) merged.text = patch.text;
    else delete merged.text;
  }
  if (patch.width !== undefined) {
    if (patch.width === null) delete merged.width;
    else merged.width = clampLineWidth(patch.width);
  }

  // 覆盖字段清空后整条也删掉:留一个只有 coeff 的空条目会让持久化文件虚胖,
  // 也让"这条档位被改过"的判断变模糊。
  const isEmpty = merged.color === undefined && merged.dashed === undefined && merged.text === undefined && merged.width === undefined;
  const rest = (grid.levelStyles ?? []).filter((s) => s.coeff !== coeff);
  const levelStyles = isEmpty ? rest : [...rest, merged].sort((a, b) => a.coeff - b.coeff);

  return updateGrid(tree, gridId, { levelStyles });
}

// ── 分组 ────────────────────────────────────────────────────────────────

export function addGroup(tree: DrawingTree, name: string): DrawingTree {
  const group: DrawingGroup = { id: nextGridId("grp"), name: name.trim() || "未命名分组", collapsed: false };
  return { ...tree, groups: [...tree.groups, group] };
}

/** 删除分组:组内对象**不删**,只是回到顶层。删分组顺手删掉用户画的线是灾难。 */
export function removeGroup(tree: DrawingTree, groupId: string): DrawingTree {
  if (!tree.groups.some((g) => g.id === groupId)) return tree;
  return {
    ...tree,
    groups: tree.groups.filter((g) => g.id !== groupId),
    grids: tree.grids.map((g) => (g.groupId === groupId ? { ...g, groupId: null } : g)),
  };
}

export function setGridGroup(tree: DrawingTree, gridId: string, groupId: string | null): DrawingTree {
  if (groupId !== null && !tree.groups.some((g) => g.id === groupId)) return tree;
  return updateGrid(tree, gridId, { groupId });
}

export function toggleGroupCollapsed(tree: DrawingTree, groupId: string): DrawingTree {
  let changed = false;
  const groups = tree.groups.map((g) => {
    if (g.id !== groupId) return g;
    changed = true;
    return { ...g, collapsed: !g.collapsed };
  });
  return changed ? { ...tree, groups } : tree;
}
