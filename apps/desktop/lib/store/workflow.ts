"use client";

import { create } from "zustand";

export type WorkflowStep =
  | "strat"
  | "factors"
  | "optimize"
  | "backtest"
  | "deploy";

export interface SelectedTemplate {
  id: string;
  name: string;
  category: string;
  sharpe?: number;
  winrate?: number;
}

export interface SelectedFactors {
  factorIds: string[];
  pickedAt: number;
}

export interface OptimizationResult {
  source: "optimizer" | "scanner" | "tuning";
  taskId?: string;
  sharpe?: number;
  params?: Record<string, number | string>;
  interval?: string;
  symbol?: string;
  pickedAt: number;
}

export interface BacktestRef {
  taskId: string;
  sharpe?: number;
  status?: string;
  pickedAt: number;
}

// 第 3 期:跨页持久化子状态,防止手动切页丢上下文

export type TuningMode = "optuna" | "smart";
export type TuningStatus = "idle" | "running" | "completed" | "failed" | "cancelled";

export interface TuningSubState {
  mode: TuningMode;
  selectedStrategies: string[];
  selectedIntervals: string[];
  exchange: string;
  marketType: string;
  symbol: string;
  trials: number;
  volatility: string;
  trendStrength: string;
  maxParams: number;
  taskId: string | null;
  status: TuningStatus;
  progress: unknown | null;
}

export interface RschSubState {
  currentId: string;
}

export interface QuantLabSubState {
  selectedProvider: string;
  selectedAction: string;
  pricesText: string;
  paramsText: string;
  report: unknown | null;
}

export interface DailyCycleSubState {
  tradingDate: string;
  icProvider: string;
  result: unknown | null;
}

export interface WorkflowState {
  template: SelectedTemplate | null;
  factors: SelectedFactors | null;
  optimization: OptimizationResult | null;
  backtest: BacktestRef | null;
  deployTarget: "paper" | "live" | null;

  tuning: TuningSubState | null;
  rsch: RschSubState | null;
  quantLab: QuantLabSubState | null;
  dailyCycle: DailyCycleSubState | null;

  selectTemplate: (t: SelectedTemplate) => void;
  setFactors: (f: string[]) => void;
  setOptimization: (r: OptimizationResult) => void;
  setBacktest: (b: BacktestRef) => void;
  setDeployTarget: (t: "paper" | "live") => void;
  setTuning: (patch: Partial<TuningSubState>) => void;
  setRsch: (patch: Partial<RschSubState>) => void;
  setQuantLab: (patch: Partial<QuantLabSubState>) => void;
  setDailyCycle: (patch: Partial<DailyCycleSubState>) => void;
  reset: () => void;
  resetFrom: (step: WorkflowStep) => void;
}

export function slugToStep(slug: string): WorkflowStep | null {
  switch (slug) {
    case "strat":
      return "strat";
    case "factors":
      return "factors";
    case "optimizer":
    case "scanner":
    case "tuning":
      return "optimize";
    case "backtests":
      return "backtest";
    case "paper":
    case "trd":
      return "deploy";
    default:
      return null;
  }
}

export const WORKFLOW_ORDER: WorkflowStep[] = [
  "strat",
  "factors",
  "optimize",
  "backtest",
  "deploy",
];

const STEP_TO_PATH: Record<WorkflowStep, string> = {
  strat: "/research/strat",
  factors: "/research/factors",
  optimize: "/research/optimizer",
  backtest: "/trade/backtests",
  deploy: "/trade/paper",
};

export function stepToPath(step: WorkflowStep): string {
  return STEP_TO_PATH[step];
}

const INITIAL: Pick<
  WorkflowState,
  | "template"
  | "factors"
  | "optimization"
  | "backtest"
  | "deployTarget"
  | "tuning"
  | "rsch"
  | "quantLab"
  | "dailyCycle"
> = {
  template: null,
  factors: null,
  optimization: null,
  backtest: null,
  deployTarget: null,
  tuning: null,
  rsch: null,
  quantLab: null,
  dailyCycle: null,
};

export const useWorkflowStore = create<WorkflowState>((set) => ({
  ...INITIAL,

  selectTemplate: (t) =>
    set({
      template: t,
      factors: null,
      optimization: null,
      backtest: null,
      deployTarget: null,
    }),

  setFactors: (factorIds) =>
    set({
      factors: { factorIds, pickedAt: Date.now() },
      optimization: null,
      backtest: null,
      deployTarget: null,
    }),

  setOptimization: (r) =>
    set({
      optimization: r,
      backtest: null,
      deployTarget: null,
    }),

  setBacktest: (b) =>
    set({
      backtest: b,
    }),

  setDeployTarget: (t) =>
    set({
      deployTarget: t,
    }),

  setTuning: (patch) =>
    set((s) => ({
      tuning: { ...(s.tuning ?? ({} as TuningSubState)), ...patch } as TuningSubState,
    })),

  setRsch: (patch) =>
    set((s) => ({
      rsch: { ...(s.rsch ?? { currentId: "" }), ...patch } as RschSubState,
    })),

  setQuantLab: (patch) =>
    set((s) => ({
      quantLab: {
        ...(s.quantLab ?? ({} as QuantLabSubState)),
        ...patch,
      } as QuantLabSubState,
    })),

  setDailyCycle: (patch) =>
    set((s) => ({
      dailyCycle: {
        ...(s.dailyCycle ?? ({} as DailyCycleSubState)),
        ...patch,
      } as DailyCycleSubState,
    })),

  reset: () => set(INITIAL),

  resetFrom: (step) =>
    set(() => {
      const idx = WORKFLOW_ORDER.indexOf(step);
      const out: Partial<WorkflowState> = {};
      if (idx <= WORKFLOW_ORDER.indexOf("factors"))
        out.factors = null;
      if (idx <= WORKFLOW_ORDER.indexOf("optimize"))
        out.optimization = null;
      if (idx <= WORKFLOW_ORDER.indexOf("backtest"))
        out.backtest = null;
      if (idx <= WORKFLOW_ORDER.indexOf("deploy"))
        out.deployTarget = null;
      return out;
    }),
}));