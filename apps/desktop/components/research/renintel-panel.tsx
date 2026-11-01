"use client";

import { useState, useEffect } from "react";
import { TextareaHTMLAttributes } from "react";

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

interface RenIntelResponse {
  provider: string;
  kind: string;
  title: string;
  metrics: Record<string, unknown>;
  artifacts: Record<string, unknown> | null;
  computed_at: string;
  notes: string | null;
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

export function RenIntelPanel() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string>("");
  const [actions, setActions] = useState<ActionInfo[]>([]);
  const [selectedAction, setSelectedAction] = useState<string>("");
  const [params, setParams] = useState<string>('{"id": "quant_researcher"}');
  const [report, setReport] = useState<RenIntelResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  useEffect(() => {
    apiFetch<ProviderInfo[]>("/api/v1/renintel/providers")
      .then((data) => {
        setProviders(data);
        if (data.length > 0 && !selectedProvider) {
          setSelectedProvider(data[0].provider);
        }
      })
      .catch((e: unknown) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedProvider) return;
    apiFetch<ActionInfo[]>(`/api/v1/renintel/${selectedProvider}/actions`)
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
      const body: Record<string, unknown> = {
        provider: selectedProvider,
        action: selectedAction,
        params: params ? JSON.parse(params) : null,
      };
      const r = await apiFetch<RenIntelResponse>("/api/v1/renintel/report", {
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

  return (
    <div className="flex h-full flex-col gap-3 overflow-auto p-3">
      <div className="text-sm font-semibold text-[color:var(--color-text)]">Renaissance Intel</div>
      <div className="text-xs text-[color:var(--color-text-muted)]">
        5 Renaissance libraries: personas / reasoning / guardrails / strategies / memory (inspired by commercial quant terminals (clean-room reimplementation))
      </div>

      <div>
        <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
          Libraries (5)
        </div>
        <div className="grid grid-cols-5 gap-1">
          {providers.map((p) => (
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
                {p.healthy === null ? "—" : p.healthy ? "healthy" : "down"}
              </div>
            </button>
          ))}
        </div>
      </div>

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

      <button
        onClick={runCompute}
        disabled={loading || !selectedProvider || !selectedAction}
        className="self-start rounded-md bg-[color:var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-bg)] disabled:opacity-50"
      >
        {loading ? "Running..." : "Run"}
      </button>

      {error && (
        <div className="rounded-md border border-red-500/50 bg-red-500/10 p-2 text-xs text-red-400">
          {error}
        </div>
      )}

      {report && (
        <div className="border-t border-[color:var(--color-border)] pt-2">
          <div className="text-xs font-semibold text-[color:var(--color-text)]">
            {report.title}
          </div>
          <div className="mb-2 text-[10px] text-[color:var(--color-text-muted)]">
            {report.provider} · {report.kind} · {new Date(report.computed_at).toLocaleString()}
            {report.notes ? ` · ${report.notes}` : ""}
          </div>

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

          {report.artifacts ? (
            <div className="mb-2">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
                Artifacts
              </div>
              <div className="max-h-[400px] overflow-auto rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-bg)] p-2">
                <pre className="text-[10px] text-[color:var(--color-text)]">
                  {JSON.stringify(report.artifacts, null, 2)}
                </pre>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
