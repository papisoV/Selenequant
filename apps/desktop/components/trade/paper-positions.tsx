"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { apiPost } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { Loader2, RefreshCw, X } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

// /api/v1/paper/positions?account_id= 返回 list[dict] — OKX position 契约
interface PaperPosition {
  symbol?: string;
  side?: string;
  pos_side?: string;
  quantity?: number;
  quantity_base?: number;
  avg_entry_price?: number;
  leverage?: number;
  margin_mode?: string;
  margin?: number;
  liquidation_price?: number | null;
  unrealized_pnl?: number;
  unrealized_pnl_pct?: number;
  realized_pnl?: number;
  current_price?: number | null;
  opened_at?: number;
}

const fmt = (n?: number | null, d = 4) =>
  n == null || Number.isNaN(Number(n)) ? "—" : Number(n).toFixed(d);
const fmtPnL = (n?: number) =>
  n == null || Number.isNaN(Number(n)) ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;
const pnlCls = (n: number) => (n >= 0 ? "text-pos" : "text-neg");

export function PaperPositionsPage() {
  const tr = useUiText()

  const accountId = useAccountId();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const { data, isLoading, mutate } = useApi<PaperPosition[]>(
    `/api/v1/paper/positions?account_id=${accountId}`
  );

  const positions = data ?? [];

  async function closePosition(p: PaperPosition, qty?: number) {
    const key = `${p.symbol}|${p.pos_side ?? p.side}`;
    setBusyKey(key);
    try {
      await apiPost("/api/v1/paper/close-position", {
        account_id: accountId,
        symbol: p.symbol,
        pos_side: p.pos_side ?? "net",
        quantity: qty,
      });
      await mutate();
    } finally {
      setBusyKey(null);
    }
  }

  const totalUnrealized = positions.reduce((s, p) => s + Number(p.unrealized_pnl ?? 0), 0);
  const longCount = positions.filter((p) => (p.pos_side ?? p.side) === "long" || p.side === "buy").length;
  const shortCount = positions.length - longCount;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("模拟持仓")}
        right={<button onClick={() => mutate()} className="flex items-center gap-1 text-[9px] text-gold hover:underline"><RefreshCw className="h-3 w-3" />{tr("刷新")}</button>}
      >
        <div className="grid grid-cols-4 gap-2">
          <StatBlock label={tr("持仓数")} value={positions.length} cls="text-gold" />
          <StatBlock label={tr("浮动 PnL")} value={`${fmtPnL(totalUnrealized)} USDT`} cls={pnlCls(totalUnrealized)} />
          <StatBlock label={tr("多单")} value={longCount} cls="text-pos" />
          <StatBlock label={tr("空单")} value={shortCount} cls="text-neg" />
        </div>

        <div className="mt-1 text-[9px] text-ink-4">{tr("账户:")} <span className="font-mono text-ink-3">{accountId}</span></div>

        {isLoading ? (
          <div className="flex items-center justify-center py-6 mt-2"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : positions.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-6 mt-2">{tr("暂无持仓")}</div>
        ) : (
          <div className="dtable mt-2 max-h-[60vh] overflow-auto">
            <div className="grid grid-cols-[1fr_44px_80px_80px_60px_80px_90px_90px_50px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
              <span>{tr("品种")}</span><span>{tr("方向")}</span><span className="text-right">{tr("数量")}</span><span className="text-right">{tr("均价")}</span><span className="text-right">{tr("杠杆")}</span><span className="text-right">{tr("现价")}</span><span className="text-right">{tr("浮动")}</span><span className="text-right">{tr("强平价")}</span><span></span>
            </div>
            {positions.map((p, i) => {
              const isLong = (p.pos_side ?? p.side) === "long" || p.side === "buy";
              const sideCls = isLong ? "text-pos" : "text-neg";
              const up = Number(p.unrealized_pnl ?? 0);
              const key = `${p.symbol}|${p.pos_side ?? p.side}`;
              return (
                <div key={key ?? i} className="grid grid-cols-[1fr_44px_80px_80px_60px_80px_90px_90px_50px] gap-2 text-[10px] py-1 border-b border-border-soft items-center">
                  <span className="text-gold font-mono truncate">{p.symbol ?? "?"}</span>
                  <span className={sideCls}>{isLong ? tr("多") : tr("空")}</span>
                  <span className="num text-right font-mono">{fmt(p.quantity)}</span>
                  <span className="num text-right font-mono">{fmt(p.avg_entry_price)}</span>
                  <span className="num text-right font-mono">{p.leverage ?? 1}×</span>
                  <span className="num text-right font-mono">{fmt(p.current_price)}</span>
                  <span className={`num text-right font-mono ${pnlCls(up)}`}>{up >= 0 ? "+" : ""}{fmt(up, 2)}</span>
                  <span className="num text-right font-mono text-ink-3">{fmt(p.liquidation_price)}</span>
                  <span>
                    <button
                      onClick={() => closePosition(p)}
                      disabled={busyKey === key}
                      title={tr("平仓")}
                      aria-label={tr("平仓 {symbol}", { symbol: p.symbol ?? "" })}
                      className="flex h-5 w-5 items-center justify-center rounded border border-gold/40 bg-gold/10 text-gold hover:bg-gold/20 disabled:opacity-40"
                    >
                      {busyKey === key ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
                    </button>
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>
    </div>
  );
}
