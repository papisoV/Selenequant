"use client";

import useSWR from "swr";
import { getPositions } from "@/lib/api/trading";
import { adaptPosition } from "@/lib/adapters/position";
import type { FrontendPosition } from "@/lib/types/frontend";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { useUiText } from "@/components/i18n/use-ui-text"

export function PositionsTable() {
  const tr = useUiText()

  const accountId = useAccountId();
  const { data, error, isLoading } = useSWR("positions", () => getPositions(accountId), {
    revalidateOnFocus: false,
    refreshInterval: 10_000,
  });

  const rows: FrontendPosition[] = (data ?? []).map((p) => adaptPosition(p));

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border-soft bg-surface px-4 py-2">
        <div className="flex items-center gap-3">
          <h1 className="text-[13px] font-medium text-ink">{tr("我的持仓")}</h1>
          <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">
            {accountId}
          </span>
        </div>
        {data && data.length > 0 && (
          <span className="text-[9px] text-ink-3">{data.length} {tr("条")}</span>
        )}
      </header>
      <div className="flex-1 overflow-auto bg-bg">
        {error ? (
          <div className="flex h-full items-center justify-center text-[11px] text-neg">
            {tr("加载失败 — 后端 trading/positions 端点不可达")}
          </div>
        ) : isLoading ? (
          <div className="flex h-full items-center justify-center text-[11px] text-ink-3">
            {tr("加载中…")}
          </div>
        ) : rows.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <p className="text-[11px] text-ink-3">{tr("当前账户无持仓")}</p>
            <p className="text-[9px] text-ink-4">
              account_id={accountId} {tr("— 模拟账户空仓状态")}
            </p>
          </div>
        ) : (
          <table className="w-full text-[10px]">
            <thead className="bg-surface-2 text-ink-3">
              <tr>
                <th className="px-2 py-1 text-left">{tr("合约")}</th>
                <th className="px-2 py-1 text-left">{tr("方向")}</th>
                <th className="px-2 py-1 text-right">{tr("数量")}</th>
                <th className="px-2 py-1 text-right">{tr("均价")}</th>
                <th className="px-2 py-1 text-right">{tr("现价")}</th>
                <th className="px-2 py-1 text-right">{tr("未实现盈亏")}</th>
                <th className="px-2 py-1 text-left">{tr("市场")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-border-soft">
                  <td className="px-2 py-1 text-ink">{r.sym}</td>
                  <td className="px-2 py-1 text-ink-2">{r.side}</td>
                  <td className="px-2 py-1 text-right text-ink-2">{r.qty}</td>
                  <td className="px-2 py-1 text-right text-ink-2">{r.avg}</td>
                  <td className="px-2 py-1 text-right text-ink-2">{r.now}</td>
                  <td className={`px-2 py-1 text-right ${r.cls === "pos" ? "text-pos" : "text-neg"}`}>
                    {r.pnl}
                  </td>
                  <td className="px-2 py-1 text-ink-3">{r.market}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
