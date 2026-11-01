"use client"

// 对象树**常驻侧栏** —— 停靠在图表右侧,不再是被点开的弹层。
//
// 为什么改常驻:对象树原来是个 `position: fixed` 弹层,点外面就关。用户每改一次
// 分组/档位/可见性就得重开一次,而"看着树改图"正是它存在的理由。侧栏默认收起成
// 24px 竖条(不占画布),展开后与图表并排,操作期间始终在视野里。
//
// 树里有两类对象,存储形状不同但**在树里是同一类东西**:
//   指标 —— 页面级布尔开关,作用于全部图格(一个开关,不是每格一个实例);
//   绘图 —— 按标的的独立对象,各有锚点 / 档位 / 锁定态。
// 所以分区渲染,只共享 id 规则(chart-objects.ts 的 `ind:` / `drawing:` 前缀)。

import { useMemo, useRef, useState } from "react"
import { useUiText } from "@/components/i18n/use-ui-text"
import { IctLayerParams, type IctLayerKey } from "@/components/watch/ict-layer-params"
import { formatPrice } from "@/lib/format"
import {
  EXTENSION_LEVELS,
  HAND_DRAWN_LEVELS,
  formatLevelLabel,
} from "@/lib/chart/fib-levels"
import { filterGridsByScope, type TreeScope } from "@/lib/chart/chart-objects"
import { styleForLevel } from "@/lib/chart/fib-style"
import { type FibGrid, hasLevels } from "@/lib/chart/fib-object"
import { toolLabelForMode } from "@/lib/chart/tool-registry"
import {
  filterRowsByQuery,
  groupLockedState,
  groupVisibleState,
  nextGroupFlag,
} from "@/lib/chart/tree-view"
import { groupRows, useDrawingsStore } from "@/lib/store/drawings"

/** 档位勾选的展示顺序:内部 11 条在前,延伸位在后,与 fib_corner.py 同序。 */
const ALL_LEVELS: readonly number[] = [...HAND_DRAWN_LEVELS, ...EXTENSION_LEVELS]

/**
 * 勾选框要展示哪些比例 = 常用档位 ∪ 这条网格自己带的档位。
 *
 * 早先只渲染 `ALL_LEVELS`,于是用户手输的 0.45 虽然进了 `levels`、图上真画了,
 * 树里却没有它的勾选框 —— 想删只能靠"恢复默认档位"连别人一起清掉。
 * 并上自定义位之后,"加进来的一定看得见、看得见的一定删得掉"。
 */
function checkboxLevels(levels: readonly number[]): number[] {
  const set = new Set<number>(ALL_LEVELS)
  for (const lv of levels) set.add(lv)
  return [...set].sort((a, b) => a - b)
}

/** 线宽下拉的候选值。与 fib-style.ts 的 0.5–6 区间对齐。 */
const FIB_WIDTH_CHOICES: readonly number[] = [0.5, 1, 1.2, 1.6, 2, 3, 4, 6]

/** 对象树里的指标行所需的最小信息(渲染不管它存在哪)。 */
export interface IndicatorObject {
  /** `tools` 上的字段名,如 "ma" / "vol"。 */
  key: string
  label: string
  params?: string
  on: boolean
  /** 所属分区：主图叠层 / 独立子窗格 / 结构层。 */
  group: "overlay" | "pane" | "structure"
  /**
   * 有参数可调的 ICT 层。展开这一行时渲染对应的参数抽屉。
   * 缺省 = 该指标没有可调参数(多数 builtin 指标就是"装了就是那个公式")。
   */
  ictLayer?: IctLayerKey
}

