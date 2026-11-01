import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { TuningResultSummary } from "@/lib/tuning-bridge";

import { TuningResultCard } from "@/components/research/cards/tuning-result-card";

const base = (): TuningResultSummary => ({
  file_name: "tuning_20260923_000001.json",
  timestamp: "20260923_000001",
  strategy_id: "bb_v3",
  strategy_name: "布林带升级版",
  interval: "4h",
  exchange: "binance",
  market_type: "futures",
  symbol: "BTC/USDT",
  best_sharpe: 2.1,
  best_params: { bb_len: 20 },
  total_return: 0.5,
  max_drawdown: 0.1,
  win_rate: 0.6,
  n_trials: 30,
  optimization_time: 12.5,
});

describe("TuningResultCard OOS 标记", () => {
  it("无 OOS 字段（旧 JSON）→ 不渲染徽章与 OOS 行", () => {
    render(<TuningResultCard r={base()} applying={false} onClick={() => {}} />);
    expect(screen.getByText("4h")).toBeInTheDocument();
    expect(screen.queryByText(/OOS衰减/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^OOS /)).not.toBeInTheDocument();
  });

  it("oos_sharpe 存在且未踢出 → 显示 OOS 值", () => {
    const r = { ...base(), oos_sharpe: 0.42 };
    render(<TuningResultCard r={r} applying={false} onClick={() => {}} />);
    expect(screen.getByText("OOS 0.42")).toBeInTheDocument();
    expect(screen.queryByText(/OOS衰减/)).not.toBeInTheDocument();
  });

  it("oos_rejected → 徽章 + 卡片降透明度", () => {
    const r = { ...base(), oos_sharpe: 0.1, oos_rejected: true };
    const { container } = render(<TuningResultCard r={r} applying={false} onClick={() => {}} />);
    expect(screen.getByText("OOS衰减")).toBeInTheDocument();
    expect(screen.queryByText("4h")).not.toBeInTheDocument();
    expect(screen.queryByText(/^OOS /)).not.toBeInTheDocument();
    expect(container.firstElementChild?.className).toContain("opacity-50");
  });

  it("all_rejected → trades不足 徽章", () => {
    const r = { ...base(), all_rejected: true };
    render(<TuningResultCard r={r} applying={false} onClick={() => {}} />);
    expect(screen.getByText("trades不足")).toBeInTheDocument();
  });
});

describe("TuningResultCard 美股化标注 (US-003/US-004)", () => {
  it("pre_noop_fix → 修复前 角标(与 gated 徽标叠加不互斥)", () => {
    const r = { ...base(), pre_noop_fix: true };
    render(<TuningResultCard r={r} applying={false} onClick={() => {}} />);
    expect(screen.getByText("修复前")).toBeInTheDocument();
  });

  it("gated + pre_noop_fix → 两个角标同时出现", () => {
    const r = { ...base(), oos_rejected: true, pre_noop_fix: true };
    render(<TuningResultCard r={r} applying={false} onClick={() => {}} />);
    expect(screen.getByText("OOS衰减")).toBeInTheDocument();
    expect(screen.getByText("修复前")).toBeInTheDocument();
  });

  it("pre_noop_fix 缺省(旧数据)→ 无修复前角标", () => {
    render(<TuningResultCard r={base()} applying={false} onClick={() => {}} />);
    expect(screen.queryByText("修复前")).not.toBeInTheDocument();
  });

  it("annualization_factor 252 → 尾行含 年化252", () => {
    const r = { ...base(), annualization_factor: 252 };
    render(<TuningResultCard r={r} applying={false} onClick={() => {}} />);
    expect(screen.getByText(/年化252/)).toBeInTheDocument();
  });

  it("annualization_factor null → 尾行不含年化", () => {
    const r = { ...base(), annualization_factor: null };
    render(<TuningResultCard r={r} applying={false} onClick={() => {}} />);
    expect(screen.queryByText(/年化/)).not.toBeInTheDocument();
  });
});
