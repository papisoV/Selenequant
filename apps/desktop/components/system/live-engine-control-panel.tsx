"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card } from "@/components/shared/page-filter-bar";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import {
  getApprovalStatus, approveFirstTrade, placeLiveOrder,
  type LivePlaceOrderRequest,
} from "@/lib/api/live";
import { toastSuccess, toastError, toastInfo } from "@/lib/toast";
import { Loader2, ShieldCheck, AlertTriangle } from "lucide-react";

type OrderType = "MARKET" | "LIMIT" | "LIMIT_MAKER";

export function LiveEngineControlPanel() {
  const { data: approval, mutate } = useSWR("/api/v1/live/approval-status", getApprovalStatus, {
    refreshInterval: 5000,
  });

  const [approveOpen, setApproveOpen] = useState(false);
  const [approveStep2, setApproveStep2] = useState(false);
  const [approving, setApproving] = useState(false);

  const [orderOpen, setOrderOpen] = useState(false);
  const [orderForm, setOrderForm] = useState<LivePlaceOrderRequest>({
    symbol: "",
    side: "buy",
    order_type: "MARKET",
    quantity: 0,
    leverage: 1,
    reduce_only: false,
  });
  const [placing, setPlacing] = useState(false);

  async function doApprove() {
    setApproving(true);
    try {
      const r = await approveFirstTrade();
      if (r.approved) {
        toastSuccess("实盘首单已授权(本 session 有效)");
        setApproveOpen(false);
        setApproveStep2(false);
        await mutate();
      } else {
        toastError(`授权失败: ${r.error || "unknown"}`);
      }
    } catch (e: unknown) {
      toastError(`授权失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setApproving(false);
    }
  }

  async function doPlaceOrder() {
    if (!orderForm.symbol || !orderForm.quantity) {
      toastError("symbol 和 quantity 必填");
      return;
    }
    if ((orderForm.order_type === "LIMIT" || orderForm.order_type === "LIMIT_MAKER") && !orderForm.price) {
      toastError("限价单必须填 price");
      return;
    }
    setPlacing(true);
    try {
      const r = await placeLiveOrder(orderForm);
      toastSuccess(`下单成功: ${r.order_id ?? "—"}`);
      setOrderOpen(false);
    } catch (e: unknown) {
      toastError(`下单失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPlacing(false);
    }
  }

  const approved = approval?.approved ?? false;
  const engineWired = approval?.engine_wired ?? false;

  return (
    <V17Card title="实盘引擎控制 (Live Engine)">
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <div className="text-ink-4">引擎状态</div>
          <div className={`font-mono ${engineWired ? "text-pos" : "text-neg"}`}>
            {engineWired ? "WIRED" : "NOT_WIRED"}
          </div>
        </div>
        <div>
          <div className="text-ink-4">首单授权</div>
          <div className={`font-mono ${approved ? "text-pos" : "text-gold"}`}>
            {approved ? "APPROVED" : "PENDING"}
          </div>
        </div>
        <div>
          <div className="text-ink-4">授权范围</div>
          <div className="font-mono text-ink-3">本 session</div>
        </div>
      </div>

      <div className="mt-2 flex gap-2">
        <button
          onClick={() => { setApproveOpen(true); setApproveStep2(false); }}
          disabled={!engineWired || approved}
          className={`rounded border px-2 py-1 text-[10px] flex items-center gap-1 ${
            !engineWired || approved
              ? "border-border-soft bg-surface-2 text-ink-4 cursor-not-allowed"
              : "border-border-soft bg-surface-3 text-gold hover:bg-gold/10"
          }`}
        >
          <ShieldCheck className="h-3 w-3" />
          {approved ? "已授权" : "授权首单"}
        </button>
        <button
          onClick={() => setOrderOpen(true)}
          disabled={!engineWired || !approved}
          className={`rounded border px-2 py-1 text-[10px] flex items-center gap-1 ${
            !engineWired || !approved
              ? "border-border-soft bg-surface-2 text-ink-4 cursor-not-allowed"
              : "border-border-soft bg-surface-3 text-neg hover:bg-neg/10"
          }`}
          title="手动下单直连 LiveExecutionEngine(绕过 paper mode)"
        >
          <AlertTriangle className="h-3 w-3" />
          手动下单
        </button>
      </div>

      <div className="mt-1 text-[9px] text-ink-4 leading-relaxed">
        注意:此入口直连 LiveExecutionEngine.place_order,绕过 paper mode 和 /trading/orders 流程。
        用于 retest 脚本和手动单次下单。正常交易 UI 请用 /trade/trd。
      </div>

      {/* Approve 两步确认 */}
      <Dialog open={approveOpen} onOpenChange={setApproveOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-gold" />
              实盘首单授权
            </DialogTitle>
            <DialogDescription>
              授权后本 session 内 LiveExecutionEngine.place_order 可下单。引擎重启后授权失效。
            </DialogDescription>
          </DialogHeader>
          {!approveStep2 ? (
            <div className="space-y-3">
              <div className="text-[11px] text-ink-3">
                确认要授权实盘首单吗?此操作仅翻转 approval flag,不会下单。
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setApproveOpen(false)}
                  className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-3 hover:bg-surface-4"
                >
                  取消
                </button>
                <button
                  onClick={() => setApproveStep2(true)}
                  className="rounded border border-gold/40 bg-gold/15 px-2 py-1 text-[10px] text-gold hover:bg-gold/25"
                >
                  下一步
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="text-[11px] text-neg flex items-start gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <span>最后确认:授权后即可调用 place_order 真实下单。确定继续?</span>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setApproveStep2(false)}
                  disabled={approving}
                  className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-3 hover:bg-surface-4"
                >
                  上一步
                </button>
                <button
                  onClick={doApprove}
                  disabled={approving}
                  className="rounded border border-pos/40 bg-pos/15 px-2 py-1 text-[10px] text-pos hover:bg-pos/25 flex items-center gap-1"
                >
                  {approving && <Loader2 className="h-3 w-3 animate-spin" />}
                  确认授权
                </button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Place order 表单 */}
      <Dialog open={orderOpen} onOpenChange={setOrderOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-neg" />
              手动下单 (Live Engine)
            </DialogTitle>
            <DialogDescription>
              直连 LiveExecutionEngine.place_order。quantity 为 BASE 资产数量(非 USDT)。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-[11px]">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-ink-4 mb-0.5">Symbol</div>
                <input
                  value={orderForm.symbol}
                  onChange={(e) => setOrderForm({ ...orderForm, symbol: e.target.value })}
                  placeholder="SOL/USDT"
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
              <div>
                <div className="text-ink-4 mb-0.5">方向</div>
                <select
                  value={orderForm.side}
                  onChange={(e) => setOrderForm({ ...orderForm, side: e.target.value as "buy" | "sell" })}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 text-ink"
                >
                  <option value="buy">buy</option>
                  <option value="sell">sell</option>
                </select>
              </div>
              <div>
                <div className="text-ink-4 mb-0.5">订单类型</div>
                <select
                  value={orderForm.order_type}
                  onChange={(e) => setOrderForm({ ...orderForm, order_type: e.target.value as OrderType })}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 text-ink"
                >
                  <option value="MARKET">MARKET</option>
                  <option value="LIMIT">LIMIT</option>
                  <option value="LIMIT_MAKER">LIMIT_MAKER</option>
                </select>
              </div>
              <div>
                <div className="text-ink-4 mb-0.5">数量 (BASE)</div>
                <input
                  type="number"
                  step="0.0001"
                  value={orderForm.quantity || ""}
                  onChange={(e) => setOrderForm({ ...orderForm, quantity: parseFloat(e.target.value) || 0 })}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
              {(orderForm.order_type === "LIMIT" || orderForm.order_type === "LIMIT_MAKER") && (
                <div>
                  <div className="text-ink-4 mb-0.5">价格</div>
                  <input
                    type="number"
                    step="0.01"
                    value={orderForm.price ?? ""}
                    onChange={(e) => setOrderForm({ ...orderForm, price: parseFloat(e.target.value) || undefined })}
                    className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                  />
                </div>
              )}
              <div>
                <div className="text-ink-4 mb-0.5">杠杆</div>
                <input
                  type="number"
                  min={1}
                  max={125}
                  value={orderForm.leverage ?? 1}
                  onChange={(e) => setOrderForm({ ...orderForm, leverage: parseInt(e.target.value) || 1 })}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
              <div>
                <div className="text-ink-4 mb-0.5">止损价 (可选)</div>
                <input
                  type="number"
                  step="0.01"
                  value={orderForm.stop_price ?? ""}
                  onChange={(e) => setOrderForm({ ...orderForm, stop_price: parseFloat(e.target.value) || undefined })}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
              <div className="col-span-2 flex items-center gap-2">
                <input
                  type="checkbox"
                  id="reduce_only"
                  checked={orderForm.reduce_only ?? false}
                  onChange={(e) => setOrderForm({ ...orderForm, reduce_only: e.target.checked })}
                  className="h-3 w-3"
                />
                <label htmlFor="reduce_only" className="text-ink-3">reduce_only (仅平仓)</label>
              </div>
            </div>
            <div className="text-[10px] text-neg flex items-start gap-1 pt-1">
              <AlertTriangle className="h-3 w-3 mt-0.5 flex-shrink-0" />
              <span>真实下单,非模拟。请确认 symbol/数量/方向无误。</span>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setOrderOpen(false)}
                disabled={placing}
                className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-3 hover:bg-surface-4"
              >
                取消
              </button>
              <button
                onClick={doPlaceOrder}
                disabled={placing}
                className="rounded border border-neg/40 bg-neg/15 px-2 py-1 text-[10px] text-neg hover:bg-neg/25 flex items-center gap-1"
              >
                {placing && <Loader2 className="h-3 w-3 animate-spin" />}
                确认下单
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </V17Card>
  );
}
