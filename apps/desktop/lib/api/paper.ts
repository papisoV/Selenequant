"use client";

import { api } from "@/lib/api-client";

const BASE = "/api/v1/paper";

export interface PaperAccountDetail {
  account_id?: string;
  balance?: number;
  equity?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  current_balance?: number;
  total_realized_pnl?: number;
  [k: string]: unknown;
}

export interface EquityPoint {
  timestamp?: string | number;
  equity?: number;
  [k: string]: unknown;
}

export async function getPaperAccount(accountId: string): Promise<PaperAccountDetail> {
  return await api<PaperAccountDetail>(`${BASE}/accounts/${encodeURIComponent(accountId)}`);
}

export async function getEquityHistory(accountId: string, limit = 100): Promise<EquityPoint[]> {
  return await api<EquityPoint[]>(
    `${BASE}/accounts/${encodeURIComponent(accountId)}/equity-history?limit=${limit}`,
  );
}
