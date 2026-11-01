"use client";

import { api } from "@/lib/api-client";

/** One (symbol, interval) group from GET /api/v1/episodes/world-model. */
export interface WorldModelGroup {
  symbol: string;
  interval: string;
  episode_count: number;
  winning_count: number;
  win_rate: number;
  kinds: string[];
  best: {
    strategy_id: string;
    annual_return: number | null;
    sharpe_ratio: number | null;
  } | null;
  best_sharpe: number | null;
  worst_sharpe: number | null;
  avg_sharpe: number | null;
  last_episode_at: string | null;
}

export interface WorldModelPayload {
  groups: WorldModelGroup[];
  generated_at: string;
  total_episodes: number;
}

export async function getWorldModel(): Promise<WorldModelPayload> {
  return await api<WorldModelPayload>("/api/v1/episodes/world-model");
}
