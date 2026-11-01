"use client";

import Link from "next/link";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useWorkflowStore } from "@/lib/store/workflow";
import { CHAINS } from "@/lib/navigation-chains";
import { ALL_PAGES } from "@/lib/page-manifest";
import { useUiText, useLocale } from "@/components/i18n/use-ui-text";
import type { TuningResultSummary } from "@/lib/tuning-bridge";

// A股研究链的入口卡:直接从导航链取 steps,避免手抄列表与链定义漂移
// (2026-10-08 审计 P0-2 —— 该链此前只在侧边栏,research 主入口看不到)。
const ASHARE_STEPS: string[] =
  CHAINS.research.find((c) => c.id === "ashare")?.steps ?? [];
const ASHARE_LABEL: Record<string, string> = {
  "ashare-fundamentals": "财报PIT · 数据底座",
  "ashare-style": "风格监测 · 现状",
  "ashare-backtest": "策略回测",
  "ashare-portfolio": "组合调仓引擎",
  "ashare-factor-report": "因子 IC 报告",
};
const titleOf = (slug: string): string =>
  ALL_PAGES.find((p) => p.slug === slug)?.title ?? slug;

// 设计稿 L6268 renderStratList + L6391 renderStratDetail — 研究中心主页面
// 列表(左) + 详情(右)布局,可选策略,展示参数/绩效/overfit
interface Strategy {
  id: string;
  name: string;
  cat: string;
  catLabel: string;
  status: "active" | "idle";
  // 工厂模板不带实测指标 —— null 表示"没跑过",渲染成 — 而不是 0.00
  sharpe: number | null;
  winrate: number | null;
}

interface BackendTemplate {
  id?: string;
  template_id?: string;
  name?: string;
  name_zh?: string;
  display_name?: string;
  category?: string;
  cat?: string;
  sharpe?: number;
  win_rate?: number;
  winrate?: number;
  default_params?: Record<string, number | string>;
}

const CAT_LABEL: Record<string, string> = {
  osc: "震荡", oscillator: "震荡",
  trend: "趋势",
  mr: "均值回归", mean_reversion: "均值回归",
  grid: "网格",
};

