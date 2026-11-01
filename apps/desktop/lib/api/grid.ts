"use client";

import { api, apiPost } from "@/lib/api-client";

const BASE = "/api/v1/grid";

export interface GridStateResponse {
  running?: boolean;
  symbol?: string;
  upper?: number;
  lower?: number;
  levels?: number;
  size?: number;
  cur_price?: number;
  fills?: { price: number; side: "buy" | "sell"; size: number }[];
  [k: string]: unknown;
}

export interface SentinelStatus {
  running: boolean;
  [k: string]: unknown;
}

export interface SentinelRunResult {
  output: string;
  [k: string]: unknown;
}

export type SentinelTool = "sentinel" | "market" | "report";

export interface GridChatRequest {
  message: string;
  history?: { role: string; content: string }[];
}

export interface GridChatResult {
  output: string;
  [k: string]: unknown;
}

function withQuery(path: string, params: Record<string, string | number | boolean | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) sp.set(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function cancelGridOrders(exchange: string): Promise<GridStateResponse> {
  return await apiPost<GridStateResponse>(withQuery(`${BASE}/cancel-orders`, { exchange }));
}

export async function closeGridPosition(exchange: string): Promise<GridStateResponse> {
  return await apiPost<GridStateResponse>(withQuery(`${BASE}/close-position`, { exchange }));
}

export async function resetGridStats(exchange: string): Promise<GridStateResponse> {
  return await apiPost<GridStateResponse>(withQuery(`${BASE}/reset-stats`, { exchange }));
}

export async function getGridSentinel(): Promise<SentinelStatus> {
  return await api<SentinelStatus>(`${BASE}/sentinel`);
}

export async function runGridSentinel(
  tool: SentinelTool = "sentinel",
  exchange = "ex",
): Promise<SentinelRunResult> {
  return await apiPost<SentinelRunResult>(withQuery(`${BASE}/sentinel/run`, { tool, exchange }));
}

export async function gridChat(body: GridChatRequest): Promise<GridChatResult> {
  return await apiPost<GridChatResult>(`${BASE}/chat`, body);
}
