"use client";

import { api, apiDelete, apiPut } from "@/lib/api-client";

const BASE = "/api/v1/me";

export interface UserDataExport {
  user_id: string;
  exported_at: number;
  strategies_count: number;
  backtests_count: number;
  chat_sessions_count: number;
  data: Record<string, unknown>;
  [k: string]: unknown;
}

export interface DeletionStatus {
  deletion_requested: boolean;
  requested_at: number | null;
  grace_period_days: number;
  [k: string]: unknown;
}

export async function exportUserData(): Promise<UserDataExport> {
  return await api<UserDataExport>(`${BASE}/export`);
}

export async function getDeletionStatus(): Promise<DeletionStatus> {
  return await api<DeletionStatus>(`${BASE}/deletion-status`);
}

export async function requestDeletion(): Promise<{
  deletion_requested: boolean;
  grace_period_days: number;
  message: string;
}> {
  return await apiDelete(`${BASE}`);
}

export async function cancelDeletion(): Promise<{ deletion_requested: boolean }> {
  return await apiPut<{ deletion_requested: boolean }>(`${BASE}/cancel-deletion`, {});
}
