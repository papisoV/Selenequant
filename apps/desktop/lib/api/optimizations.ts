"use client";

import { api, apiPost, apiDelete } from "@/lib/api-client";

const BASE = "/api/v1/optimizations";

export interface OptimizationRun {
  id?: string;
  strategy?: string;
  strategy_id?: string;
  trials?: number;
  best_sharpe?: number;
  status?: string;
  best_params?: Record<string, unknown>;
  [k: string]: unknown;
}

export async function getOptimization(id: string): Promise<OptimizationRun> {
  return await api<OptimizationRun>(`${BASE}/${id}`);
}

export async function applyOptimization(id: string): Promise<{ applied: boolean; best_params: Record<string, unknown> }> {
  return await apiPost(`${BASE}/${id}/apply`, {});
}

export interface ParamSuggestion {
  strategy_id: string;
  suggestions: Record<string, (number | string)[]>;
  source: "llm" | "default";
}

export async function suggestOptimization(strategyId: string): Promise<ParamSuggestion> {
  return await apiPost<ParamSuggestion>(`${BASE}/suggest`, { strategy_id: strategyId });
}

export async function exportOptimization(id: string, format: "json" | "csv" = "json"): Promise<string> {
  const res = await fetch(`${BASE}/${id}/export?format=${format}`);
  if (!res.ok) throw new Error(`export failed: ${res.status}`);
  const data = await res.json();
  return data?.data?.content ?? JSON.stringify(data);
}

export async function deleteOptimization(id: string): Promise<void> {
  await apiDelete(`${BASE}/${id}`);
}
