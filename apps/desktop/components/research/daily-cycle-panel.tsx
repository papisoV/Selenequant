"use client";

import { useState } from "react";
import { useWorkflowStore } from "@/lib/store/workflow";

interface PhaseInfo {
  name: string;
  status: string;
  disposition: string | null;
  error: string | null;
  metrics: Record<string, unknown>;
  artifacts: Record<string, unknown>;
}

interface WorkflowResultResponse {
  workflow_id: string;
  trading_date: string;
  status: string;
  halted_at: string | null;
  started_at: string;
  ended_at: string;
  phases: PhaseInfo[];
}

interface WorkflowInfo {
  provider: string;
  kind: string;
  phases: string[];
  ic_decision_providers: string[];
}

interface Healthcheck {
  provider: string;
  healthy: boolean;
  is_running: boolean;
  renintel_available: boolean;
  agentstudio_available: boolean;
}

const PHASE_ORDER = ["pre_market", "risk_review", "ic_decision", "execution", "post_market"];

const STATUS_COLORS: Record<string, string> = {
  success: "border-green-500/50 bg-green-500/10 text-green-400",
  failed: "border-red-500/50 bg-red-500/10 text-red-400",
  halted: "border-yellow-500/50 bg-yellow-500/10 text-yellow-400",
  skipped: "border-[color:var(--color-border)] bg-[color:var(--color-bg)] text-[color:var(--color-text-muted)]",
  pending: "border-[color:var(--color-border)] bg-[color:var(--color-bg)] text-[color:var(--color-text-muted)]",
  running: "border-blue-500/50 bg-blue-500/10 text-blue-400",
};

function statusClass(status: string): string {
  return STATUS_COLORS[status] ?? STATUS_COLORS.pending;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

function MetricBlock({ label, value }: { label: string; value: unknown }) {
  const display: string = typeof value === "object" && value !== null
    ? JSON.stringify(value, null, 2)
    : String(value);
  return (
    <div className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-bg)] p-2">
      <div className="text-[10px] uppercase tracking-wide text-[color:var(--color-text-muted)]">{label}</div>
      <div className="mt-1 font-mono text-xs text-[color:var(--color-text)] break-all">{display}</div>
    </div>
  );
}

