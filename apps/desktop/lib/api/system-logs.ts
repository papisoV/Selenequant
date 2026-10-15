"use client";

import { api } from "@/lib/api-client";

const BASE = "/api/v1/system";

export interface SystemLogRow {
  id?: number | string;
  timestamp?: number | string;
  level?: string;
  logger?: string;
  message?: string;
  module?: string;
  line?: number;
  exc?: string;
  [k: string]: unknown;
}

export async function getSystemLogs(limit = 100, level?: string): Promise<SystemLogRow[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (level) qs.set("level", level);
  return await api<SystemLogRow[]>(`${BASE}/logs?${qs}`);
}
