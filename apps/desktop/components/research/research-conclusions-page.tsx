"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { FlaskConical, ShieldAlert, CheckCircle2 } from "lucide-react"

interface ConclusionEntry {
  id: string
  verdict: "falsified" | "accepted"
  title: string
  conclusion: string
  evidence: string[]
  switchState: string
}

// 2026-08-13 ~ 2026-08-14 TG 因子研究结论登记（静态，源自各研究笔记 md，无写操作）
const CONCLUSIONS: ConclusionEntry[] = [
  {
    id: "direction",
    verdict: "falsified",
    title: "TG 方向水晶球证伪",
    conclusion: "消息方向分量不存在 — 消息 = 事件传感器（量能/波动），不是方向水晶球。方向维度不做 alpha，只做风险调制。",
    evidence: [
      "方向确认组合模式（TG 方向 × 策略一致才入场）月控后 p=0.547 / 0.585，梯度消失符号翻正",
      "R1b 入场层：aligned -0.51% < opposed +0.14%，方向与假设相反",
      "2025-11 单月崩溃期假象剔除后（2025-12 之后）bull -0.03% vs bear -0.15% p=0.547",
      "方向动量 / 方向反向结构 / 催化剂方向分离 / 方向确认组合 全部证伪",
    ],
    switchState: "方向通道:已移除，管道重构为事件传感器",
  },
  {
    id: "catalyst",
    verdict: "falsified",
    title: "波动窗口催化剂时机证伪",
    conclusion: "主策略入场落在 TG 波动窗口（事件后 48h）内，边际表现不被放大 — 动力抬升的是方差/尾部，不是期望收益。",
    evidence: [
      "window_in 胜率 52.6% vs window_out 40.0%，但 p=0.549 不显著（月控后 p=0.585）",
      "左偏机制：median 正 + mean 负 = 小赢多 + 偶尔大亏，高波动 regime 典型分布，非 edge",
      "扣成本后两组全负：window_in -0.38%/trade",
      "24-48h 波动抬升带零覆盖（w24_out_w48_in=0），策略信号只抓冲击 bar",
    ],
    switchState: "催化剂信号:不接入",
  },
  {
    id: "forward_vol",
    verdict: "accepted",
    title: "前向波动抬升白名单（币种 × 类型异质）",
    conclusion: "只有特定组合的事件后波动抬升有统计支撑；magnitude 分级因子不可用（负向）。TGVolRegimeSource 白名单即由此证据校准。",
    evidence: [
      "macro → ETH/SOL 24-48h 波动 1.15-1.20x（p≤0.004）",
      "market → BTC 24-48h 波动 1.13-1.19x（p≤0.004）",
      "macro-BTC 不持续（单 bar 尖峰，0.94-1.03x ns）",
      "magnitude 分级证伪：macro 全币种负 IC（ETH 24h -0.32 p=0.002）",
    ],
    switchState: "regime feed:opt-in 默认关（enable_tg_regime=False）",
  },
  {
    id: "offensive",
    verdict: "falsified",
    title: "三进攻方向判死",
    conclusion: "做市/流动性收割、资金费率套利、事件驱动期权卖权三方向全部判死 — TG 不做收益进攻，只做风险调制。",
    evidence: [
      "做市/流动性收割：方向研究证伪",
      "资金费率套利：方向研究证伪",
      "事件驱动期权卖权：依赖账户权限，门槛高，判死",
    ],
    switchState: "无接入",
  },
  {
    id: "overlay",
    verdict: "accepted",
    title: "风险调制 overlay（冲击减仓）",
    conclusion: "TG 事件 → 只减仓 overlay：±24h 冲击窗口 × 0.5，不赌方向。预注册 A/B 方向证伪但减仓逻辑保留，opt-in 默认关。",
    evidence: [
      "overlay ±24h × 0.5 减仓，无 side/direction key",
      "P5 验收 1/2 证伪（bb_v3 仅 11-12 笔仓，保护被盈利缩量盖过）",
      "用户 2026-08-13 决策：接受证伪，overlay 保持 opt-in",
    ],
    switchState: "use_event_overlay_source:默认 False（opt-in）",
  },
  {
    id: "regime_live",
    verdict: "accepted",
    title: "波动 regime 持续性层接线 live",
    conclusion: "48h × 0.85 持续性 de-risk 层（独立 B feed）已接入 EventGuard 风险预算通道，白名单组合非复合 min() 合并且单事实源。",
    evidence: [
      "B 首期骨架验收 6/6（窗口内波动 0.85×baseline、MDD 不劣化、Sharpe 不毁）",
      "P4 接线：get_position_multiplier min() 合并，不进 get_tg_multiplier 防宏双砍",
      "白名单 overlap 仅 BTC:market（前 2h 0.7 更严 / 后 46h 0.85），非复合双砍",
      "新增单测 11 用例全绿，回归 alpha-core 342 / strategy-engine 430",
    ],
    switchState: "enable_tg_regime:默认 False，翻转即 go-live",
  },
]

export function ResearchConclusionsPage() {
  return (
    <div className="h-full overflow-y-auto"><div className="space-y-6 p-4">
      <div className="flex items-center gap-2">
        <FlaskConical className="h-5 w-5" />
        <h1 className="text-lg font-semibold">因子研究报告（2026-08-13 ~ 08-14）</h1>
        <Badge variant="outline" className="text-xs">只读 · 结论登记</Badge>
      </div>
      <p className="text-sm text-muted-foreground">
        TG 因子五维审计后的结论沉淀：<strong>方向 + 催化剂时机双重证伪 → TG 收敛为风险调制（减损），非收益增强</strong>。
        有效组合价值是 de-risk：TG overlay（冲击 ±24h × 0.5）+ TGVolRegimeSource（持续性 48h × 0.85），均 opt-in 默认关。
      </p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {CONCLUSIONS.map((c) => (
          <Card key={c.id} className={c.verdict === "accepted" ? "border-emerald-200" : "border-red-200"}>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm">
                {c.verdict === "accepted" ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                ) : (
                  <ShieldAlert className="h-4 w-4 text-red-500" />
                )}
                {c.title}
                <Badge variant={c.verdict === "accepted" ? "default" : "destructive"} className="ml-auto text-xs">
                  {c.verdict === "accepted" ? "验收" : "证伪"}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm space-y-2">
              <p>{c.conclusion}</p>
              <ul className="list-disc list-inside text-xs text-muted-foreground space-y-0.5">
                {c.evidence.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
              <div className="pt-1">
                <span className="text-xs font-medium">生效开关：</span>
                <span className="text-xs">{c.switchState}</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      </div>
    </div>
  )
}
