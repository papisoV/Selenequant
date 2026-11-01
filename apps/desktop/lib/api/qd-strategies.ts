"use client";

import { api, apiPost } from "@/lib/api-client";

const BASE = "/api/v1/strategies/qd-templates";

/** QD's own reported result for a strategy, or the reason it could not be read. */
export interface QDBaseline {
  /** `null` when the library's prose did not anchor a single figure. */
  metrics: Record<string, number> | null;
  /** How many competing figures the extractor saw. */
  candidates: number;
  reason: string;
}

export type QDFlag =
  | "falsified"
  | "in_sample_only"
  | "portfolio"
  | "multi_instrument";

/**
 * One QuantDinger export from the local backup library.
 *
 * `symbol` and `interval` are declarations read out of the source, not
 * settings: the export binds one instrument in its `initialize` hook, so the
 * UI shows them read-only and the request carries only the `id`.
 */
export interface QDStrategy {
  id: string;
  name: string;
  file: string;
  symbol: string;
  instrument?: string;
  exchange: string | null;
  market_type: string;
  interval: string;
  warmup: number;
  direction_mode?: string;
  params: Record<string, number>;
  instruments?: string[];
  runnable: boolean;
  reason?: string;
  params_schema?: Record<string, unknown>;
  qd_baseline: QDBaseline;
  flags: QDFlag[];
}

/** The backup library. An absent library is an empty list, not an error. */
export async function listQdStrategies(): Promise<QDStrategy[]> {
  const data = await api<QDStrategy[] | null>(BASE);
  return Array.isArray(data) ? data : [];
}

/**
 * Queue a backtest for one export.
 *
 * Deliberately sends only the id: the source's own declaration supplies the
 * symbol, interval and venue. Returns the task id to poll.
 */
export async function runQdStrategy(strategyId: string): Promise<string> {
  const res = await apiPost<{ task_id: string }>("/api/v1/backtests", {
    strategy_id: strategyId,
  });
  return res.task_id;
}
