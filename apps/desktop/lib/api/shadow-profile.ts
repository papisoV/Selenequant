"use client";

import { apiPost } from "@/lib/api-client";

const BASE = "/api/v1/shadow-profile";

export interface ShadowExtractResult {
  shadow_id: string;
  rules: Array<Record<string, unknown>>;
  meta: Record<string, unknown>;
  [k: string]: unknown;
}

export interface ShadowRenderResult {
  source: string;
  [k: string]: unknown;
}

export interface ShadowBacktestBody {
  window_start: string | number;
  window_end: string | number;
  markets?: string[];
  per_market_count?: number;
  source?: string;
}

export async function extractShadowProfile(
  file: File,
  minSupport = 3,
  maxRules = 5,
): Promise<ShadowExtractResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("min_support", String(minSupport));
  form.append("max_rules", String(maxRules));
  const res = await fetch(`${BASE}/extract`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`extract failed: ${res.status} ${txt.slice(0, 200)}`);
  }
  return (await res.json()) as ShadowExtractResult;
}

export async function renderShadowEngine(shadowId: string): Promise<ShadowRenderResult> {
  return await apiPost<ShadowRenderResult>(`${BASE}/${encodeURIComponent(shadowId)}/render-engine`, {});
}

export async function backtestShadowProfile(
  shadowId: string,
  body: ShadowBacktestBody,
): Promise<Record<string, unknown>> {
  return await apiPost<Record<string, unknown>>(`${BASE}/${encodeURIComponent(shadowId)}/backtest`, body);
}
