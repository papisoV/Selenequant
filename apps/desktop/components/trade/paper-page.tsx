"use client";

import { useState } from "react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { WorkflowContextBanner } from "@/components/layout/workflow-context-banner";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { getEquityHistory, type EquityPoint } from "@/lib/api/paper";
import { PaperSettingsPage } from "@/components/trade/paper-settings";
import { Loader2, TrendingUp, LayoutGrid, Wallet, Settings } from "lucide-react";
import { useRouter } from "next/navigation";
import { useUiText } from "@/components/i18n/use-ui-text"

interface PaperPosition {
  sym: string;
  side: "多" | "空";
  qty: string;
  avg: string;
  now: string;
  pnl: string;
}
interface PaperOrder {
  id: string;
  sym: string;
  side: "买" | "卖";
  qty: string;
  price: string;
  status: string;
  time: string;
  pnl: string;
}

// 后端 paper/positions 返回 list[dict] — 字段: {symbol, side, qty, avg_price, current_price, unrealized_pnl}
interface BackendPaperPos {
  symbol?: string;
  side?: string;
  qty?: number;
  avg_price?: number;
  current_price?: number;
  unrealized_pnl?: number;
}
interface BackendPaperOrder {
  order_id?: string;
  id?: string;
  symbol?: string;
  side?: string;
  qty?: number;
  price?: number;
  status?: string;
  created_at?: string;
  pnl?: number;
}

// /api/v1/paper/accounts 返回 list[dict] — {id, name, balance, equity, unrealized_pnl, realized_pnl, ...}
interface PaperAccount {
  id?: string;
  name?: string;
  balance?: number;
  equity?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
}

// 6 个子页导航入口(设计稿 L1919 paper 模拟交易 → 各数据子页)
// K37/K38(判定表 r5):权益历史=Dialog(查看权益曲线按钮)、设置=Dialog(设置按钮),不再跳独立 route
const PAPER_SUBPAGES: { slug: string; label: string }[] = [
  { slug: "paper-accounts", label: "账户" },
  { slug: "paper-positions", label: "持仓" },
  { slug: "paper-orders", label: "订单" },
  { slug: "paper-fills", label: "成交" },
  { slug: "paper-pnl", label: "盈亏" },
  { slug: "paper-accounts-account-id", label: "账户详情" },
];

