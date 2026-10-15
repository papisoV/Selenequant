"use client";

import { DrawerPanel } from "./drawer-panel";
import { useApi } from "@/lib/hooks/use-api";

interface Props {
  show: boolean;
  onClose: () => void;
}

// 后端 /api/v1/portfolio 只返回 7 个字段(portfolio.py:33):
//   deployment_id / symbol / side / size / entry_price / current_price / unrealized_pnl
// 其余字段(equity_history / week_pnl / margin_ratio / market_breakdown /
// day_pnl / leverage)后端从不发送,已从类型与渲染中删除。
interface PortfolioItem {
  symbol?: string;
  side?: string;
  size?: number;
  entry_price?: number;
  current_price?: number;
  unrealized_pnl?: number;
}

const DASH = "—";

function fmtMoney(v: number): string {
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtSignedMoney(v: number): string {
  return `${v >= 0 ? "+" : "-"}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function AssetDrawer({ show, onClose }: Props) {
  const { data: portfolio } = useApi<PortfolioItem[] | PortfolioItem | null>(
    "/api/v1/portfolio"
  );

  const items: PortfolioItem[] = Array.isArray(portfolio)
    ? portfolio
    : portfolio
      ? [portfolio]
      : [];

  const agg = items.length > 0
    ? (() => {
        let totalVal = 0;
        let totalPnl = 0;
        items.forEach((p) => {
          const size = Math.abs(Number(p.size ?? 0));
          const px = Number(p.current_price ?? p.entry_price ?? 0);
          totalVal += size * px;
          totalPnl += Number(p.unrealized_pnl ?? 0);
        });
        return {
          netValue: totalVal > 0 ? fmtMoney(totalVal) : DASH,
          upnl: fmtSignedMoney(totalPnl),
          upnlPct: totalVal > 0 ? fmtPct((totalPnl / totalVal) * 100) : DASH,
          positionCount: String(items.length),
        };
      })()
    : null;

  const netValue = agg?.netValue ?? DASH;
  const upnl = agg?.upnl ?? DASH;
  const upnlPct = agg?.upnlPct ?? DASH;
  const positionCount = agg?.positionCount ?? "0";

  const isPos = (s: string) => s.startsWith("+");
  const isNeg = (s: string) => s.startsWith("-");

  return (
    <DrawerPanel show={show} title="资产总览 · ASSET" onClose={onClose}>
      <div className="dp-net-section">
        <div className="dp-net-label">持仓市值</div>
        <div className="dp-net-value">{netValue}</div>
        <div className="dp-pnl-row">
          <span className={`dp-pnl-abs ${isPos(upnl) ? "pos" : isNeg(upnl) ? "neg" : ""}`}>{upnl}</span>
          <span className={`dp-pnl-pct ${isPos(upnlPct) ? "pos" : isNeg(upnlPct) ? "neg" : ""}`}>{upnlPct}</span>
        </div>
      </div>

      <div className="dp-metrics-section">
        <div className="dp-metrics-label">核心指标</div>
        <div className="dp-metrics-grid">
          <div className="dp-metric-card">
            <div className="dp-metric-lab">未实现盈亏</div>
            <div className={`dp-metric-val ${isPos(upnl) ? "pos" : isNeg(upnl) ? "neg" : ""}`}>{upnl}</div>
          </div>
          <div className="dp-metric-card">
            <div className="dp-metric-lab">持仓数</div>
            <div className="dp-metric-val gold">{positionCount}</div>
          </div>
        </div>
      </div>

      <div className="dp-accounts-section">
        <div className="dp-accounts-label">账户切换</div>
        <div className="dp-acct-btns">
          <span className="dp-acct-btn binance active">B</span>
          <span className="dp-acct-btn okx">O</span>
          <span className="dp-acct-btn alpaca">A</span>
          <span className="dp-acct-btn qmt">Q</span>
        </div>
      </div>
    </DrawerPanel>
  );
}
