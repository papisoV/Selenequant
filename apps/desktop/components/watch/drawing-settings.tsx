"use client"

// 绘图设置 —— kf 顶栏「设置」按钮弹出的偏好面板。
//
// 为什么和「绘图」下拉分开:那一个是**选工具**(点完就收),这里是**调手感**
// (改完要留在原地看效果)。TradingView 也是这么分的 —— Keep drawing / Magnet
// 在左侧栏,不在画图工具列表里。
//
// 外壳沿用 `.kf-ind-picker`(portal + fixed):`.chart-main` 有 overflow:hidden,
// 弹层直接挂在工具栏里会被裁掉。

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"

import { useUiText } from "@/components/i18n/use-ui-text"
import { SettingsIcon } from "@/components/watch/drawing-icons"
import { useDrawingsStore } from "@/lib/store/drawings"
import type { MagnetMode } from "@/lib/chart/drawing-prefs"

const MAGNETS: readonly { mode: MagnetMode; label: string; hint: string }[] = [
  { mode: "off", label: "不吸附", hint: "落点自由，不吸附 K 线" },
  { mode: "weak", label: "弱吸附", hint: "离 OHLC 50px 内才吸附" },
  { mode: "strong", label: "强吸附", hint: "总是吸附到最近的开/高/低/收" },
]

export function DrawingSettings() {
  const tr = useUiText()

  const prefs = useDrawingsStore((s) => s.prefs)
  const setKeepDrawing = useDrawingsStore((s) => s.setKeepDrawing)
  const setMagnet = useDrawingsStore((s) => s.setMagnet)

  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const place = useCallback(() => {
    const r = triggerRef.current?.getBoundingClientRect()
    if (!r) return
    setPos({ left: Math.round(r.left), top: Math.round(r.bottom + 4) })
  }, [])

  useLayoutEffect(() => {
    if (open) place()
  }, [open, place])

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node | null
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

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`chart-tool chart-tool-icon ${open ? "active" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={tr("绘图设置")}
        title={tr("绘图设置：连续绘制 / 磁吸")}
        onClick={() => setOpen((v) => !v)}
      >
        <SettingsIcon />
      </button>

      {open && pos !== null && typeof document !== "undefined" &&
        createPortal(
          <div
            ref={menuRef}
            className="kf-ind-picker kf-draw-settings"
            style={{ left: `${pos.left}px`, top: `${pos.top}px` }}
            role="dialog"
            aria-label={tr("绘图设置")}
          >
            <div className="kf-tool-group">
              <div className="kf-tool-group-label">{tr("绘制方式")}</div>
              <label className="kf-draw-settings-row">
                <input
                  type="checkbox"
                  checked={prefs.keepDrawing}
                  onChange={(e) => setKeepDrawing(e.target.checked)}
                />
                <span className="kf-draw-settings-name">{tr("连续绘制")}</span>
              </label>
              <div className="kf-draw-settings-hint">
                {tr("开启后画完一个对象工具不退出，可以接着画下一个")}
              </div>
            </div>

            <div className="kf-tool-group">
              <div className="kf-tool-group-label">{tr("磁吸")}</div>
              <div className="kf-draw-seg" role="radiogroup" aria-label={tr("磁吸档位")}>
                {MAGNETS.map((m) => (
                  <button
                    key={m.mode}
                    type="button"
                    role="radio"
                    aria-checked={prefs.magnet === m.mode}
                    className={`kf-draw-seg-btn ${prefs.magnet === m.mode ? "active" : ""}`}
                    title={tr(m.hint)}
                    onClick={() => setMagnet(m.mode)}
                  >
                    {tr(m.label)}
                  </button>
                ))}
              </div>
              <div className="kf-draw-settings-hint">
                {tr("落点吸附到 K 线的开/高/低/收；只改价格，不改时间")}
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  )
}

export default DrawingSettings
