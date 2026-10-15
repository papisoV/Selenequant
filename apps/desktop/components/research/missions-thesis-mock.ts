/**
 * Thesis 计分卡 mock — Keltner 真实数字(bt-b62d32ac),与 missions-data.ts 同源。
 * 注(2026-10-02 核实):前端已改走真链路(missions-page.tsx:25),
 * 本 mock 仅被 thesis-scorecard.test.tsx 引用,不再喂线上组件。
 */

import type { DriftCheckEntry, MissionEvidenceRow, MissionPillar, ThesisScorecard } from "./missions-thesis";

export const THESIS_KELTNER_MOCK: ThesisScorecard = {
  missionId: "msn-keltner-sol-4h-001",
  pillars: [
    {
      id: "pil-a1b2c3d4e5f6",
      claim:
        "当前配置(声明 BTC/USDT 1h,实际 SOL/USDT 4h,三重止损口径)下 Keltner 突破有正期望",
      priority: "core",
      baseline: "回测 bt-b62d32ac:总收益 -35.6%,胜率 0/7",
      expectedPath: "SOL 4h 通道突破后 7 笔内应出现至少 1 笔止盈(tp_pct=14.4%)",
      confirmThreshold: "连续 20 笔样本 profit_factor ≥ 1.2",
      breakThreshold: "任一 20 笔窗口 profit_factor < 0.5 且胜率 < 20%",
      thresholdProvenance: "draft",
      status: "broken",
      signal: "invalidating",
      statusNote: "三重漂移使任何调优结论不可信;7 笔全亏且无一止盈",
      supersededBy: null,
      nextProofPoint: "2026-09-30",
      nextProofSource: "配置统一后的 SOL 4h 重回测",
    },
    {
      id: "pil-b2c3d4e5f6a1",
      claim: "Keltner 框架本身在 SOL 4h 上有可挖掘的正期望(参数错配非框架失效)",
      priority: "supporting",
      baseline: "买入持有同期 -51.8%;现金平台段显示择时雏形",
      expectedPath: "修复配置后重新调参,20 笔样本内期望值转正",
      confirmThreshold: "SOL 4h 重调参数后 expectancy > 0 且 20 笔样本",
      breakThreshold: "配置修复 + 重调参后 expectancy 仍 < -100 USDT/笔",
      thresholdProvenance: "draft",
      status: "untested",
      signal: "untested",
      statusNote: null,
      supersededBy: null,
      nextProofPoint: "2026-10-15",
      nextProofSource: "Keltner V2 SOL 4h 重调参回测",
    },
  ],
  aggregate: "broken",
  aggregateReason:
    "唯一 core 支柱已破毁(7/7 全亏 + 三重配置漂移),聚合取 broken;框架层假设未经新样本检验",
  requiresOverride: false,
  evidence: [
    {
      id: "ev-t-001",
      pillarId: "pil-a1b2c3d4e5f6",
      occurredAt: "2026-06-30",
      sourceType: "backtest",
      sourceRef: "bt-b62d32ac",
      fact: "7 笔交易全部亏损,账面 -35.6%,平均单笔 -508.9 USDT,无一笔触及止盈",
      interpretation: "当前配置的突破追入被系统性扫损,信号部分是纯负贡献",
      signal: "invalidating",
      followUp: "统一标的/周期/止损口径后重跑",
    },
    {
      id: "ev-t-002",
      pillarId: "pil-a1b2c3d4e5f6",
      occurredAt: "2026-06-30",
      sourceType: "config",
      sourceRef: "strat-6395c312",
      fact: "声明 BTC/USDT 1h,回测实际 SOL/USDT 4h;止损口径 3%/6.37%/4% 三者并存",
      interpretation: "配置从未针对 SOL 4h 校准,参数集大概率来自 BTC 1h 调优",
      signal: "strongly_weakening",
      followUp: "先修配置再谈参数",
    },
    {
      id: "ev-t-003",
      pillarId: "pil-b2c3d4e5f6a1",
      occurredAt: "2026-06-30",
      sourceType: "backtest",
      sourceRef: "bt-b62d32ac",
      fact: "权益两个长现金平台段(7918.7 / 7420.1),同期买入持有 -51.8%,策略 -35.6%",
      interpretation: "跑赢 16.1pp 全部来自'少做'而非'做对';择时雏形未被利用",
      signal: "mildly_weakening",
      followUp: null,
    },
  ],
  lastDriftCheck: {
    occurredAt: "2026-09-01",
    checkedBy: "selene",
    patterns: {
      reason_changed: { present: false },
      catalyst_laundering: {
        present: true,
        note: "亏损归因从'参数错'滑向'市场太弱',需锚定回测证据",
      },
      valuation_substitution: { present: false },
      dismissal_by_horizon: { present: false },
      protection_as_reason: { present: false },
    },
  },
} satisfies ThesisScorecard;
