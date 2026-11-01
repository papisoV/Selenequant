"use client";

import { api, apiPost } from "@/lib/api-client";

const BASE = "/api/v1/experiments";

export interface ExperimentSetupParams {
  direction?: string;
  entry?: number;
  stop_loss?: number;
  take_profit?: number;
}

export interface ExperimentBacktestResult {
  total_setups?: number;
  win_rate?: number;
  avg_win?: number;
  avg_loss?: number;
  expectancy?: number;
  profit_factor?: number | null;
  max_drawdown?: number;
  [k: string]: unknown;
}

export interface Experiment {
  id: string;
  name: string;
  hypothesis?: string | null;
  symbol?: string | null;
  timeframe?: string | null;
  dataset?: string | null;
  strategy_version?: string | null;
  setup_params?: ExperimentSetupParams | null;
  backtest_result?: ExperimentBacktestResult | null;
  evidence_refs?: unknown[] | null;
  parent_experiment?: string | null;
  created_at?: string | null;
}

export interface CreateExperimentBody {
  name: string;
  hypothesis?: string;
  symbol?: string;
  timeframe?: string;
  dataset?: string;
  strategy_version?: string;
  setup_params?: ExperimentSetupParams;
  backtest_result?: ExperimentBacktestResult;
  evidence_refs?: unknown[];
}

export interface ListExperimentsParams {
  symbol?: string;
  limit?: number;
  offset?: number;
}

export async function listExperiments(params?: ListExperimentsParams): Promise<Experiment[]> {
  const qs = new URLSearchParams();
  if (params?.symbol) qs.set("symbol", params.symbol);
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const query = qs.toString();
  return await api<Experiment[]>(`${BASE}${query ? `?${query}` : ""}`);
}

export async function getExperiment(id: string): Promise<Experiment> {
  return await api<Experiment>(`${BASE}/${encodeURIComponent(id)}`);
}

export async function createExperiment(body: CreateExperimentBody): Promise<Experiment> {
  return await apiPost<Experiment>(BASE, body);
}

export async function forkExperiment(id: string, overrides?: Partial<CreateExperimentBody>): Promise<Experiment> {
  return await apiPost<Experiment>(`${BASE}/${encodeURIComponent(id)}/fork`, {
    overrides: overrides ?? {},
  });
}

export interface EvidenceLineageNode {
  id: string;
  name: string;
  created_at?: string | null;
}

export interface EvidenceReport {
  experiment: {
    id: string;
    name: string;
    hypothesis?: string | null;
    symbol?: string | null;
    timeframe?: string | null;
    strategy_version?: string | null;
    created_at?: string | null;
  };
  data_source: {
    dataset?: string | null;
    symbol?: string | null;
    timeframe?: string | null;
  };
  config: {
    strategy_version?: string | null;
    setup_params?: ExperimentSetupParams | null;
  };
  version: {
    id: string;
    created_at?: string | null;
    strategy_version?: string | null;
    parent_experiment?: string | null;
  };
  result: {
    total_setups?: number | null;
    win_rate?: number | null;
    avg_win?: number | null;
    avg_loss?: number | null;
    expectancy?: number | null;
    profit_factor?: number | null;
    max_drawdown?: number | null;
  };
  robustness: {
    has_backtest: boolean;
    notes: string[];
  };
  risk: {
    max_drawdown?: number | null;
    risk_per_trade?: {
      direction: string;
      entry: number;
      stop_loss: number;
      amount: number;
    } | null;
  };
  conclusion: string;
  lineage: {
    parent?: string | null;
    ancestors: EvidenceLineageNode[];
    children: EvidenceLineageNode[];
  };
}

export async function getEvidenceReport(id: string): Promise<EvidenceReport> {
  return await api<EvidenceReport>(`${BASE}/${encodeURIComponent(id)}/evidence-report`);
}
