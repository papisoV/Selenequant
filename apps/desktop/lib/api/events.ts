"use client";

import { api } from "@/lib/api-client";

const BASE = "/api/v1/events";

export interface EventRow {
  id?: number | string;
  timestamp?: number | string;
  kind?: string;
  source?: string;
  symbol?: string;
  severity?: string;
  title?: string;
  body?: string;
  payload_json?: string;
  [k: string]: unknown;
}

export async function getRecentEvents(limit = 50, kind?: string): Promise<EventRow[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (kind) qs.set("kind", kind);
  return await api<EventRow[]>(`${BASE}/recent?${qs}`);
}
