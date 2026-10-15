"use client";

import React, { useMemo } from "react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { usePaperStore } from "@/lib/store/paper";
import { useShallow } from "zustand/react/shallow";
import type { PaperPositionData, PaperOrderData } from "@/lib/types/paper";

export type PositionTab = "pending" | "open" | "closed";

export interface Position {
  market: string;
  sym: string;
  side: "多" | "空" | "买" | "卖";
  qty: string;
  price?: string;
  avg?: string;
  now?: string;
  pnl?: string;
  cls?: "pos" | "neg";
  tif?: string;
  status?: string;
  time?: string;
  dur?: string;
}

type BackendPosition = PaperPositionData;
type BackendOrder = PaperOrderData;

function adaptPendingOrder(o: BackendOrder): Position {
  const qty = Number(o.quantity || 0);
  return {
    market: "futures",
    sym: o.symbol || "?",
    side: o.side === "buy" ? "买" : "卖",
    qty: String(qty),
    price: o.price != null ? String(o.price) : (o.avg_fill_price != null ? String(o.avg_fill_price) : "—"),
    tif: (o.type || "limit").toUpperCase().slice(0, 2),
    status: o.status || "accepted",
  };
}

export function adaptPosition(p: BackendPosition): Position {
  const pnl = Number(p.unrealized_pnl || 0);
  const qty = Number(p.quantity_base || p.quantity || 0);
  return {
    market: "futures",
    sym: p.symbol || "?",
    side: p.side === "buy" ? "多" : "空",
    qty: String(qty),
    avg: String(p.avg_entry_price ?? 0),
    now: String(p.current_price ?? 0),
    pnl: (pnl >= 0 ? "+" : "") + pnl.toFixed(1),
    cls: pnl >= 0 ? "pos" : "neg",
  };
}

const MK_LABEL: Record<string, string> = {
  futures: "合约",
  spot: "现货",
  stock_a: "A股",
  stock_us: "美股",
};

interface PositionTableEmbeddedProps {
  tab: PositionTab;
  onTabChange: (t: PositionTab) => void;
  showStats?: boolean;
}

