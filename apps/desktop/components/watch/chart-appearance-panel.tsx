"use client"

// 图表外观 —— 背景 / 网格 / K 线配色。kf 顶栏调色板按钮弹出的面板。
//
// 为什么独立成一块(而不是塞进「绘图设置」):这是**图表本身**的外观,
// 与绘图工具(hover/画线)无关;关掉全部绘图对象它依然有意义。
// TradingView 的划分也一样:右键 → 设置里有独立的「图表 / 画布」和「商品(K线)」两页。
//
// 颜色必须给具体值:lightweight-charts 走 canvas,`var(--x)` / `oklch()` 它读不懂,
// 会整块渲染成透明。所以输入一律限定 #rrggbb(原生 `<input type="color">` 天然满足),
// 解析在 lib/chart/chart-appearance.ts 里做兜底。

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"

import { useUiText } from "@/components/i18n/use-ui-text"
import { PaletteIcon } from "@/components/watch/drawing-icons"
import { useDrawingsStore } from "@/lib/store/drawings"
import {
  type ChartCandleColors,
  DEFAULT_CHART_APPEARANCE,
  cloneAppearance,
  defaultGridColor,
  isDefaultAppearance,
} from "@/lib/chart/chart-appearance"

/** K 线的三组颜色,与 TV「商品 → 颜色」页一致:主体 / 边框 / 影线,各自涨跌两色。 */
const CANDLE_ROWS: readonly {
  key: keyof ChartCandleColors
  label: string
}[] = [
  { key: "up", label: "上涨主体" },
  { key: "down", label: "下跌主体" },
  { key: "borderUp", label: "上涨边框" },
  { key: "borderDown", label: "下跌边框" },
  { key: "wickUp", label: "上涨影线" },
  { key: "wickDown", label: "下跌影线" },
]

