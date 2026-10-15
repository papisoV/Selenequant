"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { apiPost, apiDelete } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { Loader2, RefreshCw, XCircle, PlusCircle } from "lucide-react";

// /api/v1/paper/orders?account_id=&status= 返回 list[dict] — OKX order 契约
interface PaperOrder {
  id?: string;
  symbol?: string;
  side?: string;
  type?: string;
  status?: string;
  quantity?: number;
  price?: number | null;
  stop_price?: number | null;
  filled_quantity?: number;
  avg_fill_price?: number | null;
  leverage?: number;
  margin_mode?: string;
  pos_side?: string;
  reduce_only?: boolean;
  created_at?: number;
}

const fmt = (n?: number | null, d = 4) =>
  n == null || Number.isNaN(Number(n)) ? "—" : Number(n).toFixed(d);

const STATUS_OPTIONS = ["", "created", "open", "filled", "cancelled", "rejected"] as const;

function fmtTime(ts?: number) {
  if (ts == null) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function PaperOrdersPage() {
  const accountId = useAccountId();
  const [status, setStatus] = useState<string>("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const statusQ = status ? `&status=${encodeURIComponent(status)}` : "";
  const { data, isLoading, mutate } = useApi<PaperOrder[]>(
    `/api/v1/paper/orders?account_id=${accountId}${statusQ}`
  );

  // 下单表单
  const [sym, setSym] = useState("BTC-USDT-SWAP");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState("100");
  const [price, setPrice] = useState("");
  const [orderBusy, setOrderBusy] = useState(false);

  const orders = data ?? [];

  async function placeOrder() {
    setOrderBusy(true);
    try {
      await apiPost("/api/v1/paper/orders", {
        account_id: accountId,
        symbol: sym.trim(),
        side,
        order_type: price ? "limit" : "market",
        quantity: Number(qty) || 0,
        price: price ? Number(price) : undefined,
        leverage: 1,
        margin_mode: "cross",
        pos_side: side === "buy" ? "long" : "short",
      });
      await mutate();
    } finally {
      setOrderBusy(false);
    }
  }

  async function cancelOrder(orderId: string) {
    setBusyId(orderId);
    try {
      await apiDelete(`/api/v1/paper/orders/${encodeURIComponent(orderId)}`);
      await mutate();
    } finally {
      setBusyId(null);
    }
  }

  const openCount = orders.filter((o) => o.status === "open" || o.status === "created").length;
  const filledCount = orders.filter((o) => o.status === "filled").length;
  const canCancel = (o: PaperOrder) => !["filled", "cancelled", "rejected"].includes(o.status ?? "");

  return (
    <div className="v17-page-grid grid gap-2">
      {/* 下单表单 */}
      <V17Card title="模拟下单" right={<span className="font-mono text-ink-3">{accountId}</span>}>
        <div className="flex flex-wrap items-center gap-1.5">
          <input
            value={sym}
            onChange={(e) => setSym(e.target.value)}
            aria-label="品种"
            placeholder="BTC-USDT-SWAP"
            className="w-36 rounded border border-border-soft bg-bg px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
          />
          <select
            value={side}
            onChange={(e) => setSide(e.target.value as "buy" | "sell")}
            aria-label="方向"
            className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] text-ink outline-none focus:border-gold/50"
          >
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
          </select>
          <input
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            aria-label="数量"
            placeholder="数量(USDT)"
            type="number"
            className="w-24 rounded border border-border-soft bg-bg px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
          />
          <input
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            aria-label="限价"
            placeholder="限价(市价留空)"
            type="number"
            className="w-28 rounded border border-border-soft bg-bg px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
          />
          <button
            onClick={placeOrder}
            disabled={orderBusy || !sym.trim() || !(Number(qty) > 0)}
            className="flex items-center gap-1 rounded border border-gold/40 bg-gold/10 px-2 py-1 text-[10px] text-gold hover:bg-gold/20 disabled:opacity-40"
          >
            {orderBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <PlusCircle className="h-3 w-3" />}
            下单
          </button>
          <span className="ml-auto text-[9px] text-ink-4">限价填价格→limit;留空→市价</span>
        </div>
      </V17Card>

      <V17Card title="模拟订单" right={<button onClick={() => mutate()} className="flex items-center gap-1 text-[9px] text-gold hover:underline"><RefreshCw className="h-3 w-3" />刷新</button>}>
        <div className="grid grid-cols-3 gap-2">
          <StatBlock label="订单总数" value={orders.length} cls="text-gold" />
          <StatBlock label="待成交" value={openCount} cls={openCount > 0 ? "text-gold" : "text-ink"} />
          <StatBlock label="已成交" value={filledCount} cls="text-pos" />
        </div>

        <div className="mt-2 flex items-center gap-2">
          <label className="text-[10px] text-ink-3" htmlFor="order-status">状态筛选</label>
          <select
            id="order-status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] font-mono text-ink outline-none focus:border-gold/50"
          >
            <option value="">全部</option>
            {STATUS_OPTIONS.filter((s) => s !== "").map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-6 mt-2"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : orders.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-6 mt-2">暂无订单</div>
        ) : (
          <div className="dtable mt-2 max-h-[60vh] overflow-auto">
            <div className="grid grid-cols-[80px_1fr_40px_50px_70px_70px_50px_70px_50px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
              <span>订单号</span><span>品种</span><span>方向</span><span>类型</span><span className="text-right">数量</span><span className="text-right">价格</span><span className="text-right">已成交</span><span>状态</span><span></span>
            </div>
            {orders.slice().reverse().map((o, i) => {
              const sideCls = o.side === "buy" ? "text-pos" : o.side === "sell" ? "text-neg" : "text-ink-3";
              const statusCls = o.status === "filled" ? "text-pos" : o.status === "open" || o.status === "created" ? "text-gold" : "text-ink-3";
              return (
                <div key={o.id ?? i} className="grid grid-cols-[80px_1fr_40px_50px_70px_70px_50px_70px_50px] gap-2 text-[10px] py-1 border-b border-border-soft items-center">
                  <span className="text-ink-4 font-mono truncate" title={o.id}>{o.id ?? "?"}</span>
                  <span className="text-gold font-mono truncate">{o.symbol ?? "?"}</span>
                  <span className={sideCls}>{o.side === "buy" ? "买" : o.side === "sell" ? "卖" : (o.side ?? "—")}</span>
                  <span className="text-ink-3 font-mono">{o.type ?? "market"}</span>
                  <span className="num text-right font-mono">{fmt(o.quantity)}</span>
                  <span className="num text-right font-mono">{fmt(o.price ?? o.avg_fill_price)}</span>
                  <span className="num text-right font-mono">{fmt(o.filled_quantity)}</span>
                  <span className={`font-mono ${statusCls}`}>{o.status ?? "—"}</span>
                  <span>
                    {canCancel(o) ? (
                      <button
                        onClick={() => cancelOrder(o.id ?? "")}
                        disabled={busyId === o.id}
                        title="撤单"
                        aria-label={`撤单 ${o.id}`}
                        className="flex h-5 w-5 items-center justify-center rounded border border-border-soft text-ink-4 hover:border-neg/50 hover:text-neg disabled:opacity-40"
                      >
                        {busyId === o.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <XCircle className="h-3 w-3" />}
                      </button>
                    ) : null}
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
