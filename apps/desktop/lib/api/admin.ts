"use client";

import { api, apiPost } from "@/lib/api-client";

const BASE = "/api/v1/admin";

export interface KillSwitchState {
  active: boolean;
  reason: string | null;
  tripped_at: number | null;
  [k: string]: unknown;
}

export async function getKillSwitch(): Promise<KillSwitchState> {
  return await api<KillSwitchState>(`${BASE}/kill-switch`);
}

export async function tripKillSwitch(reason: string): Promise<KillSwitchState> {
  return await apiPost<KillSwitchState>(`${BASE}/kill-switch/trip`, { reason });
}

export async function releaseKillSwitch(reason: string, force = false): Promise<KillSwitchState> {
  return await apiPost<KillSwitchState>(`${BASE}/kill-switch/release`, { reason, force });
}
