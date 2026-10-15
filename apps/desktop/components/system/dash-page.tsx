"use client";

import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { LiveTradingStatusPanel } from "./live-trading-status-panel";
import { LiveEngineControlPanel } from "./live-engine-control-panel";
import { EventsPanel } from "./events-panel";
import { SystemLogsPanel } from "./system-logs-panel";
import { AgentReasoningPanel } from "./agent-reasoning-panel";
import { PublicKeyPanel } from "./public-key-panel";
import { RunnerStatusPanel } from "./runner-status-panel";
import { IntelligenceAlertsPanel } from "./intelligence-alerts-panel";
import { KillSwitchPanel } from "./kill-switch-panel";
import { AccountPanel } from "./account-panel";
import { ShadowProfilePanel } from "./shadow-profile-panel";
import { HealthcheckPanel } from "./healthcheck-panel";

interface DashboardMarketSummary {
  symbol: string;
  last_price: number | null;
  change_24h_pct: number | null;
  volume_24h: number | null;
}

interface DashboardPaperSummary {
  account_id: string;
  equity: number;
  unrealized_pnl: number;
  open_positions: number;
}

interface DashboardTradingSummary {
  mode: string;
  open_orders: number;
  open_positions: number;
  kill_switch: boolean;
}

interface DashboardSignalsSummary {
  total: number;
  high_severity: number;
  medium_severity: number;
  low_severity: number;
}

interface DashboardSnapshot {
  ts: number;
  markets: DashboardMarketSummary[];
  paper: DashboardPaperSummary | null;
  trading: DashboardTradingSummary | null;
  signals: DashboardSignalsSummary | null;
  slow_subcalls: string[];
}

const fmt_price = (n: number | null | undefined) =>
  n == null ? "—" : n >= 1 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toFixed(4);

const fmt_pct = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

const fmt_vol = (n: number | null | undefined) => {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
  return n.toFixed(2);
};

const fmt_ts = (ms: number) => {
  try {
    const d = new Date(ms);
    return d.toLocaleTimeString();
  } catch {
    return "—";
  }
};

// P1-5:分区标 — 只锚定区段起点,面板顺序一字不动(军规④空间记忆)。
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="col-span-full mt-1 flex items-baseline gap-2" aria-hidden>
      <span className="text-[9px] font-semibold tracking-widest text-ink-4">{children}</span>
      <span className="h-px flex-1 bg-border-soft" />
    </div>
  );
}

export function DashPage() {
  const { data, isLoading } = useApi<DashboardSnapshot>("/api/v1/dashboard");

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-ink-3">
        <div className="animate-pulse">加载中…</div>
      </div>
    );
  }

  const snap = data ?? null;
  const markets = snap?.markets ?? [];
  const trading = snap?.trading ?? null;
  const paper = snap?.paper ?? null;
  const signals = snap?.signals ?? null;
  const slow = snap?.slow_subcalls ?? [];

  return (
    <div className="v17-page-grid grid gap-2">
      {data === null && (
        <div className="rounded border border-gold/40 bg-gold/15 px-2 py-1 text-[10px] text-gold">
          后端不可达或暂无可显示数据 — 以下为占位值。原因见下方系统日志面板,或检查后端 localhost:8088 是否启动。
        </div>
      )}
      <V17Card title="系统总览">
        <div className="grid grid-cols-4 gap-2">
          <StatBlock label="运行模式" value={trading?.mode ?? "—"} />
          <StatBlock
            label="当前持仓"
            value={trading?.open_positions ?? 0}
            cls="text-pos"
          />
          <StatBlock label="挂单" value={trading?.open_orders ?? 0} cls="text-gold" />
          <StatBlock
            label="Kill Switch"
            value={trading?.kill_switch ? "ON" : "OFF"}
            cls={trading?.kill_switch ? "text-neg" : "text-pos"}
          />
        </div>
        <div className="grid grid-cols-4 gap-2 mt-2">
          <StatBlock
            label="模拟账户"
            value={paper ? (paper.account_id ?? '').slice(0, 8) : "—"}
          />
          <StatBlock
            label="账户净值"
            value={paper ? `${paper.equity?.toFixed(2) ?? '—'}` : "—"}
            cls="text-pos"
          />
          <StatBlock
            label="未实现盈亏"
            value={paper ? `${paper.unrealized_pnl?.toFixed(2) ?? '—'}` : "—"}
            cls={paper && paper.unrealized_pnl != null && paper.unrealized_pnl >= 0 ? "text-pos" : "text-neg"}
          />
          <StatBlock
            label="模拟持仓数"
            value={paper?.open_positions ?? 0}
          />
        </div>
      </V17Card>

      <LiveTradingStatusPanel />
      <LiveEngineControlPanel />
      <EventsPanel />
      <RunnerStatusPanel />
      <SectionLabel>AI 活动</SectionLabel>
      <SystemLogsPanel />
      <AgentReasoningPanel />
      <SectionLabel>系统与风险</SectionLabel>
      <IntelligenceAlertsPanel />
      <KillSwitchPanel />
      <AccountPanel />
      <ShadowProfilePanel />
      <HealthcheckPanel />
      <PublicKeyPanel />

      <V17Card title="市场行情 (订阅实时数据)">
        {markets.length === 0 ? (
          <div className="text-[11px] text-ink-3 py-2">
            暂无订阅行情。前往 watch/mk 页订阅 symbol。
          </div>
        ) : (
          <div className="flex flex-col">
            {markets.map((m) => {
              const chgCls =
                m.change_24h_pct == null
                  ? "text-ink-3"
                  : m.change_24h_pct >= 0
                    ? "text-pos"
                    : "text-neg";
              return (
                <div
                  key={m.symbol}
                  className="flex items-center gap-3 text-[10px] py-1 border-b border-border-soft"
                >
                  <span className="text-gold font-mono w-20">{m.symbol}</span>
                  <span className="text-ink-2 font-mono w-24">
                    {fmt_price(m.last_price)}
                  </span>
                  <span className={`font-mono w-20 ${chgCls}`}>
                    {fmt_pct(m.change_24h_pct)}
                  </span>
                  <span className="text-ink-3 font-mono flex-1">
                    Vol: {fmt_vol(m.volume_24h)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>

      <div className="grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <V17Card title="信号状态">
          {signals == null ? (
            <div className="text-[11px] text-ink-3 py-2">
              signals store 暂无活跃信号。
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <StatBlock label="总信号数" value={signals.total} />
              <StatBlock label="High" value={signals.high_severity} cls="text-neg" />
              <StatBlock label="Medium" value={signals.medium_severity} cls="text-gold" />
              <StatBlock label="Low" value={signals.low_severity} cls="text-ink-3" />
            </div>
          )}
        </V17Card>

        <V17Card title="后端聚合状态">
          <div className="flex flex-col text-[10px] gap-1">
            <div className="flex justify-between border-b border-border-soft py-1">
              <span className="text-ink-3">快照时间</span>
              <span className="font-mono text-ink-2">{snap ? fmt_ts(snap.ts) : "—"}</span>
            </div>
            <div className="flex justify-between border-b border-border-soft py-1">
              <span className="text-ink-3">慢子调用</span>
              <span className={`font-mono ${slow.length > 0 ? "text-gold" : "text-pos"}`}>
                {slow.length > 0 ? slow.join(", ") : "无"}
              </span>
            </div>
            <div className="flex justify-between py-1">
              <span className="text-ink-3">市场条目</span>
              <span className="font-mono text-ink-2">{markets.length}</span>
            </div>
          </div>
        </V17Card>
      </div>
    </div>
  );
}
