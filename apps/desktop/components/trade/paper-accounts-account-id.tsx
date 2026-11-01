"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { apiPost } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { Loader2, RotateCcw, RefreshCw, Wallet } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

// /api/v1/paper/accounts/{id} 返回 dict — 账户详情
interface PaperAccountDetail {
  id?: string;
  name?: string;
  account_type?: string;
  initial_balance?: number;
  current_balance?: number;
  balance?: number;
  equity?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  total_fee?: number;
  maker_fee_rate?: number;
  taker_fee_rate?: number;
  status?: string;
  created_at?: number;
}

// /api/v1/paper/accounts 返回 list — 用于账户选择
interface PaperAccountMeta {
  id?: string;
  name?: string;
}

const fmt = (n?: number, d = 2) =>
  n == null || Number.isNaN(Number(n)) ? "—" : Number(n).toFixed(d);
const fmtPct = (n?: number) =>
  n == null || Number.isNaN(Number(n)) ? "—" : `${(Number(n) * 100).toFixed(3)}%`;

export function PaperAccountDetailPage() {
  const tr = useUiText()

  const defaultId = useAccountId();
  const [accountId, setAccountId] = useState(defaultId);
  const [resetBusy, setResetBusy] = useState(false);

  const { data: accounts } = useApi<PaperAccountMeta[]>("/api/v1/paper/accounts");
  const { data: detail, isLoading, mutate } = useApi<PaperAccountDetail>(
    accountId ? `/api/v1/paper/accounts/${encodeURIComponent(accountId)}` : null
  );

  const acct = detail;

  async function resetAccount() {
    setResetBusy(true);
    try {
      await apiPost(`/api/v1/paper/accounts/${encodeURIComponent(accountId)}/reset`);
      await mutate();
    } finally {
      setResetBusy(false);
    }
  }

  const equity = Number(acct?.equity ?? 0);
  const avail = Number(acct?.balance ?? acct?.current_balance ?? 0);
  const uPnl = Number(acct?.unrealized_pnl ?? 0);
  const rPnl = Number(acct?.realized_pnl ?? 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("模拟账户详情")}
        right={
          <span className="flex items-center gap-1 font-mono text-ink-3">
            <Wallet className="h-3 w-3" />{accountId}
          </span>
        }
      >
        {/* 账户选择 */}
        <div className="mb-2 flex items-center gap-2">
          <label className="text-[10px] text-ink-3" htmlFor="acct-select">{tr("选择账户")}</label>
          <select
            id="acct-select"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
          >
            {(accounts ?? []).map((a) => (
              <option key={a.id ?? a.name} value={a.id}>{a.name ?? a.id}</option>
            ))}
          </select>
          <button
            onClick={() => mutate()}
            className="flex items-center gap-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-2 hover:text-gold"
          >
            <RefreshCw className="h-3 w-3" />{tr("刷新")}
          </button>
          <button
            onClick={resetAccount}
            disabled={resetBusy}
            className="ml-auto flex items-center gap-1 rounded border border-neg/40 bg-neg/10 px-2 py-1 text-[10px] text-neg hover:bg-neg/20 disabled:opacity-40"
          >
            {resetBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
            {tr("重置账户")}
          </button>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : !acct ? (
          <div className="text-center text-ink-4 text-[11px] py-4">{tr("账户不存在或无数据")}</div>
        ) : (
          <>
            <div className="grid grid-cols-4 gap-2">
              <StatBlock label={tr("账户净值")} value={`${fmt(equity)} USDT`} cls="text-gold" />
              <StatBlock label={tr("可用余额")} value={`${fmt(avail)} USDT`} />
              <StatBlock label={tr("浮动 PnL")} value={`${uPnl >= 0 ? "+" : ""}${fmt(uPnl)} USDT`} cls={uPnl >= 0 ? "text-pos" : "text-neg"} />
              <StatBlock label={tr("已实现 PnL")} value={`${rPnl >= 0 ? "+" : ""}${fmt(rPnl)} USDT`} cls={rPnl >= 0 ? "text-pos" : "text-neg"} />
            </div>

            <div className="mt-2 grid grid-cols-4 gap-2">
              <StatBlock label={tr("初始资金")} value={`${fmt(acct.initial_balance)} USDT`} />
              <StatBlock label={tr("累计手续费")} value={`${fmt(acct.total_fee)} USDT`} cls="text-neg" />
              <StatBlock label={tr("Maker 费率")} value={fmtPct(acct.maker_fee_rate)} />
              <StatBlock label={tr("Taker 费率")} value={fmtPct(acct.taker_fee_rate)} />
            </div>

            <div className="mt-2 flex flex-wrap gap-3 border-t border-border-soft pt-2 text-[9px] text-ink-3">
              <span>{tr("类型:")} <span className="text-ink-2 font-mono">{acct.account_type ?? "manual"}</span></span>
              <span>{tr("状态:")} <span className="text-ink-2 font-mono">{acct.status ?? "active"}</span></span>
              <span>ID: <span className="text-ink-2 font-mono">{acct.id}</span></span>
              {acct.created_at ? (
                <span>{tr("创建于:")} <span className="text-ink-2 font-mono">{new Date(acct.created_at).toLocaleString()}</span></span>
              ) : null}
            </div>
          </>
        )}
      </V17Card>
    </div>
  );
}
