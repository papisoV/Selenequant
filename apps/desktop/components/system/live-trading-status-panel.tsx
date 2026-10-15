"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

interface WsHealth {
  is_healthy: boolean;
  heartbeat_age_s: number | null;
  reconnect_count_1h: number;
  reason: string | null;
}

interface KillswitchState {
  is_active: boolean;
  reason: string;
  activated_at: number | null;
}

interface AuditChainHealth {
  available: boolean;
  entries: number;
  chain_ok: boolean;
  latest_action: string | null;
}

interface LiveStatus {
  ws_health: WsHealth;
  wal_lag: number;
  killswitch_state: KillswitchState;
  reconciliation_diff_count: number;
  shadow_mode_diff: number;
  daily_pnl: number | null;
  peak_equity: number | null;
  audit_chain_health?: AuditChainHealth;
}

function healthColor(ws: WsHealth | undefined): string {
  if (!ws) return "text-ink-3";
  if (!ws.is_healthy) return "text-neg";
  if (ws.heartbeat_age_s != null && ws.heartbeat_age_s > 2) return "text-gold";
  return "text-pos";
}

function walLagColor(lag: number): string {
  if (lag > 5) return "text-neg";
  if (lag > 0) return "text-gold";
  return "text-pos";
}

function diffColor(n: number): string {
  return n > 0 ? "text-neg" : "text-pos";
}

function fmtAge(s: number | null): string {
  if (s == null) return "—";
  return `${s.toFixed(1)}s`;
}

function fmtTs(ts: number | null): string {
  if (ts == null) return "—";
  return new Date(ts * 1000).toLocaleTimeString();
}

export function LiveTradingStatusPanel() {
  const { data, isLoading } = useApi<LiveStatus>("/api/v1/live/status", {
    refreshInterval: 5000,
  });

  if (isLoading) {
    return (
      <V17Card title="实盘可观测性 (Phase 5)">
        <div className="text-[11px] text-ink-3 py-2 animate-pulse">加载中…</div>
      </V17Card>
    );
  }

  const s = data ?? null;
  const ws = s?.ws_health;
  const ks = s?.killswitch_state;
  const walLag = s?.wal_lag ?? 0;
  const reconDiff = s?.reconciliation_diff_count ?? 0;
  const shadowDiff = s?.shadow_mode_diff ?? 0;

  return (
    <V17Card title="实盘可观测性 (Phase 5)">
      <div className="grid grid-cols-4 gap-2">
        <StatBlock
          label="WS 健康"
          value={ws ? (ws.is_healthy ? "OK" : "BAD") : "—"}
          cls={healthColor(ws)}
        />
        <StatBlock
          label="心跳延迟"
          value={fmtAge(ws?.heartbeat_age_s ?? null)}
          cls={healthColor(ws)}
        />
        <StatBlock
          label="WS 重连/h"
          value={ws?.reconnect_count_1h ?? 0}
          cls={(ws?.reconnect_count_1h ?? 0) > 5 ? "text-neg" : "text-pos"}
        />
        <StatBlock
          label="WAL 滞后"
          value={walLag}
          cls={walLagColor(walLag)}
        />
        <StatBlock
          label="Kill Switch"
          value={ks?.is_active ? "ON" : "OFF"}
          cls={ks?.is_active ? "text-neg" : "text-pos"}
        />
        <StatBlock
          label="对账 diff"
          value={reconDiff}
          cls={diffColor(reconDiff)}
        />
        <StatBlock
          label="Shadow diff"
          value={shadowDiff}
          cls={diffColor(shadowDiff)}
        />
        <StatBlock
          label="daily_pnl"
          value={s?.daily_pnl == null ? "Phase 2a" : s.daily_pnl.toFixed(2)}
          cls="text-ink-3"
        />
      </div>
      <div className="grid grid-cols-2 gap-2 mt-2">
        <StatBlock
          label="peak_equity"
          value={s?.peak_equity == null ? "Phase 2a" : s.peak_equity.toFixed(2)}
          cls="text-ink-3"
        />
        <div className="flex flex-col text-[10px] gap-1">
          <div className="flex justify-between border-b border-border-soft py-1">
            <span className="text-ink-3">WS reason</span>
            <span className="font-mono text-ink-2">
              {ws?.reason ?? "—"}
            </span>
          </div>
          <div className="flex justify-between border-b border-border-soft py-1">
            <span className="text-ink-3">KS reason</span>
            <span className="font-mono text-ink-2">
              {ks?.reason || "—"}
            </span>
          </div>
          <div className="flex justify-between py-1">
            <span className="text-ink-3">KS activated</span>
            <span className="font-mono text-ink-2">
              {fmtTs(ks?.activated_at ?? null)}
            </span>
          </div>
          <div className="flex justify-between border-t border-border-soft pt-1">
            <span className="text-ink-3">审计链</span>
            <span
              className={`font-mono ${
                s?.audit_chain_health?.chain_ok === false
                  ? "text-neg"
                  : "text-ink-2"
              }`}
            >
              {s?.audit_chain_health?.available
                ? `${s.audit_chain_health.entries} 条 · ${
                    s.audit_chain_health.chain_ok ? "链完整" : "链损坏"
                  }`
                : "无记录"}
            </span>
          </div>
        </div>
      </div>
    </V17Card>
  );
}
