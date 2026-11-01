"use client";

import { api, apiPost } from "@/lib/api-client";

const BASE = "/api/v1/memory";

export interface StrategyHistoryResult {
  strategy_id: string;
  history: Array<Record<string, unknown>>;
  regime: string;
  [k: string]: unknown;
}

export async function getStrategyHistory(
  strategyId: string,
  regime?: string,
  limit = 50,
): Promise<StrategyHistoryResult> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (regime) qs.set("regime", regime);
  return await api<StrategyHistoryResult>(
    `${BASE}/strategy-history/${encodeURIComponent(strategyId)}?${qs}`,
  );
}