export function PositionTableEmbedded({ tab, onTabChange, showStats = true }: PositionTableEmbeddedProps) {
  const positions = usePaperStore(useShallow((s) => s.positions));
  const orders = usePaperStore(useShallow((s) => s.orders));

  const realPositions = positions as BackendPosition[];
  const realPending: PaperOrderData[] = useMemo(
    () => orders.filter((o) => o.status && ["working", "pending", "new", "partial", "active", "accepted"].includes(o.status)),
    [orders],
  );

  const OPEN = useMemo<Position[]>(() => {
    if (!realPositions || realPositions.length === 0) return [];
    return realPositions.map(adaptPosition);
  }, [realPositions]);
  const PENDING = useMemo<Position[]>(() => {
    if (!realPending || realPending.length === 0) return [];
    return (realPending as BackendOrder[]).map(adaptPendingOrder);
  }, [realPending]);

  const rows = tab === "pending" ? PENDING : tab === "open" ? OPEN : [];
  const totalPnl = OPEN.reduce((s, r) => s + parseFloat((r.pnl || "0").replace("+", "")), 0);

  return (
    <V17Card title="我的持仓">
      <div className="flex items-center gap-2 mb-2 text-[10px]">
        <div className="flex gap-1">
          {(["pending", "open", "closed"] as PositionTab[]).map((t) => (
            <button
              key={t}
              onClick={() => onTabChange(t)}
              className={`pos-tab rounded px-2 py-0.5 ${tab === t ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
            >
              {t === "pending" ? "挂单" : t === "open" ? "当前持仓" : "历史平仓"}
            </button>
          ))}
        </div>
      </div>
      {tab === "open" && showStats && (
        <div className="grid grid-cols-4 gap-2 mb-2">
          <StatBlock label="总 PnL" value={`${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(1)} USDT`} cls={totalPnl >= 0 ? "text-pos" : "text-neg"} />
          <StatBlock label="净敞口" value={OPEN.length === 0 ? "—" : `${OPEN.length} 笔`} cls="text-gold" />
          <StatBlock label="风险阈值" value="—" />
          <StatBlock label="SL-TP 设置" value="—" />
        </div>
      )}
      <div className="dtable">
        {tab === "pending" && (
          <div className="grid grid-cols-[48px_1fr_40px_60px_80px_40px_70px_96px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>市场</span><span>品种</span><span>方向</span><span className="text-right">数量</span><span className="text-right">价格</span><span>TIF</span><span>状态</span><span>操作</span>
          </div>
        )}
        {tab === "open" && (
          <div className="grid grid-cols-[48px_1fr_40px_60px_70px_70px_70px_56px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>市场</span><span>品种</span><span>方向</span><span className="text-right">数量</span><span className="text-right">均价</span><span className="text-right">现价</span><span className="text-right">PnL</span><span>操作</span>
          </div>
        )}
        {tab === "closed" && (
          <div className="grid grid-cols-[70px_48px_1fr_40px_60px_70px_70px_50px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>时间</span><span>市场</span><span>品种</span><span>方向</span><span className="text-right">数量</span><span className="text-right">平仓价</span><span className="text-right">PnL</span><span>时长</span>
          </div>
        )}
        {rows.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">
            {tab === "pending" ? "该市场暂无挂单" : tab === "open" ? "该市场暂无持仓" : "该市场暂无历史"}
          </div>
        ) : (
          rows.map((r, i) => {
            const dCls = r.cls === "pos" ? "text-pos" : r.cls === "neg" ? "text-neg" : "text-ink-2";
            if (tab === "pending") {
              return (
                <div key={i} className="grid grid-cols-[48px_1fr_40px_60px_80px_40px_70px_96px] gap-2 text-[10px] py-1 border-b border-border-soft items-center">
                  <span className="mk-tag bg-surface-3 rounded px-1 py-0.5 text-ink-3 text-[9px]">{MK_LABEL[r.market]}</span>
                  <span className="text-gold font-mono">{r.sym}</span>
                  <span className={dCls}>{r.side}</span>
                  <span className="num text-right font-mono">{r.qty}</span>
                  <span className="num text-right font-mono">{r.price}</span>
                  <span className="text-ink-4 font-mono">{r.tif}</span>
                  <span className="text-gold font-mono">{r.status}</span>
                  <span className="flex gap-1">
                    <button className="row-btn rounded border border-border-soft px-1.5 py-0.5 text-[9px] text-gold">改单</button>
                    <button className="row-btn danger rounded border border-destructive/40 px-1.5 py-0.5 text-[9px] text-destructive">撤单</button>
                  </span>
                </div>
              );
            }
            if (tab === "open") {
              return (
                <div key={i} className="grid grid-cols-[48px_1fr_40px_60px_70px_70px_70px_56px] gap-2 text-[10px] py-1 border-b border-border-soft items-center">
                  <span className="mk-tag bg-surface-3 rounded px-1 py-0.5 text-ink-3 text-[9px]">{MK_LABEL[r.market]}</span>
                  <span className="text-gold font-mono">{r.sym}</span>
                  <span className={dCls}>{r.side}</span>
                  <span className="num text-right font-mono">{r.qty}</span>
                  <span className="num text-right font-mono">{r.avg}</span>
                  <span className="num text-right font-mono">{r.now}</span>
                  <span className={`num text-right font-mono ${dCls}`}>{r.pnl}</span>
                  <button className="row-btn danger rounded border border-destructive/40 px-1.5 py-0.5 text-[9px] text-destructive">平仓</button>
                </div>
              );
            }
            return (
              <div key={i} className="grid grid-cols-[70px_48px_1fr_40px_60px_70px_70px_50px] gap-2 text-[10px] py-1 border-b border-border-soft items-center">
                <span className="text-ink-4 font-mono">{r.time}</span>
                <span className="mk-tag bg-surface-3 rounded px-1 py-0.5 text-ink-3 text-[9px]">{MK_LABEL[r.market]}</span>
                <span className="text-gold font-mono">{r.sym}</span>
                <span className={dCls}>{r.side}</span>
                <span className="num text-right font-mono">{r.qty}</span>
                <span className="num text-right font-mono">{r.price}</span>
                <span className={`num text-right font-mono ${dCls}`}>{r.pnl}</span>
                <span className="text-ink-4 font-mono">{r.dur}</span>
              </div>
            );
          })
        )}
      </div>
    </V17Card>
  );
}
