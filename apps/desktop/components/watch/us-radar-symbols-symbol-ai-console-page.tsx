"use client";

import { useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useMarketStore } from "@/lib/store/market";
import { apiPost } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// AI 控制台 — /api/v1/us-radar/symbols/{symbol}/ai-console
// shape: { symbol, thoughts{thesis, key_observations[], conflicting_signals[], resolution, llm_enhanced},
//          alpha{direction, confidence, fusion_score, fusion_breakdown{total_score, contributions[]}, invalidators[], expected_holding_period},
//          execution{suggested_actions[], risk_parameters{}},
//          system{data_mode, provider, missing_data_flags[], evidence_count, last_updated_ms, llm_status},
//          timestamp }
// 按钮:运行因子分析 → POST /api/v1/us-radar/factorize/auto {symbol}

interface AiThoughts {
  thesis?: string;
  key_observations?: string[];
  conflicting_signals?: string[];
  resolution?: string | null;
  llm_enhanced?: boolean;
}
interface AiContribution {
  contributor_type?: string;
  name?: string;
  weight?: number;
  score?: number;
  contribution?: number;
}
interface AiExecutionAction {
  action_type?: string;
  entry_price?: number;
  stop_loss?: number | null;
  take_profit?: number | null;
  position_size_pct?: number;
  rationale?: string;
}
interface AiConsoleData {
  symbol?: string;
  thoughts?: AiThoughts;
  alpha?: {
    direction?: string;
    confidence?: number;
    fusion_score?: number;
    fusion_breakdown?: { total_score?: number; contributions?: AiContribution[] };
    invalidators?: string[];
    expected_holding_period?: string;
  };
  execution?: {
    suggested_actions?: AiExecutionAction[];
    risk_parameters?: { max_position_pct?: number; stop_loss_pct?: number; take_profit_pct?: number };
  };
  system?: {
    data_mode?: string;
    provider?: string;
    missing_data_flags?: string[];
    evidence_count?: number;
    last_updated_ms?: number;
    llm_status?: string;
  };
  timestamp?: number;
}

const bare = (s: string): string => s.replace(/[-/:].*$/, "");