export function DailyCyclePanel() {
  const today = new Date().toISOString().slice(0, 10);
  const dailyCycle = useWorkflowStore((s) => s.dailyCycle);
  const setDailyCycle = useWorkflowStore((s) => s.setDailyCycle);
  const tradingDate: string = dailyCycle?.tradingDate ?? today;
  const setTradingDate = (v: string) => setDailyCycle({ tradingDate: v });
  const icProvider: string = dailyCycle?.icProvider ?? "persona";
  const setIcProvider = (v: string) => setDailyCycle({ icProvider: v });
  const [info, setInfo] = useState<WorkflowInfo | null>(null);
  const [health, setHealth] = useState<Healthcheck | null>(null);
  const result: WorkflowResultResponse | null = (dailyCycle?.result ?? null) as WorkflowResultResponse | null;
  const setResult = (v: WorkflowResultResponse | null) => setDailyCycle({ result: v });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  async function loadInfo() {
    try {
      const [i, h] = await Promise.all([
        apiFetch<WorkflowInfo>("/api/v1/daily-cycle/info"),
        apiFetch<Healthcheck>("/api/v1/daily-cycle/healthcheck"),
      ]);
      setInfo(i);
      setHealth(h);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function runWorkflow() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await apiFetch<WorkflowResultResponse>("/api/v1/daily-cycle/run", {
        method: "POST",
        body: JSON.stringify({
          trading_date: tradingDate,
          ic_decision_provider: icProvider,
        }),
      });
      setResult(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  // Load info on mount
  useState(() => {
    loadInfo();
  });

  return (
    <div className="flex h-full flex-col gap-3 overflow-auto p-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-[color:var(--color-text)]">Daily Trading Cycle</div>
          <div className="text-xs text-[color:var(--color-text-muted)]">
            5-phase workflow: Pre-Market / Risk Review / IC Decision / Execution / Post-Market
          </div>
        </div>
        <span
          className={`rounded border px-2 py-0.5 text-[10px] ${
            health?.healthy
              ? "border-green-500/50 bg-green-500/10 text-green-400"
              : "border-[color:var(--color-border)] bg-[color:var(--color-bg)] text-[color:var(--color-text-muted)]"
          }`}
        >
          {health?.healthy ? "healthy" : "—"}
        </span>
      </div>

      {health && (
        <div className="flex gap-3 text-[10px] text-[color:var(--color-text-muted)]">
          <span>renintel: {health.renintel_available ? "yes" : "no"}</span>
          <span>agentstudio: {health.agentstudio_available ? "yes" : "no"}</span>
        </div>
      )}

      <div className="border-t border-[color:var(--color-border)] pt-2">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
          Run Workflow
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
              Trading Date
            </label>
            <input
              type="date"
              value={tradingDate}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTradingDate(e.target.value)}
              className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 text-xs text-[color:var(--color-text)]"
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
              IC Decision Provider
            </label>
            <select
              value={icProvider}
              onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setIcProvider(e.target.value)}
              className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 text-xs text-[color:var(--color-text)]"
            >
              <option value="persona">persona (renintel)</option>
              <option value="rd_loop">rd_loop (agentstudio)</option>
            </select>
          </div>
          <button
            onClick={runWorkflow}
            disabled={loading || !tradingDate}
            className="self-start rounded-md bg-[color:var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-bg)] disabled:opacity-50"
          >
            {loading ? "Running..." : "Run"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-500/50 bg-red-500/10 p-2 text-xs text-red-400">
          {error}
        </div>
      )}

      {result && (
        <div className="border-t border-[color:var(--color-border)] pt-2">
          <div className="mb-2 flex items-center justify-between">
            <div className="text-xs font-semibold text-[color:var(--color-text)]">
              Workflow Result
            </div>
            <span className={`rounded border px-2 py-0.5 text-[10px] ${statusClass(result.status)}`}>
              {result.status}
              {result.halted_at ? ` · halted @ ${result.halted_at}` : ""}
            </span>
          </div>
          <div className="mb-2 text-[10px] text-[color:var(--color-text-muted)]">
            {result.workflow_id} · {new Date(result.started_at).toLocaleString()} → {new Date(result.ended_at).toLocaleString()}
          </div>

          {/* Phase timeline */}
          <div className="mb-3">
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
              Phase Timeline
            </div>
            <div className="flex flex-col gap-1">
              {PHASE_ORDER.map((phaseName) => {
                const phase = result.phases.find((p) => p.name === phaseName);
                const status = phase?.status ?? "pending";
                const orderedPhases = result.phases;
                const orderIdx = orderedPhases.findIndex((p) => p.name === phaseName);
                return (
                  <div
                    key={phaseName}
                    className={`rounded border px-2 py-1 text-[11px] ${statusClass(status)}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono">{phaseName}</span>
                      <span className="text-[10px]">{status}{phase?.disposition ? ` · ${phase.disposition}` : ""}</span>
                    </div>
                    {orderIdx >= 0 && phase?.error && (
                      <div className="mt-1 text-[10px] text-red-400">{phase.error}</div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Metrics */}
          {result.phases.some((p) => Object.keys(p.metrics).length > 0) && (
            <div className="mb-2">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
                Phase Metrics
              </div>
              <div className="grid grid-cols-3 gap-2">
                {result.phases.flatMap((p) =>
                  Object.entries(p.metrics).slice(0, 4).map(([k, v]) => (
                    <MetricBlock key={`${p.name}.${k}`} label={`${p.name}.${k}`} value={v} />
                  ))
                )}
              </div>
            </div>
          )}

          {/* Artifacts */}
          {result.phases.some((p) => Object.keys(p.artifacts).length > 0) && (
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
                Phase Artifacts
              </div>
              <div className="max-h-[400px] overflow-auto rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-bg)] p-2">
                <pre className="text-[10px] text-[color:var(--color-text)]">
                  {JSON.stringify(
                    Object.fromEntries(
                      result.phases.map((p) => [p.name, p.artifacts])
                    ),
                    null,
                    2
                  )}
                </pre>
              </div>
            </div>
          )}
        </div>
      )}

      {info && !result && (
        <div className="text-[10px] text-[color:var(--color-text-muted)]">
          Phases: {info.phases.join(" / ")}
        </div>
      )}
    </div>
  );
}
