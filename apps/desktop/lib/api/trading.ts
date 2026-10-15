import { api, apiPost, apiDelete } from "@/lib/api-client";
import type { Position, Order, PnLRecord } from "@/lib/types";

// GET /api/v1/trading/positions — bare 返回（不包装 APIResponse）
export async function getPositions(accountId = "paper-default"): Promise<Position[]> {
  return api<Position[]>(`/api/v1/trading/positions?account_id=${accountId}`);
}

// GET /api/v1/trading/orders — bare 返回
export async function getOrders(accountId = "paper-default"): Promise<Order[]> {
  return api<Order[]>(`/api/v1/trading/orders?account_id=${accountId}`);
}

// POST /api/v1/trading/orders — 包装返回 {order, risk_alerts}
export async function placeOrder(order: {
  symbol: string;
  side: "buy" | "sell";
  type: string;
  quantity: number;
  price?: number;
  leverage?: number;
  margin_mode?: string;
}): Promise<{ order: Order; risk_alerts: unknown[] }> {
  return apiPost(`/api/v1/trading/orders`, order);
}

// DELETE /api/v1/trading/orders/{order_id} — bare 返回
export async function cancelOrder(orderId: string, accountId = "paper-default"): Promise<Order> {
  return apiDelete<Order>(`/api/v1/trading/orders/${orderId}?account_id=${accountId}`);
}

// GET /api/v1/trading/mode
export async function getTradingMode(): Promise<{ mode: string }> {
  return api(`/api/v1/trading/mode`);
}

// GET /api/v1/paper/positions — 包装返回 list[dict]
export async function getPaperPositions(accountId = "paper-default"): Promise<Record<string, unknown>[]> {
  return api<Record<string, unknown>[]>(`/api/v1/paper/positions?account_id=${accountId}`);
}

// GET /api/v1/paper/orders
export async function getPaperOrders(accountId = "paper-default"): Promise<Record<string, unknown>[]> {
  return api<Record<string, unknown>[]>(`/api/v1/paper/orders?account_id=${accountId}`);
}

// GET /api/v1/pnl/{symbol}
export async function getPnlRecord(symbol: string): Promise<PnLRecord> {
  return api<PnLRecord>(`/api/v1/pnl/${symbol}`);
}