export function ChartAppearance() {
  const tr = useUiText()

  const appearance = useDrawingsStore((s) => s.appearance)
  const setAppearance = useDrawingsStore((s) => s.setAppearance)

  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const place = useCallback(() => {
    const r = triggerRef.current?.getBoundingClientRect()
    if (!r) return
    // 面板比别的弹层高(六行颜色 + 两组开关),贴右边缘时往左收,
    // 免得在窄屏上被视口裁掉一半。
    const width = 252
    const left = Math.min(Math.round(r.left), Math.max(8, window.innerWidth - width - 8))
    setPos({ left, top: Math.round(r.bottom + 4) })
  }, [])

  useLayoutEffect(() => {
    if (open) place()
  }, [open, place])

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node | null
      // 「取色器」弹窗在原生 Chromium 里是独立窗口,不会命中这里 ——
      // 点开色板时 document 上收不到 pointerdown,面板不会被误关。
      if (menuRef.current?.contains(t) || triggerRef.current?.contains(t)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    const onViewport = () => setOpen(false)
    document.addEventListener("pointerdown", onDown, true)
    document.addEventListener("keydown", onKey)
    window.addEventListener("resize", onViewport)
    window.addEventListener("scroll", onViewport, true)
    return () => {
      document.removeEventListener("pointerdown", onDown, true)
      document.removeEventListener("keydown", onKey)
      window.removeEventListener("resize", onViewport)
      window.removeEventListener("scroll", onViewport, true)
    }
  }, [open])

  // 面板里每一处写入都先克隆再改:store 里的对象与默认常量、与上一次改动
  // 都不共享引用,避免"改一个色顺手改了默认值"。
  const setCandle = (key: keyof ChartCandleColors, color: string) => {
    const next = cloneAppearance(appearance)
    next.candles = { ...next.candles, [key]: color }
    setAppearance({ candles: next.candles })
  }

  const setBackgroundKind = (kind: "transparent" | "solid") => {
    const next = cloneAppearance(appearance)
    next.background = { ...next.background, kind }
    setAppearance({ background: next.background })
  }

  const setBackgroundColor = (color: string) => {
    const next = cloneAppearance(appearance)
    next.background = { ...next.background, kind: "solid", color }
    setAppearance({ background: next.background })
  }

  /** 网格色为空串 = 跟随主题默认(半透明白)。取色器里显示的是那个默认值的近似。 */
  const gridSwatch = appearance.gridColor || defaultGridColor()

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`chart-tool chart-tool-icon ${open ? "active" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={tr("图表外观")}
        title={tr("图表外观：背景 / 网格 / K 线颜色")}
        data-testid="kf-chart-appearance"
        onClick={() => setOpen((v) => !v)}
      >
        <PaletteIcon />
      </button>

      {open && pos !== null && typeof document !== "undefined" &&
        createPortal(
          <div
            ref={menuRef}
            className="kf-ind-picker kf-draw-settings kf-appearance"
            style={{ left: `${pos.left}px`, top: `${pos.top}px` }}
            role="dialog"
            aria-label={tr("图表外观")}
          >
            <div className="kf-tool-group">
              <div className="kf-tool-group-label">{tr("背景")}</div>
              <div className="kf-draw-seg" role="radiogroup" aria-label={tr("背景类型")}>
                {(["transparent", "solid"] as const).map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    role="radio"
                    aria-checked={appearance.background.kind === kind}
                    className={`kf-draw-seg-btn ${appearance.background.kind === kind ? "active" : ""}`}
                    onClick={() => setBackgroundKind(kind)}
                  >
                    {tr(kind === "transparent" ? "透明" : "纯色")}
                  </button>
                ))}
              </div>
              {appearance.background.kind === "solid" && (
                <label className="kf-appearance-row">
                  <span className="kf-appearance-name">{tr("背景色")}</span>
                  <input
                    type="color"
                    aria-label={tr("背景色")}
                    value={appearance.background.color}
                    onChange={(e) => setBackgroundColor(e.target.value)}
                  />
                  <span className="kf-appearance-hex">{appearance.background.color}</span>
                </label>
              )}
              <div className="kf-draw-settings-hint">
                {tr("透明跟随站点主题；纯色会把图表区域整块填满")}
              </div>
            </div>

            <div className="kf-tool-group">
              <div className="kf-tool-group-label">{tr("网格")}</div>
              <label className="kf-draw-settings-row">
                <input
                  type="checkbox"
                  checked={appearance.gridVisible}
                  onChange={(e) => setAppearance({ gridVisible: e.target.checked })}
                />
                <span className="kf-draw-settings-name">{tr("显示网格线")}</span>
              </label>
              <label className="kf-appearance-row">
                <span className="kf-appearance-name">{tr("网格颜色")}</span>
                <input
                  type="color"
                  aria-label={tr("网格颜色")}
                  value={gridSwatch}
                  disabled={!appearance.gridVisible}
                  onChange={(e) => setAppearance({ gridColor: e.target.value })}
                />
                <span className="kf-appearance-hex">{appearance.gridColor || tr("默认")}</span>
              </label>
            </div>

            <div className="kf-tool-group">
              <div className="kf-tool-group-label">{tr("K 线颜色")}</div>
              {CANDLE_ROWS.map((row) => (
                <label key={row.key} className="kf-appearance-row">
                  <span className="kf-appearance-name">{tr(row.label)}</span>
                  <input
                    type="color"
                    aria-label={tr(row.label)}
                    value={appearance.candles[row.key]}
                    onChange={(e) => setCandle(row.key, e.target.value)}
                  />
                  <span className="kf-appearance-hex">{appearance.candles[row.key]}</span>
                </label>
              ))}
            </div>

            <div className="kf-appearance-footer">
              <button
                type="button"
                className="kf-draw-btn"
                disabled={isDefaultAppearance(appearance)}
                onClick={() => setAppearance(cloneAppearance(DEFAULT_CHART_APPEARANCE))}
              >
                {tr("恢复默认")}
              </button>
            </div>
          </div>,
          document.body,
        )}
    </>
  )
}

export default ChartAppearance
