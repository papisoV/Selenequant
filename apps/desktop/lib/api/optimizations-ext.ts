"use client";

import { api, apiPost, apiDelete } from "@/lib/api-client";

const BASE = "/api/v1/optimizations";

export interface AutoOptimizationRequest {
  strategy_id: string;
  param_space: Record<string, unknown>;
  interval_minutes?: number;
  max_runs?: number;
}

export interface AutoOptimization {
  id: string;
  strategy_id: string;
  status: string;
  config?: {
    param_space?: Record<string, unknown>;
    interval_minutes?: number;
    max_runs?: number;
  };
  created_at?: number;
  last_run_at?: number | null;
  runs_completed?: number;
  [k: string]: unknown;
}

export interface SuggestParamsRequest {
  strategy_id: string;
}

export interface DistributedStatus {
  [k: string]: unknown;
}

export async function createAutoOptimization(body: AutoOptimizationRequest): Promise<{ id: string; status: string }> {
  return await apiPost<{ id: string; status: string }>(`${BASE}/auto`, body);
}

export async function getAutoOptimization(autoId: string): Promise<AutoOptimization> {
  return await api<AutoOptimization>(`${BASE}/auto/${encodeURIComponent(autoId)}`);
}

export async function cancelAutoOptimization(autoId: string): Promise<{ cancelled: boolean }> {
  return await apiDelete<{ cancelled: boolean }>(`${BASE}/auto/${encodeURIComponent(autoId)}`);
}

export async function suggestParams(body: SuggestParamsRequest): Promise<Record<string, unknown>> {
  return await apiPost<Record<string, unknown>>(`${BASE}/suggest`, body);
}

export async function getDistributedStatus(): Promise<DistributedStatus> {
  return await api<DistributedStatus>(`${BASE}/distributed/status`);
}
