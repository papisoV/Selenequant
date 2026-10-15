"use client";

import { useState, useEffect } from "react";
import { TextareaHTMLAttributes } from "react";

// Inline Textarea — no UI primitive exists in apps/desktop
type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;
function Textarea({ className = "", ...props }: TextareaProps) {
  return (
    <textarea
      {...props}
      className={`min-h-[80px] w-full rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-3 py-2 text-xs text-[color:var(--color-text)] focus:outline-none focus:ring-1 focus:ring-[color:var(--color-accent)] ${className}`}
    />
  );
}

interface ProviderInfo {
  provider: string;
  is_running: boolean;
  healthy: boolean | null;
  kind: string;
}

interface ActionInfo {
  action: string;
  description: string;
}

interface AgentStudioResponse {
  provider: string;
  kind: string;
  title: string;
  metrics: Record<string, unknown>;
  artifacts: Record<string, unknown> | null;
  computed_at: string;
  notes: string | null;
}

interface PromptPack {
  name: string;
  label: string;
  description: string;
  agent_count: number;
  agent_ids: string[];
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

export function AgentStudioPanel() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string>("");
  const [actions, setActions] = useState<ActionInfo[]>([]);
  const [selectedAction, setSelectedAction] = useState<string>("");
  const [task, setTask] = useState<string>("Find mean-reversion alpha factors in US tech sector");
  const [params, setParams] = useState<string>('{"loop_n": 1, "target_market": "US"}');
  const [packs, setPacks] = useState<PromptPack[]>([]);
  const [selectedPack, setSelectedPack] = useState<string>("economic");
  const [report, setReport] = useState<AgentStudioResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  // Load providers on mount
  useEffect(() => {
    apiFetch<ProviderInfo[]>("/api/v1/agentstudio/providers")
      .then((data) => {
        setProviders(data);
        if (data.length > 0 && !selectedProvider) {
          setSelectedProvider(data[0].provider);
        }
      })
      .catch((e: unknown) => setError(String(e)));
    apiFetch<PromptPack[]>("/api/v1/agentstudio/prompt-packs")
      .then(setPacks)
      .catch((e: unknown) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load actions when provider changes
  useEffect(() => {
    if (!selectedProvider) return;
    apiFetch<ActionInfo[]>(`/api/v1/agentstudio/${selectedProvider}/actions`)
      .then((data) => {
        setActions(data);
        if (data.length > 0) setSelectedAction(data[0].action);
      })
      .catch((e: unknown) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProvider]);

  async function runCompute() {
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      let body: Record<string, unknown> = {
        provider: selectedProvider,
        action: selectedAction,
        task,
        params: params ? JSON.parse(params) : null,
      };
      const r = await apiFetch<AgentStudioResponse>("/api/v1/agentstudio/report", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setReport(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function loadPack() {
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch<AgentStudioResponse>("/api/v1/agentstudio/prompt-pack", {
        method: "POST",
        body: JSON.stringify({ pack: selectedPack }),
      });
      setReport(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const rdLoopProviders = providers.filter((p) => p.kind === "rd_loop");
  const promptProviders = providers.filter((p) => p.kind === "prompt_library");

  return (
    <div className="flex h-full flex-col gap-3 overflow-auto p-3">
      <div className="text-sm font-semibold text-[color:var(--color-text)]">Agent Studio</div>
      <div className="text-xs text-[color:var(--color-text-muted)]">
        Multi-agent R&D loops + domain prompt library (inspired by commercial quant terminals (clean-room reimplementation))
      </div>

      {/* Provider Grid */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
            R&D Loops (7)
          </div>
          <div className="grid grid-cols-2 gap-1">
            {rdLoopProviders.map((p) => (
              <button
                key={p.provider}
                onClick={() => setSelectedProvider(p.provider)}
                className={`rounded border px-2 py-1 text-left text-[11px] ${
                  selectedProvider === p.provider
                    ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-soft)]"
                    : "border-[color:var(--color-border)] bg-[color:var(--color-bg)]"
                }`}
              >
                <div className="font-mono text-[color:var(--color-text)]">{p.provider}</div>
                <div className="text-[10px] text-[color:var(--color-text-muted)]">
                  {p.healthy === null ? "—" : p.healthy ? "healthy" : "unavailable"}
                </div>
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
            Prompt Library (1)
          </div>
          <div className="grid grid-cols-1 gap-1">
            {promptProviders.map((p) => (
              <button
                key={p.provider}
                onClick={() => setSelectedProvider(p.provider)}
                className={`rounded border px-2 py-1 text-left text-[11px] ${
                  selectedProvider === p.provider
                    ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-soft)]"
                    : "border-[color:var(--color-border)] bg-[color:var(--color-bg)]"
                }`}
              >
                <div className="font-mono text-[color:var(--color-text)]">{p.provider}</div>
                <div className="text-[10px] text-[color:var(--color-text-muted)]">
                  {packs.length} packs available
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Action Selector */}
      {actions.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
            Action
          </div>
          <div className="flex flex-wrap gap-1">
            {actions.map((a) => (
              <button
                key={a.action}
                onClick={() => setSelectedAction(a.action)}
                title={a.description}
                className={`rounded border px-2 py-1 text-[11px] ${
                  selectedAction === a.action
                    ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-soft)]"
                    : "border-[color:var(--color-border)] bg-[color:var(--color-bg)]"
                }`}
              >
                {a.action}
              </button>
            ))}
          </div>
          {selectedAction && (
            <div className="mt-1 text-[10px] text-[color:var(--color-text-muted)]">
              {actions.find((a) => a.action === selectedAction)?.description}
            </div>
          )}
        </div>
      )}

      {/* Task + Params inputs */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
            Task description
          </div>
          <Textarea value={task} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setTask(e.target.value)} />
        </div>
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
            Params (JSON)
          </div>
          <Textarea
            value={params}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setParams(e.target.value)}
            className="font-mono"
          />
        </div>
      </div>

      {/* Run button */}
      <button
        onClick={runCompute}
        disabled={loading || !selectedProvider || !selectedAction}
        className="self-start rounded-md bg-[color:var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-bg)] disabled:opacity-50"
      >
        {loading ? "Running..." : "Run"}
      </button>

      {/* Prompt pack selector (always visible for prompt_library provider) */}
      {selectedProvider === "prompt_library" && (
        <div className="border-t border-[color:var(--color-border)] pt-2">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
            Prompt Pack
          </div>
          <div className="flex flex-wrap gap-1">
            {packs.map((p) => (
              <button
                key={p.name}
                onClick={() => setSelectedPack(p.name)}
                className={`rounded border px-2 py-1 text-[11px] ${
                  selectedPack === p.name
                    ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-soft)]"
                    : "border-[color:var(--color-border)] bg-[color:var(--color-bg)]"
                }`}
                title={p.description}
              >
                {p.label} ({p.agent_count})
              </button>
            ))}
          </div>
          <button
            onClick={loadPack}
            disabled={loading}
            className="mt-2 self-start rounded-md bg-[color:var(--color-accent)] px-3 py-1 text-xs font-medium text-[color:var(--color-bg)] disabled:opacity-50"
          >
            Load Pack
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-md border border-red-500/50 bg-red-500/10 p-2 text-xs text-red-400">
          {error}
        </div>
      )}

      {/* Report */}
      {report && (
        <div className="border-t border-[color:var(--color-border)] pt-2">
          <div className="text-xs font-semibold text-[color:var(--color-text)]">
            {report.title}
          </div>
          <div className="mb-2 text-[10px] text-[color:var(--color-text-muted)]">
            {report.provider} · {report.kind} · {new Date(report.computed_at).toLocaleString()}
            {report.notes ? ` · ${report.notes}` : ""}
          </div>

          {/* Metrics */}
          {Object.keys(report.metrics as Record<string, unknown>).length > 0 ? (
            <div className="mb-2">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
                Metrics
              </div>
              <div className="grid grid-cols-3 gap-2">
                {Object.entries(report.metrics as Record<string, unknown>).slice(0, 12).map(([k, v]) => (
                  <MetricBlock key={k} label={k} value={v} />
                ))}
              </div>
            </div>
          ) : null}

          {/* Artifacts (prompt pack agents) */}
          {report.artifacts && report.artifacts.agents && Array.isArray(report.artifacts.agents) ? (
            <div className="mb-2">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
                Agents ({(report.artifacts.agents as unknown[]).length})
              </div>
              <div className="max-h-[300px] overflow-auto rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-bg)] p-2">
                <pre className="text-[10px] text-[color:var(--color-text)]">
                  {JSON.stringify(report.artifacts.agents, null, 2)}
                </pre>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
