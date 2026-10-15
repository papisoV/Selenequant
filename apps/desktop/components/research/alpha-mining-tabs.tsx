"use client"

import type { ReactNode } from "react"

/** 五 tab 骨架(prd 拍板:训练/验证/回测/监控/治理;草案的"部署/产物"并入训练与治理语义)。 */
export const WORKBENCH_TABS = [
  { id: "train", label: "训练" },
  { id: "validate", label: "验证" },
  { id: "backtest", label: "回测" },
  { id: "monitor", label: "监控" },
  { id: "governance", label: "治理" },
] as const

export type WorkbenchTabId = (typeof WORKBENCH_TABS)[number]["id"]

export const GUIDE_STEPS = [
  { title: "训练", desc: "Transformer + REINFORCE 挖因子" },
  { title: "验证", desc: "T4 折窗 · solo · 成本压力" },
  { title: "回测", desc: "8 指标 · 净值 · 滚动夏普" },
  { title: "监控", desc: "B4 四元组因子读数" },
  { title: "治理", desc: "T4 门控部署 + 审计历史" },
] as const

/** Tab 条:编号 + 完成态(tdone = 已走过的步骤)+ 当前 on。切换 120ms fade+4px slide 在 globals.css wbPaneIn。 */
export function WorkbenchTabBar({
  active,
  onChange,
}: {
  active: number
  onChange: (i: number) => void
}) {
  return (
    <div className="wb-tabs" role="tablist" aria-label="Alpha 工作台">
      {WORKBENCH_TABS.map((t, i) => (
        <button
          key={t.id}
          role="tab"
          type="button"
          aria-selected={i === active}
          aria-label={t.label}
          data-testid={`wb-tab-${t.id}`}
          className={`wb-tab ${i === active ? "on" : ""} ${i < active ? "tdone" : ""}`}
          onClick={() => onChange(i)}
        >
          <span className="tno">{i + 1}</span>
          {t.label}
        </button>
      ))}
    </div>
  )
}

/** 五步引导条:与 tab 共享同一个 active 索引 = 双向联动(点条目切 tab;切 tab 同步 step-on)。 */
export function GuideStrip({
  active,
  onChange,
}: {
  active: number
  onChange: (i: number) => void
}) {
  return (
    <div className="guide-strip" data-testid="guide-strip">
      {GUIDE_STEPS.map((s, i) => (
        <button
          key={s.title}
          type="button"
          data-testid={`guide-step-${i}`}
          className={`guide-item ${i === active ? "step-on" : ""}`}
          onClick={() => onChange(i)}
        >
          <span className="gt">{String(i + 1).padStart(2, "0")} · {s.title}</span>
          <span className="gd">{s.desc}</span>
        </button>
      ))}
    </div>
  )
}

/** 显隐矩阵单元:非活动 pane display:none;活动 pane 重挂 .on 触发 wbPaneIn(120ms)。 */
export function WbPane({
  active,
  index,
  testId,
  children,
}: {
  active: number
  index: number
  testId?: string
  children: ReactNode
}) {
  return (
    <div
      role="tabpanel"
      data-testid={testId ?? `wb-pane-${index}`}
      className={`wb-pane ${active === index ? "on" : ""}`}
    >
      {children}
    </div>
  )
}
