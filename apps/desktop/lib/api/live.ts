"use client";

import { api, apiPost } from "@/lib/api-client";

const BASE = "/api/v1/live";

export interface ApprovalStatus {
  approved: boolean;
  engine_wired: boolean;
  error?: string;
}

export interface ApproveResult {
  approved: boolean;
  engine_wired: boolean;
  user?: string;
  error?: string;
}

export interface LivePlaceOrderRequest {
  symbol: string;
  side: "buy" | "sell";
  order_type: "MARKET" | "LIMIT" | "LIMIT_MAKER";
  quantity: number;
  price?: number;
  leverage?: number;
  reduce_only?: boolean;
  stop_price?: number;
}

export interface LivePlaceOrderResult {
  order_id?: string;
  status?: string;
  [k: string]: unknown;
}

export async function getApprovalStatus(): Promise<ApprovalStatus> {
  return await api<ApprovalStatus>(`${BASE}/approval-status`);
}

export async function approveFirstTrade(): Promise<ApproveResult> {
  return await apiPost<ApproveResult>(`${BASE}/approve`, {});
}

export async function placeLiveOrder(body: LivePlaceOrderRequest): Promise<LivePlaceOrderResult> {
  return await apiPost<LivePlaceOrderResult>(`${BASE}/place_order`, body);
}
