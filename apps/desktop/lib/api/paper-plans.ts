"use client";

import { api, apiPost } from "@/lib/api-client";

const BASE = "/api/v1";

export type PaperPlanStatus = "planned" | "active" | "completed" | "cancelled";

export interface PaperPlan {
  id: string;
  experiment_id?: string | null;
  name: string;
  symbol?: string | null;
  timeframe?: string | null;
  direction?: string | null;
  entry?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  quantity?: number | null;
  account_id?: string | null;
  status: PaperPlanStatus;
  note?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DeployPaperPlanBody {
  account_id?: string;
  quantity?: number;
  note?: string;
}

export async function deployExperimentToPaper(
  experimentId: string,
  body?: DeployPaperPlanBody,
): Promise<PaperPlan> {
  return await apiPost<PaperPlan>(
    `${BASE}/experiments/${encodeURIComponent(experimentId)}/paper-plan`,
    body ?? {},
  );
}

export async function listExperimentPaperPlans(experimentId: string): Promise<PaperPlan[]> {
  return await api<PaperPlan[]>(`${BASE}/experiments/${encodeURIComponent(experimentId)}/paper-plans`);
}

export async function listPaperPlans(params?: { status?: PaperPlanStatus; limit?: number; offset?: number }): Promise<PaperPlan[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const query = qs.toString();
  return await api<PaperPlan[]>(`${BASE}/paper-plans${query ? `?${query}` : ""}`);
}

export async function transitionPaperPlan(planId: string, status: PaperPlanStatus): Promise<PaperPlan> {
  return await apiPost<PaperPlan>(`${BASE}/paper-plans/${encodeURIComponent(planId)}/transition`, { status });
}