export function RschPage() {
  const tr = useUiText()
  const locale = useLocale()

  const rsch = useWorkflowStore((s) => s.rsch);
  const setRsch = useWorkflowStore((s) => s.setRsch);
  const currentId: string = rsch?.currentId ?? "";
  const setCurrentId = (v: string) => setRsch({ currentId: v });
  const { data: realTemplates, isLoading, error } = useApi<BackendTemplate[] | { templates: BackendTemplate[] }>(
    "/api/v1/strategies/factory-templates"
  );
  // 过拟合检验的真实来源:调参记录。工厂模板本身不带 DSR/PBO —— 之前卡片
  // 把三项写死成 "—",看起来像"检验了但没数据",实际是从来没人接。
  const { data: tuningRows } = useApi<TuningResultSummary[]>(
    "/api/v1/research/tuning/results"
  );
  // strategy_id -> 最新一条调参记录(文件名 timestamp 是唯一权威创建时间)。
  // 同一策略可能有多次调参(不同区间/日期),取最新。
  const latestByStrategy = new Map<string, TuningResultSummary>();
  for (const r of Array.isArray(tuningRows) ? tuningRows : []) {
    const prev = latestByStrategy.get(r.strategy_id);
    if (!prev || r.timestamp > prev.timestamp) latestByStrategy.set(r.strategy_id, r);
  }
  const STRATEGIES: (Strategy & { params: [string, number | string][] })[] = (() => {
    const list: BackendTemplate[] = Array.isArray(realTemplates)
      ? realTemplates
      : (realTemplates?.templates ?? []);
    const finiteOrNull = (v: unknown): number | null => {
      const n = Number(v);
      return v === undefined || v === null || !Number.isFinite(n) ? null : n;
    };
    return list.map((t, i) => {
      const id = String(t.id ?? t.template_id ?? `s${i}`);
      const latest = latestByStrategy.get(id);
      return {
        id,
        name:
          locale === "en"
            ? (t.name ?? t.name_zh ?? t.display_name ?? "?")
            : (t.name_zh ?? t.name ?? t.display_name ?? "?"),
        cat: t.cat ?? t.category ?? "—",
        catLabel: CAT_LABEL[t.cat ?? t.category ?? ""] ?? t.cat ?? t.category ?? "—",
        status: i % 3 === 0 ? "active" : "idle",
        // 模板本身不带实测指标;有调参记录时用它的 best_sharpe 兜底,
        // 这样左栏至少能按"跑过没有"区分,而不是整列 0.00。
        sharpe: finiteOrNull(t.sharpe) ?? finiteOrNull(latest?.best_sharpe),
        winrate: finiteOrNull(t.winrate ?? t.win_rate) ?? finiteOrNull(latest?.win_rate),
        params: Object.entries(t.default_params ?? {}),
      };
    });
  })();
  const s = STRATEGIES.find((x) => x.id === currentId) || STRATEGIES[0];
  const p = s ? s.params : [];
  const lt = s ? latestByStrategy.get(s.id) : undefined;
  const fmt = (v: number | null | undefined, digits = 2): string =>
    typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "—";
  // 调参记录里的比率字段是小数(-0.092 = -9.2%),工厂模板的 winrate 是百分数
  const pct = (v: number | null | undefined): string =>
    typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(2)}%` : "—";
  // 调参记录优先;没有记录时才回落到工厂模板字段
  const perf = {
    sharpe: lt ? fmt(lt.best_sharpe) : fmt(s?.sharpe),
    annual: lt ? pct(lt.annual_return) : "—",
    winrate: lt ? pct(lt.win_rate) : s?.winrate == null ? "—" : `${s.winrate}%`,
    drawdown: lt ? pct(lt.max_drawdown) : "—",
  };

  return (
    <div><div className="grid gap-2" style={{ gridTemplateColumns: "var(--rail-w) 1fr" }}>
      <div className="flex flex-col gap-2">
        <V17Card title={tr("A股研究")} right={tr("{n} 页", { n: ASHARE_STEPS.length })}>
          <div className="flex flex-col text-[10px]">
            {ASHARE_STEPS.map((slug, i) => (
              <Link
                key={slug}
                href={`/research/${slug}`}
                className="flex items-center justify-between border-b border-border-soft py-1 last:border-0 hover:bg-surface-2"
                data-testid={`ashare-entry-${slug}`}
              >
                <span className="text-ink-2">
                  <span className="mr-1 text-ink-4 font-mono">{i + 1}</span>
                  {tr(titleOf(slug))}
                </span>
                <span className="text-[9px] text-ink-4">
                  {ASHARE_LABEL[slug] ? tr(ASHARE_LABEL[slug]) : ""}
                </span>
              </Link>
            ))}
          </div>
        </V17Card>
        <V17Card title={tr("策略工厂 {n}", { n: STRATEGIES.length })}>
        <div className="flex flex-col text-[10px]">
          <div className="grid grid-cols-[1fr_50px_40px_40px] gap-1 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>{tr("策略")}</span>
            <span className="text-right">{tr("夏普")}</span>
            <span className="text-right">{tr("胜率")}</span>
            <span className="text-right">{tr("操作")}</span>
          </div>
          {isLoading ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("加载中...")}</div>
          ) : error ? (
            <div className="text-center text-destructive text-[11px] py-3">{tr("加载失败，请稍后重试")}</div>
          ) : STRATEGIES.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          ) : (
            <>{STRATEGIES.map((st) => {
              const active = st.id === currentId ? " bg-surface-2" : "";
              const sharpeCls = st.sharpe === null ? "text-ink-4" : st.sharpe >= 1.8 ? "text-pos" : st.sharpe < 1.0 ? "text-neg" : "text-ink-2";
              const statusDot = st.status === "active" ? "text-pos" : "text-ink-4";
              return (
                <div
                  key={st.id}
                  className={`grid grid-cols-[1fr_50px_40px_40px] gap-1 py-1 border-b border-border-soft cursor-pointer hover:bg-surface-2${active}`}
                  onClick={() => setCurrentId(st.id)}
                >
                  <span className="text-ink-2">
                    <span className={statusDot}>●</span> {st.name}
                  </span>
                  <span className={`num text-right font-mono ${sharpeCls}`}>{fmt(st.sharpe)}</span>
                  <span className="num text-right text-ink-3 font-mono">{st.winrate === null ? "—" : `${st.winrate}%`}</span>
                  <span className="text-right text-gold">▶</span>
                </div>
              );
            })}</>
          )}
        </div>
        </V17Card>
      </div>
      <div className="flex flex-col gap-2">
        {s ? (
          <>
            <V17Card title={`${s.name} · ${tr(s.catLabel)}`}>
              <div className="grid grid-cols-4 gap-2 text-[10px]">
                <StatBlock label={tr("策略名")} value={s.id} />
                <StatBlock label={tr("状态")} value={s.status === "active" ? tr("运行中") : tr("空闲")} cls={s.status === "active" ? "text-pos" : "text-ink-3"} />
                <StatBlock label={tr("夏普")} value={fmt(s.sharpe)} cls="text-gold" />
                <StatBlock label={tr("胜率")} value={s.winrate === null ? "—" : `${s.winrate}%`} />
              </div>
            </V17Card>
            <V17Card title={tr("参数")}>
              <div className="flex flex-col text-[10px]">
                {p.length === 0 ? (
                  <div className="py-1 text-ink-4">{tr("该策略未声明默认参数")}</div>
                ) : (
                  p.map(([k, v]) => (
                    <div key={k} className="flex justify-between py-1 border-b border-border-soft">
                      <span className="text-ink-3">{k}</span>
                      <span className="text-gold font-mono">{v}</span>
                    </div>
                  ))
                )}
                <div className="mt-2 rounded bg-surface-3 px-2 py-1 text-center text-[9px] text-ink-4 cursor-pointer">
                  {tr("⚙ LLM 自动调参")}
                </div>
              </div>
            </V17Card>
            <V17Card title={tr("绩效")} right={lt ? tr("最近一次调参") : undefined}>
              <div className="grid grid-cols-4 gap-2">
                <StatBlock label={tr("夏普")} value={perf.sharpe} cls="text-gold" />
                <StatBlock label={tr("年化")} value={perf.annual} cls="text-pos" />
                <StatBlock label={tr("胜率")} value={perf.winrate} />
                <StatBlock label={tr("回撤")} value={perf.drawdown} cls="text-destructive" />
              </div>
            </V17Card>
            <V17Card title={tr("过拟合检验")} right={lt ? lt.timestamp.slice(0, 8) : undefined}>
              <div className="flex flex-col text-[10px]">
                {/* 三项都读最近一次调参的真实字段。null = 这次没算出来
                    (交易全平 / 记录早于该字段),显示"未计算"而不是 "—",
                    否则"没指标"和"算崩了"在界面上长得一模一样。 */}
                <div className="flex justify-between py-1 border-b border-border-soft">
                  <span className="text-ink-3">Deflated Sharpe</span>
                  <span className="text-pos font-mono" data-testid="overfit-dsr">
                    {lt ? fmt(lt.deflated_sharpe_ratio) : "未计算"}
                  </span>
                </div>
                <div className="flex justify-between py-1 border-b border-border-soft">
                  <span className="text-ink-3">PBO</span>
                  <span className="text-gold font-mono" data-testid="overfit-pbo">
                    {lt ? fmt(lt.pbo) : "未计算"}
                  </span>
                </div>
                <div className="flex justify-between py-1 border-b border-border-soft">
                  <span className="text-ink-3">Purged K-Fold</span>
                  <span className="text-pos" data-testid="overfit-kfold">
                    未接入
                  </span>
                </div>
                {!lt && (
                  <div className="mt-1 text-[9px] text-ink-4">
                    该策略还没有调参记录 —— DSR/PBO 由参数优化生成
                  </div>
                )}
                <div className="flex gap-2 mt-2">
                  {/* walk_forward_validate 只在 strategy-engine.optimizer 里,没有 HTTP 路由
                      → 此处不给假按钮:标成待接线,而不是点了没反应。 */}
                  <button
                    type="button"
                    disabled
                    title={tr("工厂模板的 walk-forward 尚无 API 路由(optimizer.walk_forward_validate 未暴露),接入后再启用")}
                    className="row-btn flex-1 cursor-not-allowed rounded border border-border-soft bg-surface-2 px-2 py-1 text-ink-4 opacity-60"
                  >
                    ▶ Walk-Forward
                  </button>
                  {/* 重采样稳健性(置换检验 + 自助法)在回测中心默认跑;跳到那里发车。 */}
                  {s && (
                    <Link
                      href={`/trade/backtests?strategy_id=${encodeURIComponent(s.id)}`}
                      title={tr("去回测中心发车 —— 置换检验/自助法置信区间默认随回测一起算")}
                      className="row-btn flex-1 rounded border border-border-soft bg-surface-2 px-2 py-1 text-center text-gold hover:bg-gold/10"
                    >
                      ▶ Monte-Carlo
                    </Link>
                  )}
                </div>
              </div>
            </V17Card>
          </>
        ) : isLoading ? (
          <V17Card title={tr("策略详情")}>
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("加载中...")}</div>
          </V17Card>
        ) : error ? (
          <V17Card title={tr("策略详情")}>
            <div className="text-center text-destructive text-[11px] py-3">{tr("加载失败，请稍后重试")}</div>
          </V17Card>
        ) : (
          <V17Card title={tr("策略详情")}>
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          </V17Card>
        )}
      </div>
      </div>
    </div>
  );
}
