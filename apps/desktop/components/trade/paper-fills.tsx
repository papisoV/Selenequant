"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { Loader2, RefreshCw, ReceiptText } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

// /api/v1/paper/fills?account_id= 返回 list[dict] — OKX fills-history 风格
interface PaperFill {
  id?: string;
  order_id?: string;
  price?: number;
  quantity?: number;
  fee?: number;
  fee_currency?: string;
  fee_rate?: number;
  realized_pnl?: number;
  exec_type?: string;
  side?: string;
  pos_side?: string;
  symbol?: string;
  leverage?: number;
  reduce_only?: boolean;
  timestamp?: number;
}

const fmt = (n?: number, d = 4) =>
  n == null || Number.isNaN(Number(n)) ? "—" : Number(n).toFixed(d);
const fmtPnL = (n?: number) =>
  n == null || Number.isNaN(Number(n)) ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;

function fmtTime(ts?: number) {
  if (ts == null) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function PaperFillsPage() {
  const tr = useUiText()

  const accountId = useAccountId();
  const { data, isLoading, mutate } = useApi<PaperFill[]>(
    `/api/v1/paper/fills?account_id=${accountId}`
  );

  const fills = data ?? [];

  const totalRealized = fills.reduce((s, f) => s + Number(f.realized_pnl ?? 0), 0);
  const totalFee = fills.reduce((s, f) => s + Number(f.fee ?? 0), 0);
  const buyVol = fills.filter((f) => f.side === "buy").reduce((s, f) => s + Number(f.quantity ?? 0), 0);
  const sellVol = fills.filter((f) => f.side === "sell").reduce((s, f) => s + Number(f.quantity ?? 0), 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("模拟成交")}
        right={<span className="flex items-center gap-1 font-mono text-ink-3"><ReceiptText className="h-3 w-3" />{accountId}</span>}
      >
        <div className="grid grid-cols-4 gap-2">
          <StatBlock label={tr("成交笔数")} value={fills.length} cls="text-gold" />
          <StatBlock label={tr("已实现 PnL")} value={`${fmtPnL(totalRealized)} USDT`} cls={totalRealized >= 0 ? "text-pos" : "text-neg"} />
          <StatBlock label={tr("累计手续费")} value={`${fmt(totalFee, 2)} USDT`} cls="text-neg" />
          <StatBlock label={tr("买入/卖出额")} value={`${fmt(buyVol, 0)} / ${fmt(sellVol, 0)}`} />
        </div>

        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={() => mutate()}
            className="flex items-center gap-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-2 hover:text-gold"
          >
            <RefreshCw className="h-3 w-3" />{tr("刷新")}
          </button>
          <span className="text-[9px] text-ink-4">{tr("最近成交(fills-history)")}</span>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-6 mt-2"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : fills.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-6 mt-2">{tr("暂无成交记录")}</div>
        ) : (
          <div className="dtable mt-2 max-h-[60vh] overflow-auto">
            <div className="grid grid-cols-[70px_1fr_44px_80px_80px_80px_80px_44px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
              <span>{tr("成交号")}</span><span>{tr("品种")}</span><span>{tr("方向")}</span><span className="text-right">{tr("价格")}</span><span className="text-right">{tr("数量")}</span><span className="text-right">{tr("手续费")}</span><span className="text-right">PnL</span><span>{tr("类型")}</span>
            </div>
            {fills.slice().reverse().map((f, i) => {
              const sideCls = f.side === "buy" ? "text-pos" : f.side === "sell" ? "text-neg" : "text-ink-3";
              const pnlCls = (f.realized_pnl ?? 0) >= 0 ? "text-pos" : "text-neg";
              return (
                <div key={f.id ?? i} className="grid grid-cols-[70px_1fr_44px_80px_80px_80px_80px_44px] gap-2 text-[10px] py-1 border-b border-border-soft items-center">
                  <span className="text-ink-4 font-mono truncate" title={f.id}>{f.id ?? "?"}</span>
                  <span className="text-gold font-mono truncate">{f.symbol ?? "?"}</span>
                  <span className={sideCls}>{f.side === "buy" ? tr("买") : f.side === "sell" ? tr("卖") : (f.side ?? "—")}</span>
                  <span className="num text-right font-mono">{fmt(f.price)}</span>
                  <span className="num text-right font-mono">{fmt(f.quantity)}</span>
                  <span className="num text-right font-mono">{fmt(f.fee, 2)}</span>
                  <span className={`num text-right font-mono ${pnlCls}`}>{fmtPnL(f.realized_pnl)}</span>
                  <span className="text-ink-3 font-mono">{f.exec_type ?? "taker"}</span>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>
    </div>
  );
}
