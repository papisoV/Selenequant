"use client";

import { useEffect } from "react";
import { useTradingStore } from "@/lib/store/trading";
import { useApi } from "@/lib/hooks/use-api";
import { useAccountId } from "@/lib/hooks/use-account-id";

// 真实数据：从 /api/v1/paper/accounts 拉账户净值/盈亏
// 后端无数据时 store 保持初始 0，不扰动
interface PaperAccount {
  id?: string;
  account_id?: string;
  equity?: number;
  initial_balance?: number;
  unrealized_pnl?: number;
}

export function usePaperAccountTick(_enabled = true) {
  const setTick = useTradingStore((s) => s.setTick);
  const accountId = useAccountId();
  const { data } = useApi<PaperAccount | PaperAccount[]>(
    `/api/v1/paper/accounts?account_id=${encodeURIComponent(accountId)}`
  );

  useEffect(() => {
    if (!data) return;
    const acct = Array.isArray(data) ? data[0] : data;
    if (!acct) return;
    const equity = acct.equity != null ? Number(acct.equity) : 0;
    const pnlToday = acct.unrealized_pnl != null ? Number(acct.unrealized_pnl) : 0;
    setTick({ equity, pnlToday, flash: pnlToday >= 0 ? "equity" : "pnl" });
  }, [data, setTick]);
}