// 设计稿 paper 模拟交易 — paper 表 + PnL 统计
export function PaperPage() {
  const tr = useUiText()

  const accountId = useAccountId();
  const router = useRouter();
  const { data: accounts } = useApi<PaperAccount[]>("/api/v1/paper/accounts");
  const { data: realPos } = useApi<BackendPaperPos[]>(
    `/api/v1/paper/positions?account_id=${accountId}`
  );
  const { data: realOrders } = useApi<BackendPaperOrder[]>(
    `/api/v1/paper/orders?account_id=${accountId}`
  );

  // 当前账户 = 列表里匹配 accountId 的账户(默认取第一个)
  const currentAccount = (accounts && accounts.length > 0)
    ? accounts.find((a) => a.id === accountId) ?? accounts[0]
    : null;

  const openPositions: PaperPosition[] = (realPos && realPos.length > 0)
    ? realPos.map((p) => {
        const pnl = Number(p.unrealized_pnl || 0);
        const qty = Number(p.qty || 0);
        return {
          sym: p.symbol || "?",
          side: p.side === "buy" ? "多" : "空",
          qty: String(qty),
          avg: String(p.avg_price ?? 0),
          now: String(p.current_price ?? 0),
          pnl: (pnl >= 0 ? "+" : "") + pnl.toFixed(1),
        };
      })
    : [];

  const orders: PaperOrder[] = (realOrders && realOrders.length > 0)
    ? realOrders.map((o) => {
        const pnl = Number(o.pnl ?? 0);
        return {
          id: String(o.order_id ?? o.id ?? "?"),
          sym: o.symbol || "?",
          side: o.side === "buy" ? "买" : "卖",
          qty: String(o.qty ?? 0),
          price: String(o.price ?? 0),
          status: o.status || "已成交",
          time: o.created_at?.slice(11, 19) ?? "—",
          pnl: (pnl >= 0 ? "+" : "") + pnl.toFixed(1),
        };
      })
    : [];

  const equity = currentAccount?.equity;
  const avail = currentAccount?.balance;
  const hasAccount = !!currentAccount;
  const unrealizedPnl = currentAccount?.unrealized_pnl ?? 0;
  const realizedPnl = currentAccount?.realized_pnl ?? 0;

  const [equityOpen, setEquityOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [equityLoading, setEquityLoading] = useState(false);
  const [equityData, setEquityData] = useState<EquityPoint[]>([]);

  async function openEquityChart() {
    setEquityOpen(true);
    setEquityLoading(true);
    try {
      const data = await getEquityHistory(accountId, 200);
      setEquityData(data);
    } catch {
      setEquityData([]);
    } finally {
      setEquityLoading(false);
    }
  }

  // 简易 SVG 折线图
  const W = 600, H = 200, P = 20;
  const points = equityData.map((p) => Number(p.equity ?? 0)).filter((n) => Number.isFinite(n));
  const min = points.length > 0 ? Math.min(...points) : 0;
  const max = points.length > 0 ? Math.max(...points) : 1;
  const range = max - min || 1;
  const pathD = points.length > 0
    ? points.map((v, i) => {
        const x = P + (i / Math.max(points.length - 1, 1)) * (W - 2 * P);
        const y = H - P - ((v - min) / range) * (H - 2 * P);
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ")
    : "";

  const goTo = (slug: string) => router.push(`/trade/${slug}`);

  return (
    <div className="v17-page-grid grid gap-2">
      <WorkflowContextBanner />
      {/* 子页导航带 */}
      <div className="flex flex-wrap items-center gap-1.5 rounded border border-border-soft bg-surface p-1.5">
        <span className="flex items-center gap-1 px-1 text-[10px] font-medium text-ink-3">
          <LayoutGrid className="h-3 w-3 text-gold" />{tr("模拟交易子页")}
        </span>
        {PAPER_SUBPAGES.map((p) => (
          <button
            key={p.slug}
            onClick={() => goTo(p.slug)}
            className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-2 hover:border-gold/50 hover:text-gold transition-colors"
          >
            {tr(p.label)}
          </button>
        ))}
      </div>

      <V17Card
        title={tr("模拟账户")}
        right={
          <span className="flex items-center gap-1 font-mono text-ink-3">
            <Wallet className="h-3 w-3" />{accountId}
          </span>
        }
      >
        <div className="grid grid-cols-4 gap-2">
          <StatBlock label={tr("账户净值")} value={hasAccount && equity != null ? `${equity.toFixed(2)} USDT` : "—"} cls="text-gold" />
          <StatBlock label={tr("可用保证金")} value={hasAccount && avail != null ? `${avail.toFixed(2)} USDT` : "—"} />
          <StatBlock label={tr("浮动 PnL")} value={hasAccount ? `${unrealizedPnl >= 0 ? "+" : ""}${unrealizedPnl.toFixed(1)} USDT` : "—"} cls={unrealizedPnl >= 0 ? "text-pos" : "text-neg"} />
          <StatBlock label={tr("已实现 PnL")} value={hasAccount ? `${realizedPnl >= 0 ? "+" : ""}${realizedPnl.toFixed(1)} USDT` : "—"} cls={realizedPnl >= 0 ? "text-pos" : "text-neg"} />
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={openEquityChart}
            className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-gold hover:bg-gold/10 flex items-center gap-1"
          >
            <TrendingUp className="h-3 w-3" />{tr("查看权益曲线")}
          </button>
          <button
            onClick={() => setSettingsOpen(true)}
            className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-2 hover:text-gold hover:border-gold/50 flex items-center gap-1 transition-colors"
          >
            <Settings className="h-3 w-3" />{tr("设置")}
          </button>
        </div>
      </V17Card>

      <V17Card title={tr("当前模拟持仓")} right={<button onClick={() => goTo("paper-positions")} className="text-[9px] text-gold hover:underline">{tr("查看全部 →")}</button>}>
        <div className="dtable">
          <div className="grid grid-cols-[1fr_40px_60px_70px_70px_70px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>{tr("品种")}</span><span>{tr("方向")}</span><span className="text-right">{tr("数量")}</span><span className="text-right">{tr("均价")}</span><span className="text-right">{tr("现价")}</span><span className="text-right">PnL</span>
          </div>
          {openPositions.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无模拟持仓")}</div>
          ) : openPositions.map((r, i) => {
            const dCls = r.side === "多" ? "text-pos" : "text-neg";
            const pnlCls = r.pnl.startsWith("+") ? "text-pos" : r.pnl.startsWith("-") ? "text-neg" : "text-ink-3";
            return (
              <div
                key={i}
                className="grid grid-cols-[1fr_40px_60px_70px_70px_70px] gap-2 text-[10px] py-1 border-b border-border-soft items-center"
              >
                <span className="text-gold font-mono">{r.sym}</span>
                <span className={dCls}>{tr(r.side)}</span>
                <span className="num text-right font-mono">{r.qty}</span>
                <span className="num text-right font-mono">{r.avg}</span>
                <span className="num text-right font-mono">{r.now}</span>
                <span className={`num text-right font-mono ${pnlCls}`}>{r.pnl}</span>
              </div>
            );
          })}
        </div>
      </V17Card>

      <V17Card title={tr("历史订单")} right={<button onClick={() => goTo("paper-orders")} className="text-[9px] text-gold hover:underline">{tr("查看全部 →")}</button>}>
        <div className="dtable">
          <div className="grid grid-cols-[70px_1fr_40px_60px_70px_70px_60px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>{tr("订单号")}</span><span>{tr("品种")}</span><span>{tr("方向")}</span><span className="text-right">{tr("数量")}</span><span className="text-right">{tr("价格")}</span><span className="text-right">PnL</span><span>{tr("状态")}</span>
          </div>
          {orders.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无历史订单")}</div>
          ) : orders.map((r, i) => {
            const dCls = r.side === "买" ? "text-pos" : "text-neg";
            const pnlCls = r.pnl.startsWith("+") && r.pnl !== "+0" ? "text-pos" : r.pnl.startsWith("-") ? "text-neg" : "text-ink-3";
            return (
              <div
                key={i}
                className="grid grid-cols-[70px_1fr_40px_60px_70px_70px_60px] gap-2 text-[10px] py-1 border-b border-border-soft items-center"
              >
                <span className="text-ink-4 font-mono">{r.id}</span>
                <span className="text-gold font-mono">{r.sym}</span>
                <span className={dCls}>{tr(r.side)}</span>
                <span className="num text-right font-mono">{r.qty}</span>
                <span className="num text-right font-mono">{r.price}</span>
                <span className={`num text-right font-mono ${pnlCls}`}>{r.pnl}</span>
                <span className="text-pos font-mono">{tr(r.status)}</span>
              </div>
            );
          })}
        </div>
      </V17Card>

      <Dialog open={equityOpen} onOpenChange={setEquityOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-gold" />
              {tr("权益曲线 —")} {accountId}
            </DialogTitle>
            <DialogDescription>
              {tr("最近")} {equityData.length} {tr("个权益快照点")}
            </DialogDescription>
          </DialogHeader>
          {equityLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : points.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground text-sm">{tr("暂无权益历史数据")}</div>
          ) : (
            <div className="space-y-2">
              <div className="text-xs text-muted-foreground flex justify-between">
                <span>{tr("低:")} {min.toFixed(2)}</span>
                <span>{tr("高:")} {max.toFixed(2)}</span>
              </div>
              <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto border border-border-soft rounded">
                <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="var(--color-border-soft)" strokeWidth="0.5" />
                <line x1={P} y1={P} x2={P} y2={H - P} stroke="var(--color-border-soft)" strokeWidth="0.5" />
                <path d={pathD} fill="none" stroke="var(--color-gold)" strokeWidth="1.5" />
              </svg>
            </div>
          )}
        </DialogContent>
      </Dialog>
      {/* K38(判定表 r5):paper-settings 从 route 收为 Dialog */}
      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{tr("模拟交易设置")}</DialogTitle>
            <DialogDescription>{tr("账户")} {accountId} {tr("的手续费率配置")}</DialogDescription>
          </DialogHeader>
          <PaperSettingsPage />
        </DialogContent>
      </Dialog>
    </div>
  );
}
