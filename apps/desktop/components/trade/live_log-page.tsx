"use client";

import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useAccountId } from "@/lib/hooks/use-account-id";

interface LogEntry {
  t: string;
  level: "info" | "warn" | "error";
  sym: string;
  evt: string;
  detail: string;
}

interface BackendOrder {
  order_id?: string;
  id?: string;
  symbol?: string;
  side?: string;
  type?: string;
  price?: number;
  quantity?: number;
  quantity_base?: number;
  status?: string;
  created_at?: string;
  filled_quantity?: number;
}

// 设计稿 live_log 实盘日志 — 按时间倒序的交易+事件日志
export function LiveLogPage() {
  const accountId = useAccountId();
  const { data: realOrders } = useApi<BackendOrder[]>(
    `/api/v1/trading/orders?account_id=${encodeURIComponent(accountId)}`
  );

  const logs: LogEntry[] = (() => {
    if (!realOrders || realOrders.length === 0) return [];
    return realOrders
      .filter((o) => o.status)
      .map((o) => {
        const isFilled = o.status === "filled";
        const isRejected = o.status === "rejected" || o.status === "canceled";
        const level: LogEntry["level"] = isRejected ? "error" : isFilled ? "info" : "warn";
        const side = o.side === "buy" ? "买" : "卖";
        const qty = Number(o.quantity_base ?? o.quantity ?? 0);
        const evt = isFilled
          ? `订单 #${o.order_id ?? o.id ?? "?"} 已成交`
          : isRejected
            ? `订单 #${o.order_id ?? o.id ?? "?"} 已拒绝`
            : `订单 #${o.order_id ?? o.id ?? "?"} ${o.status}`;
        const detail = `${side} ${qty} @ ${o.price ?? 0}`;
        const t = o.created_at?.slice(11, 19) ?? "—";
        return { t, level, sym: o.symbol || "—", evt, detail };
      })
      .slice(0, 20);
  })();

  const levelColor: Record<string, string> = { info: "text-ink-3", warn: "text-gold", error: "text-destructive" };
  const levelBg: Record<string, string> = { info: "transparent", warn: "rgba(250, 197, 71, 0.10)", error: "rgba(250, 104, 99, 0.10)" };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="实盘日志">
        <div className="flex flex-col">
          {logs.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          )}
          {logs.map((l, i) => (
            <div
              key={i}
              className="flex items-start gap-3 text-[10px] py-1.5 border-b border-border-soft"
              style={{ background: levelBg[l.level] }}
            >
              <span className="font-mono text-ink-4 w-20 shrink-0">{l.t}</span>
              <span className={`font-mono w-12 shrink-0 ${levelColor[l.level]}`}>
                [{l.level.toUpperCase()}]
              </span>
              <span className="text-gold font-mono w-24 shrink-0">{l.sym}</span>
              <span className="text-ink-2 flex-1">{l.evt}</span>
              <span className="text-ink-4 font-mono text-right">{l.detail}</span>
            </div>
          ))}
        </div>
      </V17Card>
      <V17Card title="日志统计">
        <div className="grid grid-cols-4 gap-2">
          <StatBlock label="总日志" value={logs.length} />
          <StatBlock label="INFO" value={logs.filter((l) => l.level === "info").length} cls="text-ink-2" />
          <StatBlock label="WARN" value={logs.filter((l) => l.level === "warn").length} cls="text-gold" />
          <StatBlock label="ERROR" value={logs.filter((l) => l.level === "error").length} cls="text-destructive" />
        </div>
      </V17Card>
    </div>
  );
}
