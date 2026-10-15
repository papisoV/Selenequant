"use client";

import { api } from "@/lib/api-client";

const BASE = "/api/v1/strategies";

export interface RunnerSlotStatus {
  deployment_id?: string;
  strategy_id?: string;
  status?: string;
  [k: string]: unknown;
}

export interface RunnerStatus {
  running: boolean;
  deployment_count: number;
  deployments: RunnerSlotStatus[];
  [k: string]: unknown;
}

export async function getRunnerStatus(): Promise<RunnerStatus> {
  return await api<RunnerStatus>(`${BASE}/runner/status`);
}
