"use client"

// 绘图工具栏 —— kf 顶栏里的一组**几何图标按钮**。
//
// 1022 改版:原来是「绘图 ▾」一个大文字按钮 + 文字列表弹层。用户点名要
// TradingView 那种"几何图形按钮":工具本身就是一个图形(斐波那契=几条比例线,
// 趋势线=两端带把手的斜线),看形状比读六个中文词快,也省横向像素。
//
// 为什么图标能直铺进 28px 的工具栏:每个按钮 22×22、图形 16×16,7 个加分隔符
// 约 180px;原先那个文字按钮(~64px)+「对象 · N」(~70px)+「绘图设置」(~60px)
// 合计相近,净增不多。真挤不下时 `.chart-toolbar` 横向滚动(滚动条隐藏),
// 不会像以前那样把尾部工具静默裁掉。
//
// 树本体**不在这里**:它是 `.chart-area` 里的第三列(见 `object-tree-dock.tsx`)。

import { useDrawingsStore, type DrawingTool } from "@/lib/store/drawings"
import { useUiText } from "@/components/i18n/use-ui-text"
import { DrawingSettings } from "@/components/watch/drawing-settings"
import { ChartAppearance } from "@/components/watch/chart-appearance-panel"
import {
  DeleteIcon,
  ObjectsIcon,
  SelectIcon,
  ToolIcon,
} from "@/components/watch/drawing-icons"
import { TOOLS, TOOL_ORDER, isDrawingTool } from "@/lib/chart/tool-registry"

export type { IndicatorObject } from "@/components/watch/object-tree-dock"

interface IconButtonProps {
  label: string
  hint: string
  active?: boolean
  onClick: () => void
  children: React.ReactNode
  /** 徽标计数(对象数 / 选中数),0 或 undefined 不显示。 */
  badge?: number
  /** 附加的 data/aria 钩子(例如 aria-controls)。 */
  extra?: Record<string, string>
}

/** 工具栏图标按钮的统一外壳 —— 三个入口(选择/工具/对象)长得一样才不散。 */
function IconButton({ label, hint, active, onClick, children, badge, extra }: IconButtonProps) {
  return (
    <button
      type="button"
      className={`chart-tool chart-tool-icon ${active ? "active" : ""}`}
      aria-label={label}
      aria-pressed={active}
      title={hint}
      onClick={onClick}
      {...extra}
    >
      {children}
      {badge ? <span className="chart-tool-badge">{badge > 99 ? "99+" : badge}</span> : null}
    </button>
  )
}

export function DrawingTools({ symbol }: { symbol: string }) {
  const tr = useUiText()

  const grids = useDrawingsStore((s) => s.grids)
  const selectedIds = useDrawingsStore((s) => s.selectedIds)
  const activeTool = useDrawingsStore((s) => s.activeTool)
  const setActiveTool = useDrawingsStore((s) => s.setActiveTool)
  const dockOpen = useDrawingsStore((s) => s.dockOpen)
  const setDockOpen = useDrawingsStore((s) => s.setDockOpen)
  const removeMany = useDrawingsStore((s) => s.removeMany)

  const countForSymbol = grids.filter((g) => g.symbol === symbol).length
  const drawing = isDrawingTool(activeTool)
  const selectedCount = selectedIds.size

  /** 再点一次已激活的工具 = 退出绘图态(TV 的双击同一个工具也是这个语义)。 */
  const pick = (tool: DrawingTool) => setActiveTool(activeTool === tool ? "select" : tool)

  return (
    <>
      <div className="chart-tool-sep" />
      <div className="chart-tool-group" role="group" aria-label={tr("绘图工具")}>
        <IconButton
          label={tr("选择")}
          hint={tr("选择：点选/拖动已有对象（Esc 取消，Delete 删除）")}
          active={!drawing}
          onClick={() => setActiveTool("select")}
        >
          <SelectIcon />
        </IconButton>
        {TOOL_ORDER.map((kind) => {
          const spec = TOOLS[kind]
          return (
            <IconButton
              key={kind}
              label={tr(spec.label)}
              hint={tr(spec.hint)}
              active={activeTool === kind}
              onClick={() => pick(kind)}
            >
              <ToolIcon kind={kind} />
            </IconButton>
          )
        })}
      </div>

      {/* 有选中才出现:给 Delete 键一个看得见的落点,也顺手支持鼠标用户。
          删完最后一个对象自己消失,不留一个永远灰着的按钮。 */}
      {selectedCount > 0 && (
        <IconButton
          label={tr("删除选中的 {n} 项", { n: selectedCount })}
          hint={tr("删除选中的 {n} 项（Delete）", { n: selectedCount })}
          onClick={() => removeMany(Array.from(selectedIds))}
          badge={selectedCount}
        >
          <DeleteIcon />
        </IconButton>
      )}

      <div className="chart-tool-sep" />
      <IconButton
        label={tr("对象树")}
        hint={tr("对象树：显示/锁定/档位/分组（{n} 个对象）", { n: countForSymbol })}
        active={dockOpen}
        onClick={() => setDockOpen(!dockOpen)}
        badge={countForSymbol}
        extra={{ "aria-controls": "kf-object-tree" }}
      >
        <ObjectsIcon />
      </IconButton>

      {/* 手感设置(连续绘制 / 磁吸)与图表外观。挂在绘图子系统里:
          它们与「画什么」「怎么画」是同一组动作,拆开会让工具栏多出两个
          看不出归属的设置按钮。 */}
      <DrawingSettings />
      <ChartAppearance />
    </>
  )
}
