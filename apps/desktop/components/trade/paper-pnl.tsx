"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { Loader2, RefreshCw, TrendingDown, TrendingUp } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

// /api/v1/paper/pnl?account_id= 返回 list[dict] — {symbol, realized_pnl, unrealized_pnl, total_pnl, fees, timestamp}
interface PnlRecord {
  symbol?: string;
  realized_pnl?: number;
  unrealized_pnl?: number;
  total_pnl?: number;
  fees?: number;
  timestamp?: number;
}

const fmt = (n?: number, d = 2) =>
  n == null || Number.isNaN(Number(n)) ? "—" : Number(n).toFixed(d);
const pnlCls = (n: number) => (n >= 0 ? "text-pos" : "text-neg");

export function PaperPnlPage() {
  const tr = useUiText()

  const accountId = useAccountId();
  const { data, isLoading, mutate } = useApi<PnlRecord[]>(
    `/api/v1/paper/pnl?account_id=${accountId}`
  );

  const records = data ?? [];

  const totalRealized = records.reduce((s, r) => s + Number(r.realized_pnl ?? 0), 0);
  const totalUnrealized = records.reduce((s, r) => s + Number(r.unrealized_pnl ?? 0), 0);
  const totalFees = records.reduce((s, r) => s + Number(r.fees ?? 0), 0);
  const total = totalRealized + totalUnrealized;
  const winners = records.filter((r) => Number(r.total_pnl ?? 0) > 0).length;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("模拟盈亏")}
        right={<button onClick={() => mutate()} className="flex items-center gap-1 text-[9px] text-gold hover:underline"><RefreshCw className="h-3 w-3" />{tr("刷新")}</button>}
      >
        <div className="grid grid-cols-4 gap-2">
          <StatBlock label={tr("总 PnL")} value={`${total >= 0 ? "+" : ""}${fmt(total)} USDT`} cls={pnlCls(total)} />
          <StatBlock label={tr("已实现")} value={`${totalRealized >= 0 ? "+" : ""}${fmt(totalRealized)} USDT`} cls={pnlCls(totalRealized)} />
          <StatBlock label={tr("浮动")} value={`${totalUnrealized >= 0 ? "+" : ""}${fmt(totalUnrealized)} USDT`} cls={pnlCls(totalUnrealized)} />
          <StatBlock label={tr("盈利品种")} value={`${winners} / ${records.length}`} cls={records.length > 0 && winners === records.length ? "text-pos" : "text-ink"} />
        </div>

        <div className="mt-1 text-[9px] text-ink-4">{tr("账户:")} <span className="font-mono text-ink-3">{accountId}</span></div>

        {isLoading ? (
          <div className="flex items-center justify-center py-6 mt-2"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : records.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-6 mt-2">{tr("暂无盈亏数据(无持仓)")}</div>
        ) : (
          <div className="dtable mt-2">
            <div className="grid grid-cols-[1fr_100px_100px_100px_80px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
              <span>{tr("品种")}</span><span className="text-right">{tr("已实现")}</span><span className="text-right">{tr("浮动")}</span><span className="text-right">{tr("总 PnL")}</span><span className="text-right">{tr("手续费")}</span>
            </div>
            {records.map((r, i) => {
              const rp = Number(r.realized_pnl ?? 0);
              const up = Number(r.unrealized_pnl ?? 0);
              const tp = Number(r.total_pnl ?? 0);
              const isWin = tp > 0;
              return (
                <div key={r.symbol ?? i} className="grid grid-cols-[1fr_100px_100px_100px_80px] gap-2 text-[10px] py-1 border-b border-border-soft items-center">
                  <span className="flex items-center gap-1.5">
                    {isWin ? <TrendingUp className="h-3 w-3 text-pos" /> : <TrendingDown className="h-3 w-3 text-neg" />}
                    <span className="text-gold font-mono">{r.symbol ?? "?"}</span>
                  </span>
                  <span className={`num text-right font-mono ${pnlCls(rp)}`}>{rp >= 0 ? "+" : ""}{fmt(rp)}</span>
                  <span className={`num text-right font-mono ${pnlCls(up)}`}>{up >= 0 ? "+" : ""}{fmt(up)}</span>
                  <span className={`num text-right font-mono ${pnlCls(tp)}`}>{tp >= 0 ? "+" : ""}{fmt(tp)}</span>
                  <span className="num text-right font-mono text-ink-3">{fmt(r.fees)}</span>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>
    </div>
  );
}
