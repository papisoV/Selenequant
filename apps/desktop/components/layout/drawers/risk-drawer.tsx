"use client";

import { useState } from "react";
import { DrawerPanel } from "./drawer-panel";
import { useApi } from "@/lib/hooks/use-api";
import { clickable } from "@/lib/a11y/clickable";
import { useUiText } from "@/components/i18n/use-ui-text"

interface Props {
  show: boolean;
  onClose: () => void;
}

// 后端暂无 /api/v1/risk/status,用 /api/v1/portfolio 计算总敞口/日亏损
// 其他风险字段(最大回撤/单仓敞口上限/总杠杆上限/相关性)后端无对应数据,显示 "—"
interface PortfolioItem {
  symbol?: string;
  side?: string;
  size?: number;
  entry_price?: number;
  current_price?: number;
  unrealized_pnl?: number;
}

const fmtMoney = (v: number): string => {
  if (!Number.isFinite(v) || v === 0) return "$0";
  const abs = Math.abs(v);
  if (abs >= 1000) return `$${(v / 1000).toFixed(1)}K`;
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};

export function RiskDrawer({ show, onClose }: Props) {
  const tr = useUiText()

  const [toggles, setToggles] = useState({
    autoStop: true,
    hedgeOn: true,
    maxDrawdown: false,
    dailyLimit: true,
  });
  const { data: portfolio } = useApi<PortfolioItem[] | null>("/api/v1/portfolio");

  const hasData =
    Array.isArray(portfolio) && portfolio.length > 0 &&
    portfolio.some((p) => Math.abs(Number(p.size ?? 0)) > 0);

  const totalExposure = (() => {
    if (!hasData || !portfolio) return "—";
    let total = 0;
    portfolio.forEach((p) => {
      const size = Math.abs(Number(p.size ?? 0));
      const px = Number(p.current_price ?? p.entry_price ?? 0);
      total += size * px;
    });
    return fmtMoney(total);
  })();

  const dailyLoss = (() => {
    if (!hasData || !portfolio) return "—";
    let pnl = 0;
    portfolio.forEach((p) => {
      pnl += Number(p.unrealized_pnl ?? 0);
    });
    if (pnl >= 0) return "$0";
    return `-$${Math.abs(pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  })();

  const dailyLossPct = (() => {
    if (!hasData || !portfolio) return 0;
    let pnl = 0;
    let equity = 0;
    portfolio.forEach((p) => {
      pnl += Number(p.unrealized_pnl ?? 0);
      const size = Math.abs(Number(p.size ?? 0));
      const px = Number(p.current_price ?? p.entry_price ?? 0);
      equity += size * px;
    });
    if (equity <= 0) return 0;
    return Math.min(100, Math.abs(pnl) / 5000 * 100);
  })();

  const toggle = (k: keyof typeof toggles) =>
    setToggles((t) => ({ ...t, [k]: !t[k] }));

  return (
    <DrawerPanel show={show} title={tr("风控中心 · RISK")} onClose={onClose}>
      <div className="dp-metrics-section">
        <div className="dp-metrics-label">{tr("风险敞口")}</div>
        <div className="dp-metrics-grid">
          <div className="dp-metric-card">
            <div className="dp-metric-lab">{tr("总敞口")}</div>
            <div className="dp-metric-val gold">{totalExposure}</div>
          </div>
          <div className="dp-metric-card">
            <div className="dp-metric-lab">{tr("最大回撤")}</div>
            <div className="dp-metric-val neg">—</div>
          </div>
        </div>
      </div>

      <div className="risk-item">
        <div className="risk-line">
          <span className="risk-lab">{tr("单仓位敞口")}</span>
          <span className="risk-val">—</span>
        </div>
        <div className="risk-bar">
          <div className="risk-bar-fill" style={{ width: "0%" }} />
        </div>
      </div>
      <div className="risk-item">
        <div className="risk-line">
          <span className="risk-lab">{tr("总杠杆")}</span>
          <span className="risk-val">— / 5x</span>
        </div>
        <div className="risk-bar">
          <div className="risk-bar-fill ok" style={{ width: "0%" }} />
        </div>
      </div>
      <div className="risk-item">
        <div className="risk-line">
          <span className="risk-lab">{tr("日亏损")}</span>
          <span className="risk-val neg">{dailyLoss} / -$5,000</span>
        </div>
        <div className="risk-bar">
          <div className="risk-bar-fill crit" style={{ width: `${dailyLossPct}%` }} />
        </div>
      </div>
      <div className="risk-item">
        <div className="risk-line">
          <span className="risk-lab">{tr("相关性风险")}</span>
          <span className="risk-val">— / 0.80</span>
        </div>
        <div className="risk-bar">
          <div className="risk-bar-fill" style={{ width: "0%" }} />
        </div>
      </div>

      <div className="risk-toggles">
        <div className="risk-toggle-label">{tr("风控开关")}</div>
        {([
          ["autoStop", "自动止损"],
          ["hedgeOn", "自动对冲"],
          ["maxDrawdown", "回撤熔断"],
          ["dailyLimit", "日亏损限制"],
        ] as const).map(([k, name]) => (
          <div key={k} className="risk-toggle-row">
            <span className="risk-toggle-name">{tr(name)}</span>
            <div
              className={`risk-toggle-switch ${toggles[k] ? "on" : ""}`}
              aria-label={tr(name)}
              aria-checked={toggles[k]}
              {...clickable(() => toggle(k), "switch")}
            >
              <span className="switch-knob" />
            </div>
          </div>
        ))}
      </div>

      <button className="kill-switch-btn">
        <svg viewBox="0 0 14 14" fill="none">
          <path d="M4 4 L10 10 M10 4 L4 10" stroke="currentColor" strokeWidth="1.8" />
          <circle cx="7" cy="7" r="6" stroke="currentColor" strokeWidth="1" fill="none" />
        </svg>
        KILL ALL
      </button>
    </DrawerPanel>
  );
}
