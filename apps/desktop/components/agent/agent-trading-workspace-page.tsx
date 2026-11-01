"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 交易工作台 — GET /api/v1/agent/trading/workspace
// shape: { positions[], pending_orders[], active_strategies[], account_summary{} }

interface Position {
  symbol?: string;
  side?: string;
  size?: number;
  entry_price?: number;
  mark_price?: number;
  unrealized_pnl?: number;
}
interface PendingOrder {
  id?: string;
  symbol?: string;
  side?: string;
  size?: number;
  price?: number;
  type?: string;
}
interface ActiveStrategy {
  id?: string;
  name?: string;
  symbol?: string;
  status?: string;
}
interface AccountSummary {
  balance?: number;
  equity?: number;
  unrealized_pnl?: number;
  available?: number;
}
interface TradingWorkspaceData {
  positions?: Position[];
  pending_orders?: PendingOrder[];
  active_strategies?: ActiveStrategy[];
  account_summary?: AccountSummary;
}

interface DecisionLesson {
  deployment_id?: string;
  strategy_id?: string;
  symbols?: string[];
  entry_date?: string;
  rating?: string;
  is_review?: boolean;
  decision_text?: string;
  status?: string;
  outcome_metrics?: Record<string, unknown> | null;
  reflection_text?: string;
}

function sideCls(s?: string): string {
  if (s === "buy" || s === "long") return "text-pos";
  if (s === "sell" || s === "short") return "text-neg";
  return "text-ink-3";
}

const fmtNum = (n?: number): string =>
  n == null ? "—" : Number.isInteger(n) ? String(n) : n.toFixed(4);

export function AgentTradingWorkspacePage() {
  const tr = useUiText()

  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<TradingWorkspaceData | null>(
    "/api/v1/agent/trading/workspace",
  );
  const { data: lessons } = useApi<DecisionLesson[] | null>(
    "/api/v1/agent/decision-lessons",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const positions = d?.positions ?? [];
  const orders = d?.pending_orders ?? [];
  const strategies = d?.active_strategies ?? [];
  const acct = d?.account_summary ?? {};

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      {/* 页面级 h1:V17Card 的 title 渲染为 <span>,不构成标题层级 →
          axe page-has-heading-one 报缺。视觉不变(sr-only),补齐语义标题。 */}
      <h1 className="sr-only">{tr("交易工作台")}</h1>
      <V17Card
        title={tr("交易工作台")}
        right={
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing || isLoading}
            aria-label={tr("刷新")}
            className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={tr("账户余额")} value={fmtNum(acct.balance)} cls="text-ink" />
          <StatBlock label={tr("权益")} value={fmtNum(acct.equity)} cls="text-ink-2" />
          <StatBlock label={tr("可用")} value={fmtNum(acct.available)} cls="text-ink-2" />
          <StatBlock
            label={tr("未实现盈亏")}
            value={fmtNum(acct.unrealized_pnl)}
            cls={(acct.unrealized_pnl ?? 0) >= 0 ? "text-pos" : "text-neg"}
          />
        </div>
      </V17Card>

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
        <V17Card title={tr("持仓 ({n})", { n: positions.length })}>
          {positions.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{isLoading ? tr("加载中…") : tr("无持仓")}</div>
          ) : (
            <div className="max-h-64 overflow-auto">
              {positions.map((p, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                  <span className="w-14 truncate text-ink">{p.symbol}</span>
                  <span className={`w-12 font-mono ${sideCls(p.side)}`}>{p.side}</span>
                  <span className="w-12 text-right font-mono num text-ink-3">{fmtNum(p.size)}</span>
                  <span className="w-14 text-right font-mono num text-ink-3">{fmtNum(p.entry_price)}</span>
                  <span className={`w-14 text-right font-mono num ${(p.unrealized_pnl ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                    {fmtNum(p.unrealized_pnl)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </V17Card>

        <V17Card title={tr("待处理订单 ({n})", { n: orders.length })}>
          {orders.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("无待处理订单")}</div>
          ) : (
            <div className="max-h-64 overflow-auto">
              {orders.map((o, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                  <span className="w-16 truncate text-ink">{o.symbol}</span>
                  <span className={`w-12 font-mono ${sideCls(o.side)}`}>{o.side}</span>
                  <span className="w-10 text-right font-mono num text-ink-3">{fmtNum(o.size)}</span>
                  <span className="w-14 text-right font-mono num text-ink-3">{fmtNum(o.price)}</span>
                  <span className="text-[9px] text-ink-4">{o.type}</span>
                </div>
              ))}
            </div>
          )}
        </V17Card>
      </div>

      <V17Card title={tr("活跃策略 ({n})", { n: strategies.length })}>
        {strategies.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">{tr("无活跃策略")}</div>
        ) : (
          <div className="flex flex-col">
            {strategies.map((s, i) => (
              <div key={i} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                <span className="flex-1 truncate text-ink">{s.name ?? s.id}</span>
                <span className="w-14 truncate text-ink-3 font-mono">{s.symbol}</span>
                <span className={`w-16 text-right font-mono ${s.status === "active" ? "text-pos" : "text-gold"}`}>{s.status}</span>
              </div>
            ))}
          </div>
        )}
      </V17Card>

      <V17Card title={tr("决策教训 ({n})", { n: (lessons ?? []).length })}>
        {(lessons ?? []).length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无决策记录（部署策略后自动生成）")}</div>
        ) : (
          <div className="max-h-64 overflow-auto">
            {(lessons ?? []).map((l, i) => (
              <div key={l.deployment_id ?? i} className="py-1.5 border-b border-border-soft text-[10px]">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-ink-3">{l.entry_date}</span>
                  <span className="truncate text-ink">{l.strategy_id}</span>
                  <span className="truncate text-ink-4">{(l.symbols ?? []).join("/")}</span>
                  <span
                    className={`ml-auto rounded px-1.5 py-0.5 text-[9px] ${
                      l.status === "filled" ? "bg-emerald-900/40 text-pos" : "bg-gold/20 text-gold"
                    }`}
                  >
                    {l.status === "filled" ? tr("已回填") : tr("待回填")}
                  </span>
                  {l.rating &&
                    (l.is_review ? (
                      <span
                        className="rounded bg-warn/20 px-1.5 py-0.5 text-[9px] font-semibold text-warn"
                        title={tr("评级读不出,不能当作可交易档位——需人看或重跑")}
                      >
                        {tr("评级读不出·需人看")}
                      </span>
                    ) : (
                      <span className="font-mono text-ink-4">{l.rating}</span>
                    ))}
                </div>
                {l.reflection_text && (
                  <div className="mt-0.5 text-ink-3">{l.reflection_text}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}