function signalCls(sig?: string): string {
  if (sig === "long" || sig === "bullish" || sig === "positive") return "text-pos";
  if (sig === "short" || sig === "bearish" || sig === "negative") return "text-neg";
  return "text-ink-3";
}

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function SymbolsSymbolAiConsolePage() {
  const { useApi } = useApiPage({ distinguish: true });
  const { pageFilter } = useMarketStore();
  const symbol = bare(pageFilter.symbol) || "BTC";
  const { data, isLoading, mutate } = useApi<AiConsoleData | null>(
    `/api/v1/us-radar/symbols/${symbol}/ai-console`,
  );
  const [refreshing, setRefreshing] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<string | null>(null);
  const d = data ?? null;

  const contributions = d?.alpha?.fusion_breakdown?.contributions ?? [];
  const actions = d?.execution?.suggested_actions ?? [];
  const missingFlags = d?.system?.missing_data_flags ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const handleRunFactorize = async () => {
    if (running) return;
    setRunning(true);
    setRunResult(null);
    try {
      const res = await apiPost<{ symbol?: string; factors?: unknown[]; llm_enhanced?: boolean }>(
        "/api/v1/us-radar/factorize/auto",
        { symbol },
      );
      setRunResult(`已分析 ${res?.symbol ?? symbol} · ${res?.factors?.length ?? 0} 个因子${res?.llm_enhanced ? " · LLM 增强" : ""}`);
    } catch {
      setRunResult("分析失败(需 LLM 配置或后端不可用)");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={`AI 控制台 · ${d?.symbol ?? symbol}`}
        right={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleRunFactorize}
              disabled={running}
              className="flex items-center gap-1 rounded bg-gold/15 px-1.5 py-0.5 text-[9px] text-gold hover:bg-gold/25"
            >
              <Sparkles className={`h-3 w-3 ${running ? "animate-pulse" : ""}`} />
              {running ? "分析中…" : "运行因子分析"}
            </button>
            <span className="text-[9px] text-ink-4 font-mono">{fmtTime(d?.timestamp)}</span>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || isLoading}
              aria-label="刷新"
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="方向" value={d?.alpha?.direction ?? "—"} cls={signalCls(d?.alpha?.direction)} />
          <StatBlock label="置信度" value={d?.alpha?.confidence != null ? `${(d.alpha.confidence * 100).toFixed(0)}%` : "—"} cls="text-gold" />
          <StatBlock label="融合分" value={d?.alpha?.fusion_score != null ? d.alpha.fusion_score.toFixed(2) : "—"} cls="text-ink" />
          <StatBlock label="持仓周期" value={d?.alpha?.expected_holding_period ?? "—"} cls="text-ink-3" />
        </div>
        {runResult && <div className="mt-2 rounded bg-surface-2 px-2 py-1 text-[9px] text-ink-2">{runResult}</div>}
      </V17Card>

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
        <V17Card title="Thoughts">
          <div className="flex flex-col gap-2">
            {d?.thoughts?.thesis && (
              <div className="rounded bg-surface-2 p-2 text-[10px] text-ink-2">
                {d.thoughts.thesis}
                {d.thoughts.llm_enhanced && (
                  <span className="ml-1 rounded bg-purple-400/20 px-1 py-0.5 text-[8px] text-purple-400">LLM</span>
                )}
              </div>
            )}
            {d?.thoughts?.key_observations?.length ? (
              <div>
                <div className="mb-1 text-[9px] text-ink-4">关键观察</div>
                {d.thoughts.key_observations.map((o, i) => (
                  <div key={i} className="border-b border-border-soft py-1 text-[10px] text-ink-3">• {o}</div>
                ))}
              </div>
            ) : null}
            {d?.thoughts?.conflicting_signals?.length ? (
              <div>
                <div className="mb-1 text-[9px] text-ink-4">矛盾信号</div>
                {d.thoughts.conflicting_signals.map((o, i) => (
                  <div key={i} className="border-b border-border-soft py-1 text-[10px] text-neg">• {o}</div>
                ))}
              </div>
            ) : null}
            {d?.thoughts?.resolution && (
              <div className="text-[10px] text-ink-2">
                <span className="text-ink-4">化解 · </span>{d.thoughts.resolution}
              </div>
            )}
          </div>
        </V17Card>

        <V17Card title="Alpha 分解">
          {contributions.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          ) : (
            <div className="flex flex-col">
              {contributions.map((c, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                  <span className="w-20 truncate text-ink-3">{c.contributor_type}</span>
                  <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
                    <div className={`h-full ${(c.contribution ?? 0) >= 0 ? "bg-pos" : "bg-neg"}`} style={{ width: `${Math.min(100, Math.abs(c.contribution ?? 0) * 1000)}%` }} />
                  </div>
                  <span className={`w-12 text-right font-mono num ${(c.contribution ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                    {(c.contribution ?? 0).toFixed(3)}
                  </span>
                  <span className="w-8 text-right font-mono num text-ink-3">{c.score ?? 0}</span>
                </div>
              ))}
            </div>
          )}
        </V17Card>
      </div>

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
        <V17Card title="执行建议">
          {actions.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无建议</div>
          ) : (
            <div className="flex flex-col">
              {actions.map((a, i) => (
                <div key={i} className="border-b border-border-soft py-1.5 text-[10px]">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[9px] text-ink-2">{a.action_type}</span>
                    <span className="font-mono text-ink">{a.entry_price?.toFixed(2) ?? "—"}</span>
                    <div className="flex-1" />
                    <span className="text-[9px] text-ink-4">
                      仓位 {a.position_size_pct ?? 0}% · SL {a.stop_loss?.toFixed(2) ?? "—"} · TP {a.take_profit?.toFixed(2) ?? "—"}
                    </span>
                  </div>
                  {a.rationale && <div className="mt-0.5 text-[9px] text-ink-4">{a.rationale}</div>}
                </div>
              ))}
            </div>
          )}
        </V17Card>

        <V17Card title="系统状态">
          <div className="flex flex-col gap-1.5 text-[10px]">
            <div className="flex justify-between"><span className="text-ink-3">数据模式</span><span className="font-mono text-ink">{d?.system?.data_mode ?? "—"}</span></div>
            <div className="flex justify-between"><span className="text-ink-3">供应商</span><span className="font-mono text-ink">{d?.system?.provider ?? "—"}</span></div>
            <div className="flex justify-between"><span className="text-ink-3">证据数</span><span className="font-mono text-ink">{d?.system?.evidence_count ?? 0}</span></div>
            <div className="flex justify-between"><span className="text-ink-3">LLM</span><span className={`font-mono ${d?.system?.llm_status === "configured" ? "text-pos" : "text-neg"}`}>{d?.system?.llm_status ?? "—"}</span></div>
            {missingFlags.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {missingFlags.map((f) => (
                  <span key={f} className="rounded bg-neg/15 px-1.5 py-0.5 text-[9px] text-neg">{f}</span>
                ))}
              </div>
            )}
          </div>
        </V17Card>
      </div>
    </div>
  );
}
