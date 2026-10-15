"use client";

import Link from "next/link";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useWorkflowStore } from "@/lib/store/workflow";
import { CHAINS } from "@/lib/navigation-chains";
import { ALL_PAGES } from "@/lib/page-manifest";

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
  sharpe: number;
  winrate: number;
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
  const rsch = useWorkflowStore((s) => s.rsch);
  const setRsch = useWorkflowStore((s) => s.setRsch);
  const currentId: string = rsch?.currentId ?? "";
  const setCurrentId = (v: string) => setRsch({ currentId: v });
  const { data: realTemplates, isLoading, error } = useApi<BackendTemplate[] | { templates: BackendTemplate[] }>(
    "/api/v1/strategies/factory-templates"
  );
  const STRATEGIES: (Strategy & { params: [string, number | string][] })[] = (() => {
    const list: BackendTemplate[] = Array.isArray(realTemplates)
      ? realTemplates
      : (realTemplates?.templates ?? []);
    return list.map((t, i) => ({
      id: String(t.id ?? t.template_id ?? `s${i}`),
      name: t.name_zh ?? t.name ?? t.display_name ?? "?",
      cat: t.cat ?? t.category ?? "—",
      catLabel: CAT_LABEL[t.cat ?? t.category ?? ""] ?? t.cat ?? t.category ?? "—",
      status: i % 3 === 0 ? "active" : "idle",
      sharpe: Number(t.sharpe ?? 0),
      winrate: Number(t.winrate ?? t.win_rate ?? 0),
      params: Object.entries(t.default_params ?? {}),
    }));
  })();
  const s = STRATEGIES.find((x) => x.id === currentId) || STRATEGIES[0];
  const p = s ? s.params : [];

  return (
    <div className="h-full overflow-y-auto"><div className="grid gap-2" style={{ gridTemplateColumns: "var(--rail-w) 1fr" }}>
      <div className="flex flex-col gap-2">
        <V17Card title="A股研究" right={`${ASHARE_STEPS.length} 页`}>
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
                  {titleOf(slug)}
                </span>
                <span className="text-[9px] text-ink-4">
                  {ASHARE_LABEL[slug] ?? ""}
                </span>
              </Link>
            ))}
          </div>
        </V17Card>
        <V17Card title={`策略工厂 ${STRATEGIES.length}`}>
        <div className="flex flex-col text-[10px]">
          <div className="grid grid-cols-[1fr_50px_40px_40px] gap-1 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>策略</span>
            <span className="text-right">夏普</span>
            <span className="text-right">胜率</span>
            <span className="text-right">操作</span>
          </div>
          {isLoading ? (
            <div className="text-center text-ink-4 text-[11px] py-3">加载中...</div>
          ) : error ? (
            <div className="text-center text-destructive text-[11px] py-3">加载失败，请稍后重试</div>
          ) : STRATEGIES.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          ) : (
            <>{STRATEGIES.map((st) => {
              const active = st.id === currentId ? " bg-surface-2" : "";
              const sharpeCls = (st.sharpe ?? 0) >= 1.8 ? "text-pos" : (st.sharpe ?? 0) < 1.0 ? "text-neg" : "text-ink-2";
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
                  <span className={`num text-right font-mono ${sharpeCls}`}>{st.sharpe?.toFixed(2) ?? "—"}</span>
                  <span className="num text-right text-ink-3 font-mono">{st.winrate}%</span>
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
            <V17Card title={`${s.name} · ${s.catLabel}`}>
              <div className="grid grid-cols-4 gap-2 text-[10px]">
                <StatBlock label="策略名" value={s.id} />
                <StatBlock label="状态" value={s.status === "active" ? "运行中" : "空闲"} cls={s.status === "active" ? "text-pos" : "text-ink-3"} />
                <StatBlock label="夏普" value={s.sharpe?.toFixed(2) ?? "—"} cls="text-gold" />
                <StatBlock label="胜率" value={`${s.winrate}%`} />
              </div>
            </V17Card>
            <V17Card title="参数">
              <div className="flex flex-col text-[10px]">
                {p.length === 0 ? (
                  <div className="py-1 text-ink-4">该策略未声明默认参数</div>
                ) : (
                  p.map(([k, v]) => (
                    <div key={k} className="flex justify-between py-1 border-b border-border-soft">
                      <span className="text-ink-3">{k}</span>
                      <span className="text-gold font-mono">{v}</span>
                    </div>
                  ))
                )}
                <div className="mt-2 rounded bg-surface-3 px-2 py-1 text-center text-[9px] text-ink-4 cursor-pointer">
                  ⚙ LLM 自动调参
                </div>
              </div>
            </V17Card>
            <V17Card title="绩效">
              <div className="grid grid-cols-4 gap-2">
                <StatBlock label="夏普" value={s.sharpe?.toFixed(2) ?? "—"} cls="text-gold" />
                <StatBlock label="年化" value="—" cls="text-pos" />
                <StatBlock label="胜率" value={`${s.winrate}%`} />
                <StatBlock label="回撤" value="—" cls="text-destructive" />
              </div>
            </V17Card>
            <V17Card title="过拟合检验">
              <div className="flex flex-col text-[10px]">
                <div className="flex justify-between py-1 border-b border-border-soft">
                  <span className="text-ink-3">Deflated Sharpe</span>
                  <span className="text-pos font-mono">—</span>
                </div>
                <div className="flex justify-between py-1 border-b border-border-soft">
                  <span className="text-ink-3">PBO</span>
                  <span className="text-gold font-mono">—</span>
                </div>
                <div className="flex justify-between py-1 border-b border-border-soft">
                  <span className="text-ink-3">Purged K-Fold</span>
                  <span className="text-pos">—</span>
                </div>
                <div className="flex gap-2 mt-2">
                  <button className="row-btn flex-1 rounded border border-border-soft bg-surface-2 px-2 py-1 text-gold">
                    ▶ Walk-Forward
                  </button>
                  <button className="row-btn flex-1 rounded border border-border-soft bg-surface-2 px-2 py-1 text-gold">
                    ▶ Monte-Carlo
                  </button>
                </div>
              </div>
            </V17Card>
          </>
        ) : isLoading ? (
          <V17Card title="策略详情">
            <div className="text-center text-ink-4 text-[11px] py-3">加载中...</div>
          </V17Card>
        ) : error ? (
          <V17Card title="策略详情">
            <div className="text-center text-destructive text-[11px] py-3">加载失败，请稍后重试</div>
          </V17Card>
        ) : (
          <V17Card title="策略详情">
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          </V17Card>
        )}
      </div>
      </div>
    </div>
  );
}
