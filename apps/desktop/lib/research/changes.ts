"use client";

// /changes 聚合端点的客户端。走 api()(GET + fetchEnvelope 解包),
// 与 research-graph.ts 同族:/api/v1/research/ 不在 BARE_ENDPOINT_PATTERNS,
// 后端必须包信封,否则这里静默返回 null(回归门见 desk-page.test.tsx)。

import { api } from "@/lib/api-client";

export interface Epistemic {
  source: string;
  actor: string;
  cites_evidence: boolean;
  evidence_mode: string | null;
}

export interface ChangeItem {
  id: string;
  tier: 1 | 2 | 3;
  occurred_at: string;
  source: "event" | "revision" | "evidence" | "decision" | "proposal";
  kind: string;
  question_id: string | null;
  question_title: string;
  question_status: string | null;
  actor: string;
  detail: Record<string, unknown>;
  epistemic: Epistemic;
  consequence: string;
  needs_director: boolean;
}

export interface ChangesResult {
  changes: ChangeItem[];
  counts: { t1: number; t2: number; t3: number };
  server_now: string;
}

export async function fetchChanges(
  since: string | null,
): Promise<ChangesResult | null> {
  const qs = since ? `?since=${encodeURIComponent(since)}` : "";
  return api<ChangesResult>(`/api/v1/research/graph/changes${qs}`);
}
