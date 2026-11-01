"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card } from "@/components/shared/page-filter-bar";
import { api } from "@/lib/api-client";
import { toastSuccess, toastError } from "@/lib/toast";
import { Loader2, Activity } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

interface ProviderInfo {
  name?: string;
  label?: string;
  description?: string;
  healthy?: boolean;
  [k: string]: unknown;
}

async function fetchProviders(): Promise<ProviderInfo[]> {
  try {
    return await api<ProviderInfo[]>("/api/v1/agentstudio/providers");
  } catch {
    return [];
  }
}

async function fetchDailyHealthcheck(): Promise<Record<string, unknown> | null> {
  try {
    return await api<Record<string, unknown>>("/api/v1/daily-cycle/healthcheck");
  } catch {
    return null;
  }
}

async function checkProvider(provider: string): Promise<Record<string, unknown>> {
  const res = await fetch(`/api/v1/agentstudio/${encodeURIComponent(provider)}/healthcheck`);
  if (!res.ok) throw new Error(`healthcheck failed: ${res.status}`);
  const body = await res.json();
  return (body?.data ?? body) as Record<string, unknown>;
}

export function HealthcheckPanel() {
  const tr = useUiText()

  const { data: providers } = useSWR("/api/v1/agentstudio/providers", fetchProviders, {
    refreshInterval: 60000,
  });
  const { data: dailyHc } = useSWR("/api/v1/daily-cycle/healthcheck", fetchDailyHealthcheck, {
    refreshInterval: 60000,
  });

  const [checking, setChecking] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, Record<string, unknown>>>({});

  async function doCheck(provider: string) {
    setChecking(provider);
    try {
      const r = await checkProvider(provider);
      setResult((prev) => ({ ...prev, [provider]: r }));
      const ok = r.healthy ?? r.ok ?? r.status === "ok";
      if (ok) toastSuccess(`${provider} healthy`);
      else toastError(`${provider} unhealthy: ${JSON.stringify(r).slice(0, 100)}`);
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setChecking(null);
    }
  }

  const providerList = providers ?? [];

  return (
    <V17Card title={tr("Healthchecks (健康检查)")}>
      <div className="grid grid-cols-2 gap-2 mb-2">
        <div className="border border-border-soft rounded p-1.5">
          <div className="text-[9px] text-ink-4">Daily Cycle</div>
          {dailyHc == null ? (
            <div className="text-[10px] text-ink-4">{tr("不可用")}</div>
          ) : (
            <pre className="text-[9px] text-ink-3 font-mono max-h-16 overflow-auto">
              {JSON.stringify(dailyHc, null, 2).slice(0, 300)}
            </pre>
          )}
        </div>
        <div className="border border-border-soft rounded p-1.5">
          <div className="text-[9px] text-ink-4">AgentStudio Providers</div>
          <div className="text-[10px] text-gold font-mono">{providerList.length} {tr("个")}</div>
        </div>
      </div>

      <div className="flex flex-col">
        {providerList.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-2">{tr("暂无 providers")}</div>
        )}
        {providerList.map((p, i) => {
          const name = String(p.name ?? `provider-${i}`);
          const healthy = p.healthy;
          const r = result[name];
          return (
            <div
              key={name}
              className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft"
            >
              <span
                className={`w-10 flex-shrink-0 font-mono ${
                  healthy === true ? "text-pos" : healthy === false ? "text-neg" : "text-ink-4"
                }`}
              >
                {healthy === true ? "OK" : healthy === false ? "BAD" : "—"}
              </span>
              <span className="w-24 flex-shrink-0 text-gold font-mono truncate" title={name}>
                {name}
              </span>
              <span className="flex-1 text-ink-3 truncate" title={p.description ?? ""}>
                {p.label ?? p.description ?? "—"}
              </span>
              <button
                onClick={() => doCheck(name)}
                disabled={checking === name}
                className="rounded bg-surface-3 text-ink-3 px-1.5 py-0.5 text-[9px] hover:bg-sky-500/15 hover:text-sky-400 flex items-center gap-0.5"
                title={tr("运行 healthcheck")}
              >
                {checking === name ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <Activity className="h-2.5 w-2.5" />}
                {tr("检查")}
              </button>
              {r && (
                <pre className="text-[8px] text-ink-4 font-mono max-w-32 truncate" title={JSON.stringify(r)}>
                  {JSON.stringify(r).slice(0, 60)}
                </pre>
              )}
            </div>
          );
        })}
      </div>
    </V17Card>
  );
}
