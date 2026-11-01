"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { apiPost, apiDelete } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { Loader2, Plus, Trash2, RefreshCw } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

// /api/v1/paper/accounts 返回 list[dict] — {id, name, balance, equity, unrealized_pnl, realized_pnl, ...}
interface PaperAccount {
  id?: string;
  name?: string;
  account_type?: string;
  initial_balance?: number;
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

const fmt = (n?: number, d = 2) =>
  n == null || Number.isNaN(Number(n)) ? "—" : Number(n).toFixed(d);

export function PaperAccountsPage() {
  const tr = useUiText()

  const accountId = useAccountId();
  const { data, isLoading, mutate } = useApi<PaperAccount[]>("/api/v1/paper/accounts");

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [initBal, setInitBal] = useState("10000");
  const [busyId, setBusyId] = useState<string | null>(null);

  const accounts = data ?? [];

  async function createAccount() {
    if (!name.trim()) return;
    setCreating(true);
    try {
      await apiPost("/api/v1/paper/accounts", {
        name: name.trim(),
        initial_balance: Number(initBal) || 10000,
      });
      setName("");
      await mutate();
    } finally {
      setCreating(false);
    }
  }

  async function deleteAccount(id: string) {
    setBusyId(id);
    try {
      await apiDelete(`/api/v1/paper/accounts/${encodeURIComponent(id)}`);
      await mutate();
    } finally {
      setBusyId(null);
    }
  }

  const totalEquity = accounts.reduce((s, a) => s + Number(a.equity ?? 0), 0);
  const totalRealized = accounts.reduce((s, a) => s + Number(a.realized_pnl ?? 0), 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("模拟账户")}
        right={<button onClick={() => mutate()} className="flex items-center gap-1 text-[9px] text-gold hover:underline"><RefreshCw className="h-3 w-3" />{tr("刷新")}</button>}
      >
        <div className="grid grid-cols-3 gap-2">
          <StatBlock label={tr("账户数")} value={accounts.length} cls="text-gold" />
          <StatBlock label={tr("总净值")} value={`${fmt(totalEquity)} USDT`} />
          <StatBlock label={tr("总已实现 PnL")} value={`${totalRealized >= 0 ? "+" : ""}${fmt(totalRealized)} USDT`} cls={totalRealized >= 0 ? "text-pos" : "text-neg"} />
        </div>

        {/* 创建账户 */}
        <div className="mt-2 flex items-center gap-1.5 rounded border border-border-soft bg-surface-2 p-1.5">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={tr("账户名")}
            aria-label={tr("账户名")}
            className="min-w-0 flex-1 rounded border border-border-soft bg-bg px-2 py-1 text-[10px] text-ink outline-none focus:border-gold/50"
          />
          <input
            value={initBal}
            onChange={(e) => setInitBal(e.target.value)}
            placeholder={tr("初始资金")}
            aria-label={tr("初始资金")}
            type="number"
            className="w-24 rounded border border-border-soft bg-bg px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
          />
          <button
            onClick={createAccount}
            disabled={creating || !name.trim()}
            className="flex items-center gap-1 rounded border border-gold/40 bg-gold/10 px-2 py-1 text-[10px] text-gold hover:bg-gold/20 disabled:opacity-40"
          >
            {creating ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
            {tr("创建账户")}
          </button>
        </div>

        {/* 账户列表 */}
        {isLoading ? (
          <div className="flex items-center justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : accounts.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-4">{tr("暂无模拟账户")}</div>
        ) : (
          <div className="dtable mt-2">
            <div className="grid grid-cols-[1fr_50px_90px_90px_90px_90px_40px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
              <span>{tr("名称")}</span><span>{tr("类型")}</span><span className="text-right">{tr("净值")}</span><span className="text-right">{tr("可用")}</span><span className="text-right">{tr("浮动")}</span><span className="text-right">{tr("已实现")}</span><span></span>
            </div>
            {accounts.map((a) => {
              const id = a.id ?? "";
              const isActive = id === accountId;
              const uPnl = Number(a.unrealized_pnl ?? 0);
              const rPnl = Number(a.realized_pnl ?? 0);
              return (
                <div key={id} className={`grid grid-cols-[1fr_50px_90px_90px_90px_90px_40px] gap-2 text-[10px] py-1 border-b border-border-soft items-center ${isActive ? "bg-gold/5" : ""}`}>
                  <span className="flex items-center gap-1.5">
                    <span className="text-gold font-mono">{a.name ?? id}</span>
                    {isActive && <span className="rounded bg-gold/15 px-1 text-[8px] text-gold">{tr("当前")}</span>}
                  </span>
                  <span className="text-ink-3">{a.account_type ?? "manual"}</span>
                  <span className="num text-right font-mono text-gold">{fmt(a.equity)}</span>
                  <span className="num text-right font-mono">{fmt(a.balance)}</span>
                  <span className={`num text-right font-mono ${uPnl >= 0 ? "text-pos" : "text-neg"}`}>{uPnl >= 0 ? "+" : ""}{fmt(uPnl)}</span>
                  <span className={`num text-right font-mono ${rPnl >= 0 ? "text-pos" : "text-neg"}`}>{rPnl >= 0 ? "+" : ""}{fmt(rPnl)}</span>
                  <button
                    onClick={() => deleteAccount(id)}
                    disabled={busyId === id}
                    title={tr("删除账户")}
                    aria-label={tr("删除 {p0}", { p0: a.name ?? id })}
                    className="flex h-5 w-5 items-center justify-center rounded border border-border-soft text-ink-4 hover:border-neg/50 hover:text-neg disabled:opacity-40"
                  >
                    {busyId === id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>
    </div>
  );
}
