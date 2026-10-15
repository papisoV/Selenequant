"use client";

import { api, apiPost } from "@/lib/api-client";

const DEPLOY_BASE = "/api/v1/strategies";

export interface DeploymentSummary {
  id: string;
  strategy_id: string;
  strategy_name?: string;
  deployment_id?: string;
  status: "running" | "stopped" | "paused" | "starting" | "stopping" | string;
  account_id?: string;
  mode?: "paper" | "live" | string;
  created_at?: string;
  strategy_version?: number;
  // deployments/list 注入(内存 _deployment_flags,后端 strategies.py list 端点合并)
  flags?: DeploymentFlags;
  // US-102: runner slot 运行时健康;null = runner 没起或 slot 不存在
  runtime?: {
    bar_count: number;
    signals_emitted: number;
    errors: number;
    last_signal_ts: number | null;
  } | null;
}

export async function listDeployments(): Promise<DeploymentSummary[]> {
  const data = await api<{ deployments?: DeploymentSummary[] } | DeploymentSummary[]>(
    `${DEPLOY_BASE}/deployments/list`,
  );
  if (Array.isArray(data)) return data;
  return data?.deployments ?? [];
}

export interface DeployResponse {
  deployed?: boolean;
  runner_status?: "running" | "failed" | "blocked" | string;
  runner_error?: string | null;
  backtest_warning?: string | null;
  [k: string]: unknown;
}

// 门拒绝(runner_status=blocked)和 runner 失败都是 HTTP 200,调用方必须
// 自己检查 runner_status —— 否则会把发布门拦截误报成部署成功。
export async function deployPaper(strategyId: string): Promise<DeployResponse> {
  return apiPost<DeployResponse>(`${DEPLOY_BASE}/${strategyId}/deploy-paper`, {});
}

export async function deployLive(strategyId: string): Promise<DeployResponse> {
  return apiPost<DeployResponse>(`${DEPLOY_BASE}/${strategyId}/deploy-live`, {});
}

export async function pauseDeployment(strategyId: string): Promise<void> {
  await apiPost(`${DEPLOY_BASE}/${strategyId}/pause-deployment`, {});
}

export async function resumeDeployment(strategyId: string): Promise<void> {
  await apiPost(`${DEPLOY_BASE}/${strategyId}/resume-deployment`, {});
}

export async function stopDeployment(strategyId: string): Promise<void> {
  await apiPost(`${DEPLOY_BASE}/${strategyId}/stop-deployment`, {});
}

export async function cloneStrategy(strategyId: string): Promise<void> {
  await apiPost(`${DEPLOY_BASE}/${strategyId}/clone`, {});
}

export async function exportStrategy(strategyId: string): Promise<string> {
  const res = await fetch(`${DEPLOY_BASE}/${strategyId}/export`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`export failed: ${res.status}`);
  return res.text();
}

export interface StrategyDetail {
  id: string;
  name?: string;
  type?: string;
  description?: string;
  symbols?: string[];
  interval?: string;
  [k: string]: unknown;
}

export async function createFromTemplate(templateId: string): Promise<StrategyDetail> {
  return await apiPost<StrategyDetail>(`${DEPLOY_BASE}/factory-templates/${templateId}/create`, {});
}

export interface DeploymentFlags {
  ai_gate_enabled?: boolean;
  bias_guard_enabled?: boolean;
}

export async function updateDeploymentFlags(
  strategyId: string,
  flags: DeploymentFlags,
): Promise<{ updated: boolean; flags: DeploymentFlags; error?: string }> {
  const res = await fetch(`${DEPLOY_BASE}/${strategyId}/deployment/flags`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(flags),
  });
  if (!res.ok) throw new Error(`updateDeploymentFlags failed: ${res.status}`);
  const body = await res.json();
  return (body?.data ?? body) as { updated: boolean; flags: DeploymentFlags; error?: string };
}
