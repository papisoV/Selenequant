"use client";

import { api } from "@/lib/api-client";

const BASE = "/api/v1/agent";

export interface AgentReasoningRow {
  id?: number | string;
  timestamp?: number | string;
  session_id?: string;
  user_id?: string;
  symbol?: string;
  role?: string;
  content?: string;
  chains_json?: string;
  meta_json?: string;
  [k: string]: unknown;
}

export async function getAgentReasoningRecent(
  limit = 50,
  sessionId?: string,
  symbol?: string,
): Promise<AgentReasoningRow[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (sessionId) qs.set("session_id", sessionId);
  if (symbol) qs.set("symbol", symbol);
  return await api<AgentReasoningRow[]>(`${BASE}/reasoning/recent?${qs}`);
}