export function ObjectTreeDock({
  symbol,
  indicators = [],
  onToggleIndicator,
}: {
  symbol: string
  /** 页面级指标开关。不传 = 树里不出指标区(如非标的只画图)。 */
  indicators?: readonly IndicatorObject[]
  onToggleIndicator?: (key: string) => void
}) {
  const tr = useUiText()

  const open = useDrawingsStore((s) => s.dockOpen)
  const setOpen = useDrawingsStore((s) => s.setDockOpen)
  const grids = useDrawingsStore((s) => s.grids)
  const groups = useDrawingsStore((s) => s.groups)
  const select = useDrawingsStore((s) => s.select)
  const toggleSelect = useDrawingsStore((s) => s.toggleSelect)
  const selectRangeTo = useDrawingsStore((s) => s.selectRangeTo)
  const selectAllIn = useDrawingsStore((s) => s.selectAllIn)
  const clearSelection = useDrawingsStore((s) => s.clearSelection)
  const selectedIds = useDrawingsStore((s) => s.selectedIds)
  const remove = useDrawingsStore((s) => s.remove)
  const removeMany = useDrawingsStore((s) => s.removeMany)
  const setVisibleOf = useDrawingsStore((s) => s.setVisibleOf)
  const setLockedOf = useDrawingsStore((s) => s.setLockedOf)
  const rename = useDrawingsStore((s) => s.rename)
  const duplicate = useDrawingsStore((s) => s.duplicate)
  const toggleVisible = useDrawingsStore((s) => s.toggleVisible)
  const toggleLockOf = useDrawingsStore((s) => s.toggleLockOf)
  const toggleExtend = useDrawingsStore((s) => s.toggleExtend)
  const toggleExtendSideOf = useDrawingsStore((s) => s.toggleExtendSideOf)
  const toggleLevelOf = useDrawingsStore((s) => s.toggleLevelOf)
  const addLevelOf = useDrawingsStore((s) => s.addLevelOf)
  const removeLevelOf = useDrawingsStore((s) => s.removeLevelOf)
  const resetLevelsOf = useDrawingsStore((s) => s.resetLevelsOf)
  const setLevelStyleOf = useDrawingsStore((s) => s.setLevelStyleOf)
  const addGroupNamed = useDrawingsStore((s) => s.addGroupNamed)
  const removeGroupById = useDrawingsStore((s) => s.removeGroupById)
  const assignGroup = useDrawingsStore((s) => s.assignGroup)
  const collapseGroup = useDrawingsStore((s) => s.collapseGroup)

  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [groupName, setGroupName] = useState("")
  const [indOpen, setIndOpen] = useState<Record<string, boolean>>({})
  const [query, setQuery] = useState("")
  /** 每条网格各自的"新增档位"输入草稿。按对象 id 存 —— 展开多条时互不串味。 */
  const [levelInput, setLevelInput] = useState<Record<string, string>>({})
  /** 每条网格的校验错误(原文,渲染时再过 tr)。空 = 无错误。 */
  const [levelError, setLevelError] = useState<Record<string, string>>({})

  /**
   * 提交一条自定义档位。
   *
   * 两条拒绝线(而不是"悄悄取整 / 悄悄变 0"):
   *   - 空串不能被 Number("") 吃成 0 —— 那会静默加一条 0 档,用户以为自己没输;
   *   - 重复的(已在列表里)明说"已存在",而不是装作加成功。
   * 比例本身允许任意有限数:负数/大于 1 是延伸位,图上本来就要画。
   */
  const submitLevel = (gridId: string) => {
    const raw = (levelInput[gridId] ?? "").trim()
    if (!raw) {
      setLevelError((prev) => ({ ...prev, [gridId]: "请输入比例" }))
      return
    }
    const value = Number(raw)
    if (!Number.isFinite(value)) {
      setLevelError((prev) => ({ ...prev, [gridId]: "比例必须是数字" }))
      return
    }
    const grid = grids.find((g) => g.id === gridId)
    if (grid?.levels.includes(value)) {
      setLevelError((prev) => ({ ...prev, [gridId]: "该档位已存在" }))
      return
    }
    addLevelOf(gridId, value)
    setLevelInput((prev) => ({ ...prev, [gridId]: "" }))
    setLevelError((prev) => ({ ...prev, [gridId]: "" }))
  }
  /**
   * 树的范围:默认只看当前图(贴 TradingView 的对象树直觉 —— 树是当前图表私有的)。
   * "全部标的"留给跨标的批量清理:9 图格同屏时误画的线不必切 9 次图才能删。
   */
  const [scope, setScope] = useState<TreeScope>("current")

  const countForSymbol = grids.filter((g) => g.symbol === symbol).length
  /**
   * 范围内参与树渲染的绘图。分组的归属关系**不变**(仍按全量 grids 分组),
   * 否则"全部标的"下组内成员齐全、"当前图"下却少了别的标的,组头计数会跳。
   */
  const scopedGrids = filterGridsByScope(grids, scope, symbol)
  const indicatorCount = indicators.filter((i) => i.on).length

  /**
   * 搜索只过滤**显示**,不改变分组归属与选中集合:
   * 藏在筛选结果外的已选项仍会被批量按钮作用到 —— 与文件管理器一致,
   * 筛选不是"重新选中"。
   */
  const rows = useMemo(
    () => filterRowsByQuery(groupRows(scopedGrids, groups), query),
    [scopedGrids, groups, query],
  )

  /**
   * 树里"当前可见"的扁平 id 顺序 —— Shift 扩选与全选都按这个顺序走。
   * 用分组后的行序而不是存储序：用户看到的相邻关系才是他以为的区间。
   * 折叠的分组不参与;搜索后只算留下来的行。
   */
  const visibleOrder = useMemo(
    () => rows.flatMap((r) => (r.group?.collapsed ? [] : r.grids.map((g) => g.id))),
    [rows],
  )

  /**
   * 选中集合里是**绘图的裸 id**(store 与绘图树都只认裸 id)。
   * 指标不参与多选:它们是页面级布尔,没有批量显隐/锁定/删除这回事,
   * 放进集合只会得到"已选 1、按钮全灰"的死路。
   */
  const selectedDrawingIds = useMemo(() => [...selectedIds], [selectedIds])
  const selectedCount = selectedIds.size
  const anchorRef = useRef<string | null>(null)

  /** 行点击。修饰键语义与画布一致:无修饰=单选,⌘/Ctrl=加减选,Shift=区间扩选。 */
  const onRowSelect = (id: string, e: { shiftKey: boolean; metaKey: boolean; ctrlKey: boolean }) => {
    if (e.shiftKey) {
      const anchor = anchorRef.current ?? id
      selectRangeTo(visibleOrder, anchor, id)
      return
    }
    if (e.metaKey || e.ctrlKey) {
      toggleSelect(id)
      anchorRef.current = id
      return
    }
    select(id)
    anchorRef.current = id
  }

  /** 批量操作后清空选中:删完还留一串已不存在的 id 会让计数虚高。 */
  const batch = (fn: () => void) => {
    fn()
    clearSelection()
    anchorRef.current = null
  }

  /**
   * 组级眼睛/锁。作用范围 = **该组在当前范围内的成员**。
   * 混合态点一下统一到"开"(不是翻转),与行级开关的直觉一致。
   */
  const toggleGroupVisible = (ids: readonly string[], state: ReturnType<typeof groupVisibleState>) => {
    setVisibleOf(ids, nextGroupFlag(state))
  }
  const toggleGroupLocked = (ids: readonly string[], state: ReturnType<typeof groupLockedState>) => {
    setLockedOf(ids, nextGroupFlag(state))
  }

  // 收起时不渲染任何东西:开关是工具栏上的「对象 · N」(唯一入口,不另做竖条
  // —— 两个同名按钮抢同一职责,还会让 getByRole 撞名)。
  if (!open) return null

  return (
    <aside
      id="kf-object-tree"
      className="kf-dock"
      role="complementary"
      aria-label={tr("对象树")}
      data-testid="kf-dock"
    >
      <div className="kf-draw-head">
        <span className="kf-draw-head-title">
          {scope === "all"
            ? tr("全部标的的手绘对象 · 当前图 {symbol}", { symbol })
            : tr("当前图 {symbol} 的手绘对象", { symbol })}
        </span>
        <button
          type="button"
          className="kf-draw-scope"
          aria-pressed={scope === "all"}
          title={scope === "all" ? tr("切换到：只看当前图") : tr("切换到：全部标的")}
          onClick={() => setScope((s) => (s === "all" ? "current" : "all"))}
        >
          {scope === "all" ? tr("全部标的") : tr("只看当前图")}
        </button>
        <button
          type="button"
          className="kf-draw-ic kf-dock-collapse"
          aria-expanded={true}
          aria-label={tr("收起对象树")}
          title={tr("收起对象树")}
          onClick={() => setOpen(false)}
        >
          ›
        </button>
      </div>

      <div className="kf-dock-search">
        <input
          className="kf-draw-input"
          data-testid="kf-dock-search"
          value={query}
          placeholder={tr("搜索对象（名称 / 标的 / 周期）")}
          aria-label={tr("搜索对象")}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query !== "" && (
          <button
            type="button"
            className="kf-draw-ic"
            aria-label={tr("清除搜索")}
            title={tr("清除搜索")}
            onClick={() => setQuery("")}
          >
            ×
          </button>
        )}
      </div>

      {indicators.length > 0 && (
        <div className="kf-draw-group kf-draw-ind" data-testid="kf-draw-indicators">
          <div className="kf-draw-group-head">
            <span className="kf-draw-group-name">{tr("技术指标")}</span>
            <span className="kf-draw-count">
              {indicatorCount}/{indicators.length}
            </span>
          </div>
          <div className="kf-draw-group-note">{tr("页面级 · 作用于全部图格")}</div>

          {(["overlay", "pane", "structure"] as const).map((kind) => {
            const items = indicators.filter((i) => i.group === kind)
            if (items.length === 0) return null
            return (
              <div key={kind} className="kf-draw-ind-section">
                <div className="kf-draw-ind-section-head">
                  {kind === "overlay"
                    ? tr("主图叠层")
                    : kind === "pane"
                      ? tr("独立子窗格")
                      : tr("结构层")}
                </div>
                {items.map((ind) => {
                  const isOpen = !!indOpen[ind.key]
                  return (
                    <div key={ind.key} className={`kf-draw-item ${isOpen ? "open" : ""}`}>
                      <div className={`kf-draw-row ${!ind.on ? "dim" : ""}`}>
                        <button
                          type="button"
                          className="kf-draw-ic"
                          aria-label={ind.on ? tr("隐藏") : tr("显示")}
                          title={ind.on ? tr("隐藏") : tr("显示")}
                          onClick={(e) => {
                            e.stopPropagation()
                            onToggleIndicator?.(ind.key)
                          }}
                        >
                          {ind.on ? "◉" : "○"}
                        </button>
                        {/* 指标没有"拖动"概念,锁定位留空以保持三列对齐 */}
                        <span className="kf-draw-ic kf-draw-ic-empty" aria-hidden="true" />
                        <button
                          type="button"
                          className="kf-draw-caret"
                          aria-expanded={isOpen}
                          aria-label={isOpen ? tr("收起档位") : tr("展开档位")}
                          onClick={(e) => {
                            e.stopPropagation()
                            setIndOpen((m) => ({ ...m, [ind.key]: !m[ind.key] }))
                          }}
                        >
                          {isOpen ? "▾" : "▸"}
                        </button>
                        <span className="kf-draw-title">{tr(ind.label)}</span>
                        {ind.params && <span className="kf-draw-range">{tr(ind.params)}</span>}
                      </div>

                      {isOpen && (
                        <div className="kf-draw-detail">
                          {ind.ictLayer ? (
                            <IctLayerParams layer={ind.ictLayer} />
                          ) : (
                            <div className="kf-draw-field">
                              <span className="kf-draw-label">{tr("参数 {params}", { params: ind.params || "—" })}</span>
                            </div>
                          )}
                          <div className="kf-draw-group-note">{tr("页面级 · 作用于全部图格")}</div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      )}

      {/* 多选操作栏:无选中时只留一行操作提示(高度恒定,免得树内容上下跳)。
          指标行不参与多选 —— 它们是页面级布尔,没有批量显隐/锁定/删除,
          放进集合只会得到"已选 1、按钮全灰"的死路。 */}
      <div className="kf-draw-batch" data-testid="kf-draw-batch">
        {selectedCount === 0 ? (
          <span className="kf-draw-batch-hint">{tr("⌘/Ctrl 点选多项 · Shift 选区间")}</span>
        ) : (
          <>
            <span className="kf-draw-batch-hint">{tr("⌘/Ctrl 点选多项 · Shift 选区间")}</span>
            <span className="kf-draw-batch-count" data-testid="kf-draw-batch-count">
              {tr("已选 {n}", { n: selectedCount })}
            </span>
            <button
              type="button"
              className="kf-draw-btn"
              disabled={selectedDrawingIds.length === 0}
              title={tr("隐藏选中的 {n} 项", { n: selectedDrawingIds.length })}
              onClick={() => batch(() => setVisibleOf(selectedDrawingIds, false))}
            >
              {tr("设为隐藏")}
            </button>
            <button
              type="button"
              className="kf-draw-btn"
              disabled={selectedDrawingIds.length === 0}
              title={tr("显示选中的 {n} 项", { n: selectedDrawingIds.length })}
              onClick={() => batch(() => setVisibleOf(selectedDrawingIds, true))}
            >
              {tr("设为可见")}
            </button>
            <button
              type="button"
              className="kf-draw-btn"
              disabled={selectedDrawingIds.length === 0}
              title={tr("锁定选中的 {n} 项", { n: selectedDrawingIds.length })}
              onClick={() => batch(() => setLockedOf(selectedDrawingIds, true))}
            >
              {tr("锁定")}
            </button>
            <button
              type="button"
              className="kf-draw-btn"
              disabled={selectedDrawingIds.length === 0}
              onClick={() => batch(() => setLockedOf(selectedDrawingIds, false))}
            >
              {tr("解锁")}
            </button>
            <button
              type="button"
              className="kf-draw-btn kf-draw-danger"
              disabled={selectedDrawingIds.length === 0}
              title={tr("删除选中的 {n} 项", { n: selectedDrawingIds.length })}
              onClick={() => batch(() => removeMany(selectedDrawingIds))}
            >
              {tr("删除")}
            </button>
          </>
        )}
        <button
          type="button"
          className="kf-draw-batch-all"
          onClick={() => {
            if (selectedCount > 0) clearSelection()
            else selectAllIn(visibleOrder)
          }}
        >
          {selectedCount > 0 ? tr("清空选择") : tr("全选")}
        </button>
      </div>

      {rows.length === 0 && (
        <div className="kf-draw-empty">
          {query !== ""
            ? tr("没有匹配「{q}」的对象。", { q: query.trim() })
            : scope === "all"
            ? tr("还没有对象。点「斐波那契」后在图上拖出一条腿。")
            : tr("当前图上还没有对象。")}
        </div>
      )}

      {rows.map((row) => {
        const groupGrids = row.grids
        const visState = groupVisibleState(groupGrids)
        const lockState = groupLockedState(groupGrids)
        const groupIds = groupGrids.map((g) => g.id)
        return (
          <div key={row.group?.id ?? "__top__"} className="kf-draw-group">
            {row.group && (
              <div className="kf-draw-group-head">
                <button
                  type="button"
                  className="kf-draw-ic"
                  aria-label={row.group.collapsed ? tr("展开分组") : tr("收起分组")}
                  onClick={() => collapseGroup(row.group!.id)}
                >
                  {row.group.collapsed ? "▸" : "▾"}
                </button>
                {/* 组级眼睛 / 锁:一次作用于组内全部成员。
                    混合态显示 ◐ / ◪,点一下统一到"开"。 */}
                <button
                  type="button"
                  className={`kf-draw-ic ${visState === "all" ? "on" : ""} ${visState === "mixed" ? "mixed" : ""}`}
                  data-testid="kf-group-vis"
                  aria-label={visState === "all" ? tr("隐藏分组") : tr("显示分组")}
                  title={visState === "all" ? tr("隐藏分组") : tr("显示分组")}
                  onClick={() => toggleGroupVisible(groupIds, visState)}
                >
                  {visState === "all" ? "◉" : visState === "mixed" ? "◐" : "○"}
                </button>
                <button
                  type="button"
                  className={`kf-draw-ic ${lockState === "all" ? "on" : ""} ${lockState === "mixed" ? "mixed" : ""}`}
                  data-testid="kf-group-lock"
                  aria-label={lockState === "all" ? tr("解锁分组") : tr("锁定分组")}
                  title={lockState === "all" ? tr("解锁分组") : tr("锁定分组")}
                  onClick={() => toggleGroupLocked(groupIds, lockState)}
                >
                  {lockState === "all" ? "▣" : lockState === "mixed" ? "◪" : "▢"}
                </button>
                <span className="kf-draw-group-name">{row.group.name}</span>
                <span className="kf-draw-count">{groupGrids.length}</span>
                <button
                  type="button"
                  className="kf-draw-ic kf-draw-danger"
                  aria-label={tr("删除分组（组内对象保留）")}
                  title={tr("删除分组（组内对象保留）")}
                  onClick={() => removeGroupById(row.group!.id)}
                >
                  ×
                </button>
              </div>
            )}

            {!(row.group?.collapsed ?? false) &&
              row.grids.map((g) => {
                const isOpen = !!expanded[g.id]
                const isSelected = selectedIds.has(g.id)
                // 单档样式只为"当前勾着的那几档"出行 —— 没画的线调颜色没有反馈,
                // 让用户在 16 行里找 3 条可见档位纯属折磨。
                const visibleLevels = [...g.levels].sort((a, b) => a - b)
                return (
                  <div key={g.id} className={`kf-draw-item ${isOpen ? "open" : ""}`}>
                    <div
                      className={`kf-draw-row ${!g.visible ? "dim" : ""} ${isSelected ? "sel" : ""}`}
                      aria-selected={isSelected}
                      onClick={(e) => onRowSelect(g.id, e)}
                    >
                      <button
                        type="button"
                        className="kf-draw-ic"
                        aria-label={g.visible ? tr("隐藏") : tr("显示")}
                        title={g.visible ? tr("隐藏") : tr("显示")}
                        onClick={(e) => {
                          e.stopPropagation()
                          toggleVisible(g.id)
                        }}
                      >
                        {g.visible ? "◉" : "○"}
                      </button>
                      <button
                        type="button"
                        className={`kf-draw-ic ${g.locked ? "on" : ""}`}
                        aria-label={g.locked ? tr("解锁") : tr("锁定")}
                        title={g.locked ? tr("解锁") : tr("锁定")}
                        onClick={(e) => {
                          e.stopPropagation()
                          toggleLockOf(g.id)
                        }}
                      >
                        {g.locked ? "▣" : "▢"}
                      </button>
                      <button
                        type="button"
                        className="kf-draw-caret"
                        aria-expanded={isOpen}
                        aria-label={isOpen ? tr("收起档位") : tr("展开档位")}
                        onClick={(e) => {
                          e.stopPropagation()
                          setExpanded((m) => ({ ...m, [g.id]: !m[g.id] }))
                        }}
                      >
                        {isOpen ? "▾" : "▸"}
                      </button>
                      <span className="kf-draw-title">
                        {g.label || tr(toolLabelForMode(g.mode))}
                        {g.symbol !== symbol && <span className="kf-draw-sym">{g.symbol}</span>}
                      </span>
                      <span className="kf-draw-range">
                        {g.start.price === g.end.price
                          ? formatPrice(g.end.price)
                          : `${formatPrice(g.start.price)} → ${formatPrice(g.end.price)}`}
                      </span>
                    </div>

                    {isOpen && (
                      <div className="kf-draw-detail">
                        <div className="kf-draw-field">
                          <span className="kf-draw-label">{tr("名称")}</span>
                          <input
                            className="kf-draw-input"
                            value={g.label}
                            placeholder={tr("未命名")}
                            onChange={(e) => rename(g.id, e.target.value)}
                          />
                        </div>

                        <div className="kf-draw-field">
                          <span className="kf-draw-label">{tr("分组")}</span>
                          <select
                            className="kf-draw-input"
                            value={g.groupId ?? ""}
                            onChange={(e) => assignGroup(g.id, e.target.value || null)}
                          >
                            <option value="">{tr("顶层")}</option>
                            {groups.map((grp) => (
                              <option key={grp.id} value={grp.id}>
                                {grp.name}
                              </option>
                            ))}
                          </select>
                        </div>

                        {/* 档位/延伸只对斐波那契家族有意义。趋势线、矩形等基础图形
                            的 `levels` 恒为空,把这些控件留在面板里只会误导用户去点。 */}
                        {hasLevels(g.mode) && (
                          <>
                            <label className="kf-draw-check">
                              <input
                                type="checkbox"
                                checked={g.autoExtend}
                                onChange={() => toggleExtend(g.id)}
                              />
                              <span>{tr("扩展档位（1.272 / 1.414 / 1.618 / 2.0 / 2.618）")}</span>
                            </label>

                            <div className="kf-draw-field">
                              <span className="kf-draw-label">{tr("横向延伸")}</span>
                              <div className="kf-draw-extend-row">
                                <label className="kf-draw-check">
                                  <input
                                    type="checkbox"
                                    checked={g.extendLeft}
                                    onChange={() => toggleExtendSideOf(g.id, "left")}
                                  />
                                  <span>{tr("向左")}</span>
                                </label>
                                <label className="kf-draw-check">
                                  <input
                                    type="checkbox"
                                    checked={g.extendRight}
                                    onChange={() => toggleExtendSideOf(g.id, "right")}
                                  />
                                  <span>{tr("向右")}</span>
                                </label>
                              </div>
                            </div>

                            <div className="kf-draw-levels">
                              <div className="kf-draw-label">{tr("档位")}</div>
                              <div className="kf-draw-level-grid">
                                {checkboxLevels(g.levels).map((lv) => {
                                  const checked = g.levels.includes(lv)
                                  // 自定义档位(不在固定表里)只在"画着"时才存在 ——
                                  // 取消勾选 = 它从列表里消失,所以额外给一个明确的 ×。
                                  const custom = !ALL_LEVELS.includes(lv)
                                  return (
                                    <label key={lv} className={`kf-draw-level ${custom ? "custom" : ""}`}>
                                      <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={() => toggleLevelOf(g.id, lv)}
                                      />
                                      <span>{formatLevelLabel(lv)}</span>
                                      {custom && (
                                        <button
                                          type="button"
                                          className="kf-draw-level-del"
                                          aria-label={tr("移除档位 {label}", { label: formatLevelLabel(lv) })}
                                          title={tr("移除档位 {label}", { label: formatLevelLabel(lv) })}
                                          onClick={(e) => {
                                            e.preventDefault()
                                            e.stopPropagation()
                                            removeLevelOf(g.id, lv)
                                          }}
                                        >
                                          ×
                                        </button>
                                      )}
                                    </label>
                                  )
                                })}
                              </div>

                              {/* 任意比例。TV 的档位表也是"一行比例 + 一个删除",
                                  这里用输入框而不是预置一长串,免得 24 个复选框糊住侧栏。 */}
                              <div className="kf-draw-level-add">
                                <input
                                  className="kf-draw-input"
                                  inputMode="decimal"
                                  maxLength={8}
                                  placeholder={tr("比例，如 0.45")}
                                  aria-label={tr("添加档位比例")}
                                  value={levelInput[g.id] ?? ""}
                                  onChange={(e) => setLevelInput((prev) => ({ ...prev, [g.id]: e.target.value }))}
                                  onKeyDown={(e) => {
                                    if (e.key !== "Enter") return
                                    e.preventDefault()
                                    submitLevel(g.id)
                                  }}
                                />
                                <button
                                  type="button"
                                  className="kf-draw-btn"
                                  aria-label={tr("添加档位")}
                                  onClick={() => submitLevel(g.id)}
                                >
                                  {tr("添加")}
                                </button>
                              </div>
                              <div className="kf-draw-level-err" role="status" aria-live="polite">
                                {levelError[g.id] ? tr(levelError[g.id]) : ""}
                              </div>
                            </div>

                            {visibleLevels.length > 0 && (
                              <details className="kf-draw-level-styles">
                                <summary className="kf-draw-label">{tr("单档样式（颜色 / 线宽 / 别名）")}</summary>
                                <div className="kf-draw-level-style-rows">
                                  {visibleLevels.map((lv) => {
                                    const override = g.levelStyles?.find((s) => s.coeff === lv)
                                    const fallback = styleForLevel(lv)
                                    return (
                                      <div key={lv} className="kf-draw-level-style-row">
                                        <span className="kf-draw-level-style-name">{formatLevelLabel(lv)}</span>
                                        <input
                                          type="color"
                                          className="kf-draw-color"
                                          aria-label={tr("档位 {label} 的颜色", { label: formatLevelLabel(lv) })}
                                          value={override?.color ?? fallback.color}
                                          onChange={(e) => setLevelStyleOf(g.id, lv, { color: e.target.value })}
                                        />
                                        <select
                                          className="kf-draw-width"
                                          aria-label={tr("档位 {label} 的线宽", { label: formatLevelLabel(lv) })}
                                          value={String(override?.width ?? fallback.size)}
                                          onChange={(e) => {
                                            const v = Number(e.target.value)
                                            setLevelStyleOf(g.id, lv, { width: v === fallback.size ? null : v })
                                          }}
                                        >
                                          {FIB_WIDTH_CHOICES.map((w) => (
                                            <option key={w} value={String(w)}>
                                              {w}px
                                            </option>
                                          ))}
                                        </select>
                                        <input
                                          className="kf-draw-alias"
                                          aria-label={tr("档位 {label} 的别名", { label: formatLevelLabel(lv) })}
                                          placeholder={tr("别名")}
                                          maxLength={24}
                                          value={override?.text ?? ""}
                                          onChange={(e) => setLevelStyleOf(g.id, lv, { text: e.target.value })}
                                        />
                                        <input
                                          type="checkbox"
                                          aria-label={tr("档位 {label} 虚线", { label: formatLevelLabel(lv) })}
                                          checked={override?.dashed ?? fallback.dashed}
                                          onChange={(e) => setLevelStyleOf(g.id, lv, { dashed: e.target.checked })}
                                        />
                                      </div>
                                    )
                                  })}
                                </div>
                              </details>
                            )}
                          </>
                        )}

                        <div className="kf-draw-actions">
                          {hasLevels(g.mode) && (
                            <button type="button" className="kf-draw-btn" onClick={() => resetLevelsOf(g.id)}>
                              {tr("恢复默认档位")}
                            </button>
                          )}
                          <button
                            type="button"
                            className="kf-draw-btn"
                            title={tr("复制一份并向右下错开")}
                            onClick={() =>
                              duplicate(g.id, {
                                time: Math.round((g.end.time - g.start.time) * 0.05),
                                price: (g.end.price - g.start.price) * 0.05,
                              })
                            }
                          >
                            {tr("复制")}
                          </button>
                          <button
                            type="button"
                            className="kf-draw-btn kf-draw-danger"
                            onClick={() => remove(g.id)}
                          >
                            {tr("删除")}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
          </div>
        )
      })}

      <div className="kf-draw-newgroup">
        <input
          className="kf-draw-input"
          value={groupName}
          placeholder={tr("新分组名")}
          onChange={(e) => setGroupName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return
            addGroupNamed(groupName)
            setGroupName("")
          }}
        />
        <button
          type="button"
          className="kf-draw-btn"
          onClick={() => {
            addGroupNamed(groupName)
            setGroupName("")
          }}
        >
          {tr("新建分组")}
        </button>
      </div>
    </aside>
  )
}
