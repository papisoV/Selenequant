"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useApiPage } from "@/lib/hooks/use-api-page";
import type { WorldModelGroup } from "@/lib/api/episodes";
import { useUiText } from "@/components/i18n/use-ui-text"

// ---------------------------------------------------------------------------
// Types — mirror services/local-api/app/api/v1/episodes.py replay payload
// ---------------------------------------------------------------------------

type Provenance = "RECORDED" | "NOT_AVAILABLE" | "WITHHELD" | "MISSING";

interface Bar {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface Trade {
  side: string;
  entry_action?: string;
  entry_price: number;
  exit_price: number;
  entry_time: number;
  exit_time: number;
  pnl: number;
}

interface ReplayPayload {
  episode_id: string;
  strategy_kind: string;
  strategy_id: string;
  strategy_name: string | null;
  symbol: string;
  interval: string;
  exchange: string | null;
  market_type: string | null;
  at: number;
  at_iso: string;
  at_source: string;
  revealed: boolean;
  state: {
    market: {
      provenance: Provenance;
      bars: Bar[];
      bar_count: number;
      last_close: number | null;
    };
    strategy: { provenance: Provenance; params: unknown; kind: string };
    signal: {
      provenance: Provenance;
      trades_before_t: Trade[];
      closed_trade_count: number;
      last_trade: Trade | null;
      backtest_id: string | null;
    };
    memory: { provenance: Provenance; note?: string };
    events: { provenance: Provenance; note?: string };
  };
  outcome:
    | {
        provenance: Provenance;
        trades_after_t: Trade[];
        closed_trade_count: number;
        metrics: Record<string, number> | null;
        post_mortem: Record<string, unknown> | null;
      }
    | {
        provenance: "WITHHELD";
        trades_after_t: Trade[];
        note: string;
        withheld_count: number;
      };
}

interface EpisodeRow {
  id: string;
  strategy_kind: string;
  strategy_id: string;
  strategy_name: string | null;
  symbol: string;
  interval: string;
  backtest_id: string | null;
  provenance: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Provenance badge — the info bar. RECORDED=green, NOT_AVAILABLE=gray,
// WITHHELD=amber, MISSING=red.
// ---------------------------------------------------------------------------

const PROV_STYLE: Record<Provenance, string> = {
  RECORDED: "border-emerald-500/40 bg-emerald-500/10 text-emerald-500",
  NOT_AVAILABLE: "border-border bg-surface-3 text-ink-3",
  WITHHELD: "border-amber-500/40 bg-amber-500/10 text-amber-500",
  MISSING: "border-red-500/40 bg-red-500/10 text-red-500",
};

const PROV_LABEL: Record<Provenance, string> = {
  RECORDED: "RECORDED",
  NOT_AVAILABLE: "NOT AVAILABLE",
  WITHHELD: "WITHHELD",
  MISSING: "MISSING",
};

function ProvBadge({ prov }: { prov: Provenance }) {
  return (
    <span
      data-testid="prov-badge"
      className={`inline-flex shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${PROV_STYLE[prov] ?? PROV_STYLE.NOT_AVAILABLE}`}
    >
      {PROV_LABEL[prov] ?? prov}
    </span>
  );
}

function fmtMs(ms: number): string {
  return new Date(ms).toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

function fmtPnl(n: number): string {
  const s = n >= 0 ? "+" : "";
  return `${s}${n.toFixed(4)}`;
}

// ---------------------------------------------------------------------------
// Time scrubber — input[type=range] over the backtest window. Range endpoints
// come from the fetched market bars (RECORDED kline partition), not invented.
// ---------------------------------------------------------------------------

function TimeScrubber({
  min,
  max,
  value,
  onChange,
}: {
  min: number;
  max: number;
  value: number;
  onChange: (ms: number) => void;
}) {
  const step = Math.max(1, Math.floor((max - min) / 200));
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        data-testid="scrubber"
        className="h-1 flex-1 accent-accent"
        min={min}
        max={max}
        step={step}
        value={Math.min(Math.max(value, min), max)}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span data-testid="scrubber-value" className="shrink-0 text-[11px] text-ink-2">
        {fmtMs(value)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trades table (T-before list / revealed outcome list)
// ---------------------------------------------------------------------------

function TradesTable({ trades, testId }: { trades: Trade[]; testId: string }) {
  const tr = useUiText()

  if (trades.length === 0) {
    return <div className="px-3 py-2 text-xs text-ink-3">{tr("无成交")}</div>;
  }
  return (
    <table data-testid={testId} className="w-full text-left text-xs">
      <thead className="text-ink-3">
        <tr className="border-b border-border">
          <th className="px-3 py-1.5 font-normal">{tr("方向")}</th>
          <th className="px-3 py-1.5 font-normal">{tr("入场")}</th>
          <th className="px-3 py-1.5 font-normal">{tr("出场")}</th>
          <th className="px-3 py-1.5 font-normal">{tr("平仓时间")}</th>
          <th className="px-3 py-1.5 text-right font-normal">PnL</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t, i) => (
          <tr key={i} className="border-b border-border/50">
            <td className={`px-3 py-1.5 ${t.side === "long" || t.entry_action === "buy" ? "text-emerald-500" : "text-red-500"}`}>
              {t.entry_action ?? t.side}
            </td>
            <td className="px-3 py-1.5">{t.entry_price}</td>
            <td className="px-3 py-1.5">{t.exit_price}</td>
            <td className="px-3 py-1.5 text-ink-2">{fmtMs(t.exit_time)}</td>
            <td className={`px-3 py-1.5 text-right ${t.pnl >= 0 ? "text-emerald-500" : "text-red-500"}`}>
              {fmtPnl(t.pnl)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Replay view — ?id=epi-xxx
// ---------------------------------------------------------------------------

function ReplayView({ episodeId }: { episodeId: string }) {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const [revealed, setRevealed] = useState(false);
  const [scrubAt, setScrubAt] = useState<number | null>(null);

  const path = useMemo(() => {
    const p = new URLSearchParams();
    if (scrubAt !== null) p.set("at", String(scrubAt));
    if (revealed) p.set("reveal", "true");
    const q = p.toString();
    return `/api/v1/episodes/${encodeURIComponent(episodeId)}/replay${q ? `?${q}` : ""}`;
  }, [episodeId, scrubAt, revealed]);

  const { data, error, isLoading } = useApi<ReplayPayload>(path);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center gap-3 text-xs text-ink-3">
        <span>{tr("Episode 加载失败（")}{error.status}）</span>
        <Link className="text-accent hover:underline" href="/research/episodes">
          {tr("返回列表")}
        </Link>
      </div>
    );
  }
  if (isLoading || !data) {
    return <div className="flex h-full items-center justify-center text-xs text-ink-3">{tr("加载中…")}</div>;
  }

  const s = data.state;
  const outcome = data.outcome;
  const bars = s.market.bars;
  const firstBar = bars[0];
  const lastBar = bars[bars.length - 1];
  const scrubberReady = firstBar && lastBar && firstBar.timestamp < lastBar.timestamp;
  const isRevealed = "withheld_count" in outcome ? false : true;
  const withheldCount = "withheld_count" in outcome ? outcome.withheld_count : 0;
  const revealedOutcome = "withheld_count" in outcome ? null : outcome;

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      {/* header info bar */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-ink">{data.strategy_name ?? data.strategy_id}</span>
        <span className="text-ink-3">{data.symbol}</span>
        <span className="text-ink-3">{data.interval}</span>
        <span className="text-ink-3">{data.strategy_kind}</span>
        {s.signal.backtest_id && (
          <span className="text-ink-3">bt {s.signal.backtest_id}</span>
        )}
        <span className="ml-auto text-[11px] text-ink-3">
          T = {fmtMs(data.at)}（{data.at_source}）
        </span>
      </div>

      {/* scrubber */}
      {scrubberReady ? (
        <TimeScrubber
          min={firstBar.timestamp}
          max={lastBar.timestamp}
          value={data.at}
          onChange={setScrubAt}
        />
      ) : (
        <div className="text-[11px] text-ink-3">{tr("无市场数据，scrubber 不可用")}</div>
      )}

      {/* state sections */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <section className="rounded border border-border">
          <header className="flex items-center gap-2 border-b border-border px-3 py-2">
            <h3 className="text-xs font-medium text-ink">{tr("市场（T 前 200 根）")}</h3>
            <ProvBadge prov={s.market.provenance} />
            {s.market.last_close !== null && (
              <span className="ml-auto text-[11px] text-ink-2">
                close {s.market.last_close}
              </span>
            )}
          </header>
          <div className="px-3 py-2 text-[11px] text-ink-2">
            {s.market.bar_count} {tr("根 bar · 最新")} {lastBar ? fmtMs(lastBar.timestamp) : "—"}
          </div>
        </section>

        <section className="rounded border border-border">
          <header className="flex items-center gap-2 border-b border-border px-3 py-2">
            <h3 className="text-xs font-medium text-ink">{tr("策略参数")}</h3>
            <ProvBadge prov={s.strategy.provenance} />
          </header>
          <pre className="max-h-24 overflow-auto px-3 py-2 text-[11px] text-ink-2">
            {s.strategy.params ? JSON.stringify(s.strategy.params, null, 1) : "—"}
          </pre>
        </section>

        <section className="rounded border border-border">
          <header className="flex items-center gap-2 border-b border-border px-3 py-2">
            <h3 className="text-xs font-medium text-ink">{tr("信号（T 前成交）")}</h3>
            <ProvBadge prov={s.signal.provenance} />
            <span className="ml-auto text-[11px] text-ink-2">
              {tr("已平仓")} {s.signal.closed_trade_count} {tr("笔 · 显示最近")} {s.signal.trades_before_t.length}
            </span>
          </header>
          <div className="max-h-64 overflow-auto">
            <TradesTable trades={s.signal.trades_before_t} testId="trades-before" />
          </div>
        </section>

        <section className="rounded border border-border">
          <header className="flex items-center gap-2 border-b border-border px-3 py-2">
            <h3 className="text-xs font-medium text-ink">{tr("结果（T 后）")}</h3>
            <ProvBadge prov={outcome.provenance} />
            {isRevealed && revealedOutcome && (
              <span className="ml-auto text-[11px] text-ink-2">
                {revealedOutcome.closed_trade_count} {tr("笔")}
              </span>
            )}
          </header>
          {!isRevealed ? (
            <div className="flex items-center gap-3 px-3 py-3">
              <span className="text-[11px] text-ink-2">
                {withheldCount} {tr("笔 T 后成交已扣住（Outcome Reveal）")}
              </span>
              <button
                type="button"
                data-testid="reveal-btn"
                onClick={() => setRevealed(true)}
                className="rounded border border-accent/60 bg-accent/10 px-2.5 py-1 text-xs text-accent hover:bg-accent/20"
              >
                {tr("Reveal 揭晓")}
              </button>
            </div>
          ) : (
            <div className="max-h-64 overflow-auto">
              <TradesTable trades={revealedOutcome!.trades_after_t} testId="trades-after" />
            </div>
          )}
        </section>
      </div>

      {/* revealed extras: metrics + post-mortem */}
      {isRevealed && revealedOutcome?.metrics && (
        <section className="rounded border border-border">
          <header className="border-b border-border px-3 py-2">
            <h3 className="text-xs font-medium text-ink">{tr("回测指标（全窗口）")}</h3>
          </header>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 px-3 py-2 text-[11px] sm:grid-cols-4">
            {Object.entries(revealedOutcome.metrics)
              .filter(([, v]) => typeof v === "number")
              .map(([k, v]) => (
                <div key={k} className="flex justify-between gap-2">
                  <span className="text-ink-3">{k}</span>
                  <span className="text-ink-2">{(v as number).toFixed(4)}</span>
                </div>
              ))}
          </div>
        </section>
      )}
      {isRevealed && revealedOutcome?.post_mortem && (
        <section className="rounded border border-border">
          <header className="border-b border-border px-3 py-2">
            <h3 className="text-xs font-medium text-ink">{tr("Post-mortem（T 前判断 vs 实际）")}</h3>
          </header>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 px-3 py-2 text-[11px] sm:grid-cols-3">
            {Object.entries(revealedOutcome.post_mortem).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <span className="text-ink-3">{k}</span>
                <span className="text-ink-2">{String(v)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* v0 honesty footer */}
      <div className="flex flex-wrap items-center gap-2 text-[10px] text-ink-3">
        <span>memory</span>
        <ProvBadge prov={s.memory.provenance} />
        <span>events</span>
        <ProvBadge prov={s.events.provenance} />
        <span className="ml-auto">
          {tr("RECONSTRUCTED 不做 — 回放只答「当时看到了什么」，不含任何未来信息（reveal 前除外，且明确标注）")}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// List view — no ?id. Dual tab: 经验地图 (World Model) default / Episode 列表.
// ---------------------------------------------------------------------------

function winRateBadgeClass(winRate: number): string {
  if (winRate >= 0.5) return "border-emerald-500/40 bg-emerald-500/10 text-emerald-500";
  if (winRate > 0) return "border-amber-500/40 bg-amber-500/10 text-amber-500";
  return "border-red-500/40 bg-red-500/10 text-red-500";
}

function WorldModelView() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, error, isLoading } =
    useApi<{ groups: WorldModelGroup[]; total_episodes: number }>("/api/v1/episodes/world-model");

  if (error) {
    return <div className="flex h-full items-center justify-center text-xs text-ink-3">{tr("经验地图加载失败（")}{error.status}）</div>;
  }
  if (isLoading) {
    return <div className="flex h-full items-center justify-center text-xs text-ink-3">{tr("加载中…")}</div>;
  }
  if (!data || data.groups.length === 0) {
    return <div className="flex h-full items-center justify-center text-xs text-ink-3">{tr("暂无经验 — 跑一次回测后按品种/周期聚合成经验地图")}</div>;
  }

  return (
    <div data-testid="world-model-grid" className="grid grid-cols-1 gap-3 overflow-y-auto p-4 md:grid-cols-2 xl:grid-cols-3">
      {data.groups.map((g) => (
        <div key={`${g.symbol}:${g.interval}`} className="rounded border border-border p-3">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium text-ink">
              {g.symbol} · {g.interval}
            </h2>
            <span
              data-testid="win-rate-badge"
              className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${winRateBadgeClass(g.win_rate)}`}
            >
              {tr("胜率")} {(g.win_rate * 100).toFixed(0)}%
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-2">
            <span>Episode {g.episode_count}</span>
            <span>{tr("盈利")} {g.winning_count}</span>
            {g.avg_sharpe !== null && <span>avg Sharpe {g.avg_sharpe.toFixed(2)}</span>}
            <span className="text-ink-3">{g.kinds.join(" / ")}</span>
          </div>
          {g.best && (
            <div className="mt-2 border-t border-border/50 pt-2 text-[11px]">
              <div className="text-ink">{g.best.strategy_id}</div>
              <div className="flex gap-x-4 text-ink-2">
                <span>{tr("best 年化")} {(g.best.annual_return! * 100).toFixed(2)}%</span>
                {g.best.sharpe_ratio !== null && <span>Sharpe {g.best.sharpe_ratio.toFixed(2)}</span>}
              </div>
            </div>
          )}
          {g.last_episode_at && (
            <div className="mt-2 text-[10px] text-ink-3">{tr("最近")} {g.last_episode_at}</div>
          )}
        </div>
      ))}
    </div>
  );
}

function EpisodeListView() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, error, isLoading } = useApi<EpisodeRow[]>("/api/v1/episodes?limit=50");

  if (error) {
    return <div className="flex h-full items-center justify-center text-xs text-ink-3">{tr("Episode 列表加载失败（")}{error.status}）</div>;
  }
  if (isLoading) {
    return <div className="flex h-full items-center justify-center text-xs text-ink-3">{tr("加载中…")}</div>;
  }
  if (!data || data.length === 0) {
    return <div className="flex h-full items-center justify-center text-xs text-ink-3">{tr("暂无 Episode — 跑一次回测后这里会出现经验记录")}</div>;
  }

  return (
    <div className="overflow-auto">
      <table className="w-full text-left text-xs" data-testid="episode-list">
        <thead className="text-ink-3">
          <tr className="border-b border-border">
            <th className="px-3 py-2 font-normal">{tr("策略")}</th>
            <th className="px-3 py-2 font-normal">{tr("符号")}</th>
            <th className="px-3 py-2 font-normal">{tr("周期")}</th>
            <th className="px-3 py-2 font-normal">kind</th>
            <th className="px-3 py-2 font-normal">provenance</th>
            <th className="px-3 py-2 font-normal">{tr("时间")}</th>
            <th className="px-3 py-2 font-normal"></th>
          </tr>
        </thead>
        <tbody>
          {data.map((e) => (
            <tr key={e.id} className="border-b border-border/50">
              <td className="px-3 py-2 text-ink">{e.strategy_name ?? e.strategy_id}</td>
              <td className="px-3 py-2">{e.symbol}</td>
              <td className="px-3 py-2">{e.interval}</td>
              <td className="px-3 py-2 text-ink-2">{e.strategy_kind}</td>
              <td className="px-3 py-2">{e.provenance}</td>
              <td className="px-3 py-2 text-ink-2">{e.created_at}</td>
              <td className="px-3 py-2 text-right">
                <Link
                  className="text-accent hover:underline"
                  href={`/research/episodes?id=${encodeURIComponent(e.id)}`}
                >
                  Replay
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page — ?id=epi-xxx → ReplayView; no id → list
// ---------------------------------------------------------------------------

function EpisodesInner() {
  const tr = useUiText();
  const params = useSearchParams();
  const id = params.get("id");
  const [tab, setTab] = useState<"map" | "list">("map");

  if (id) {
    return <ReplayView episodeId={id} />;
  }

  return (
    <div className="flex h-full flex-col">
      {/* 本页此前没有任何 h1:标题目录里只有 tab 按钮和分组 H3,
          屏幕阅读器的标题导航会直接落到「BTC/USDT · 4h」。 */}
      <h1 className="sr-only">Episode 经验记录</h1>
      <div className="flex gap-1 border-b border-border px-3 py-2">
        {(
          [
            ["map", "经验地图"],
            ["list", "Episode 列表"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            data-testid={`tab-${key}`}
            onClick={() => setTab(key)}
            className={`rounded px-2.5 py-1 text-xs ${
              tab === key
                ? "bg-accent/10 font-medium text-accent"
                : "text-ink-3 hover:bg-surface-3"
            }`}
          >
            {tr(label)}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        {tab === "map" ? <WorldModelView /> : <EpisodeListView />}
      </div>
    </div>
  );
}

export function EpisodesPage() {
  return (
    <Suspense fallback={<div className="h-full" />}>
      <EpisodesInner />
    </Suspense>
  );
}
