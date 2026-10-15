"use client";

import { V17Card } from "@/components/shared/page-filter-bar";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useWorkflowStore } from "@/lib/store/workflow";
import type { SelectedTemplate } from "@/lib/store/workflow";
import { useRouter, useSearchParams } from "next/navigation";
import { applyTuningResult as applyTuningResultBridge, type TuningResultSummary } from "@/lib/tuning-bridge";
import { useEffect, useMemo, useState } from "react";
import { FactoryTemplateCard, type StratTemplate } from "./cards/factory-template-card";
import { QDStrategyCard } from "./cards/qd-strategy-card";
import { listQdStrategies, type QDStrategy } from "@/lib/api/qd-strategies";
import { LaunchPanel } from "@/components/strategy/launch-panel";
import { DeployWizardDialog, type WizardTemplate } from "@/components/strategy/deploy-wizard-dialog";
import { AlphaStrategyCard, type SavedStrategy } from "./cards/alpha-strategy-card";
import { TuningResultCard } from "./cards/tuning-result-card";
import { DeploymentCard } from "./cards/deployment-card";
import { useStrategyEvidence } from "@/lib/hooks/use-strategy-evidence";
import {
  listDeployments,
  type DeploymentSummary,
} from "@/lib/api/strategies";
import { deploySavedStrategy } from "@/lib/api/alpha";
import { toastSuccess, toastError } from "@/lib/toast";
import useSWR from "swr";

interface BackendTemplate {
  id?: string;
  template_id?: string;
  name?: string;
  display_name?: string;
  category?: string;
  cat?: string;
  sharpe?: number;
  win_rate?: number;
  winrate?: number;
  /** false = 目录里挂着但无实现,不能回测/部署(见 factory.py `_UNIMPLEMENTED_KEYS`)。 */
  runnable?: boolean;
}

/** P11: SavedStrategy → SelectedTemplate (5 字段映射: id←name@version, name, category="alpha") */
export function adaptSavedStrategy(s: SavedStrategy): SelectedTemplate {
  return {
    id: `${s.name}@${s.version}`,
    name: s.name,
    category: "alpha",
  };
}

/** factory-templates 全量行 → 向导模板(向导要 default_params/risk_params/symbols/interval) */
interface BackendTemplateFull extends BackendTemplate {
  name_zh?: string;
  description?: string;
  default_params?: Record<string, unknown>;
  risk_params?: Record<string, unknown>;
  symbols?: string[];
  interval?: string;
}

function toWizardTemplate(t: BackendTemplateFull): WizardTemplate {
  return {
    id: String(t.id ?? t.template_id ?? "?"),
    name: t.name ?? t.display_name ?? "?",
    nameZh: t.name_zh,
    category: t.cat ?? t.category,
    description: t.description,
    defaultParams: t.default_params ?? {},
    riskParams: t.risk_params ?? {},
    symbols: t.symbols?.length ? t.symbols : ["BTC/USDT"],
    interval: t.interval ?? "1h",
  };
}

/** US-3 部署列表三态过滤。后端 Status 只有 running/paused/stopped(strategies.py:1115/1160/1477/1497),
 * model 无 error 字段 →「需关注」= status !== "running"(暂停/停止都算需要人看一眼)。 */
export type DeployFilterKey = "all" | "running" | "attention";

export const DEPLOY_FILTERS: {
  key: DeployFilterKey;
  label: string;
  match: (list: DeploymentSummary[]) => DeploymentSummary[];
}[] = [
  { key: "all", label: "全部", match: (l) => l },
  { key: "running", label: "运行中", match: (l) => l.filter((d) => d.status === "running") },
  { key: "attention", label: "需关注", match: (l) => l.filter((d) => d.status !== "running") },
];

/** 卡片级加载失败行:「服务挂」与「暂无数据」分开渲染的诚实态。
 * retry 调 SWR mutate 重新拉取;不重试就永远停在假"暂无"上。 */
export function CardLoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="text-center text-[11px] py-6 text-destructive">
      加载失败。{" "}
      <button
        type="button"
        onClick={onRetry}
        className="underline underline-offset-2 hover:text-ink-2"
      >
        重试
      </button>
    </div>
  );
}

// 设计稿 strat 策略工厂 — 3 section 卡片网格
export function StratPage() {
  // distinguish=true:错误走 SWR error 通道,「加载失败」与「暂无」分开渲染
  // (fetcher 吞错时后端挂了显示"暂无"= 撒谎,Live 部署可能正在跑)。
  const { useApi } = useApiPage({ distinguish: true });
  const {
    data: realTemplates,
    error: templatesErr,
    mutate: reloadTemplates,
  } = useApi<BackendTemplate[] | { templates: BackendTemplate[] }>(
    "/api/v1/strategies/factory-templates"
  );
  const {
    data: alphaStrategiesRaw,
    error: alphaErr,
    mutate: reloadAlpha,
  } = useApi<{ strategies: SavedStrategy[] }>("/api/v1/alpha/saved-strategies");
  const {
    data: tuningResultsRaw,
    error: tuningErr,
    mutate: reloadTuning,
  } = useApi<TuningResultSummary[]>("/api/v1/research/tuning/results");
  const {
    data: deployments,
    error: deployErr,
    mutate: reloadDeployments,
  } = useSWR<DeploymentSummary[]>(
    "/api/v1/strategies/deployments/list",
    () => listDeployments(),
    // 15s 轮询:部署状态不刷新则"运行中"pill 可信寿命=页面打开瞬间(workbench runner 同款间隔)
    { revalidateOnFocus: false, dedupingInterval: 5000, refreshInterval: 15_000 }
  );
  const {
    data: qdStrategies,
    error: qdErr,
    mutate: reloadQd,
  } = useSWR<QDStrategy[]>(
    "/api/v1/strategies/qd-templates",
    () => listQdStrategies(),
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );
  const selectTemplate = useWorkflowStore((s) => s.selectTemplate);
  const setOptimization = useWorkflowStore((s) => s.setOptimization);
  // P0-2 证据行:列表页一次聚合,禁每卡一请求(120/min 限流)
  const { evidence } = useStrategyEvidence();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [applyingTuning, setApplyingTuning] = useState<string | null>(null);
  const [deployingAlpha, setDeployingAlpha] = useState<string | null>(null);
  const [deployFilter, setDeployFilter] = useState<DeployFilterKey>("all");

  // US-2 部署向导状态(替代原盲部署:一键 createFromTemplate→deploy 无参数确认无免责)
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardTemplate, setWizardTemplate] = useState<WizardTemplate | null>(null);
  const [wizardMode, setWizardMode] = useState<"paper" | "live">("paper");

  const openWizard = (t: StratTemplate, mode: "paper" | "live") => {
    // StratTemplate 是裁剪版(仅 id/name/cat/sharpe/winrate);全量 default_params 从 realTemplates 找
    const full = fullTemplates.find((f) => f.id === t.id || f.template_id === t.id);
    setWizardTemplate(
      full
        ? toWizardTemplate(full)
        : { id: t.id, name: t.name, defaultParams: {}, riskParams: {}, symbols: ["BTC/USDT"], interval: "1h" },
    );
    setWizardMode(mode);
    setWizardOpen(true);
  };

  // 深链 ?deploy=1&template=<id> 直开向导(对齐 QD URL 即状态;模式可选 ?mode=live)。
  // P3 竞态修复:realTemplates 未回就执行时 openWizard find 不到全量 default_params,
  // 向导参数落空——守卫到数据就绪(或明确失败)再开。失败也放行:请求 429/挂掉时
  // 静默不弹窗 = URL 承诺了却不兑现,比 stub 兜底(诚实渲染"无参数")更糟。
  useEffect(() => {
    if (!realTemplates && !templatesErr) return;
    if (searchParams.get("deploy") === "1") {
      const tid = searchParams.get("template");
      if (tid && !wizardOpen) {
        openWizard({ id: tid, name: tid, cat: "", sharpe: 0, winrate: 0 }, searchParams.get("mode") === "live" ? "live" : "paper");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, realTemplates, templatesErr]);

  const deployAlpha = async (s: SavedStrategy) => {
    if (deployingAlpha) return;
    setDeployingAlpha(s.name);
    try {
      await deploySavedStrategy(s.name);
      toastSuccess(`Alpha 策略 ${s.name} 已部署 Paper`);
      await reloadDeployments();
    } catch (e: unknown) {
      toastError(`部署失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDeployingAlpha(null);
    }
  };

  const templates: StratTemplate[] = (() => {
    const list: BackendTemplateFull[] = Array.isArray(realTemplates)
      ? realTemplates
      : (realTemplates?.templates ?? []);
    if (list.length === 0) return [];
    return list.map((t) => ({
      id: String(t.id ?? t.template_id ?? "?"),
      name: t.name ?? t.display_name ?? "?",
      cat: t.cat ?? t.category ?? "—",
      sharpe: Number(t.sharpe ?? 0),
      winrate: Number(t.winrate ?? t.win_rate ?? 0),
      runnable: t.runnable !== false,
    }));
  })();

  const fullTemplates: BackendTemplateFull[] = useMemo(() => {
    return Array.isArray(realTemplates)
      ? realTemplates
      : (realTemplates?.templates ?? []);
  }, [realTemplates]);

  const alphaStrategies: SavedStrategy[] = alphaStrategiesRaw?.strategies ?? [];
  const tuningResults: TuningResultSummary[] = tuningResultsRaw ?? [];
  const deploymentList: DeploymentSummary[] = deployments ?? [];
  const visibleDeployments = useMemo(() => {
    const f = DEPLOY_FILTERS.find((x) => x.key === deployFilter) ?? DEPLOY_FILTERS[0];
    return f.match(deploymentList);
  }, [deployFilter, deploymentList]);
  const qdList: QDStrategy[] = qdStrategies ?? [];

  const selectAndGo = (t: StratTemplate, path: string) => {
    selectTemplate({
      id: t.id,
      name: t.name,
      category: t.cat,
      sharpe: t.sharpe,
      winrate: t.winrate,
    });
    router.push(path);
  };

  return (
    <div className="h-full overflow-y-auto"><div className="grid gap-2" style={{ gridTemplateColumns: "1fr var(--rail-w)" }}>
      <div className="v17-page-grid grid gap-2">
        <V17Card title={`策略模板库 (${templates.length})`}>
          {templatesErr ? (
            <CardLoadError onRetry={() => void reloadTemplates()} />
          ) : templates.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-6">
              暂无策略模板。后端 /api/v1/strategies/factory-templates 返回空。
            </div>
          ) : (
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              {templates.map((t) => (
                <FactoryTemplateCard
                  key={t.id}
                  t={t}
                  evidence={evidence?.get(t.id)}
                  onCardClick={() => selectAndGo(t, "/research/factors")}
                  onBacktest={() => selectAndGo(t, "/trade/backtests")}
                  onDeployPaper={() => openWizard(t, "paper")}
                  onDeployLive={() => openWizard(t, "live")}
                  deployingPaper={false}
                  deployingLive={false}
                />
              ))}
            </div>
          )}
        </V17Card>

        <V17Card title={`QuantDinger 策略库 (${qdList.length})`}>
          {qdErr ? (
            <CardLoadError onRetry={() => void reloadQd()} />
          ) : qdList.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-6">
              未发现 QD 策略导出。设置环境变量 SELENE_QD_STRATEGY_DIR 指向备份库目录。
            </div>
          ) : (
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              {qdList.map((s) => (
                <QDStrategyCard key={s.id} s={s} evidence={evidence?.get(s.id)} />
              ))}
            </div>
          )}
        </V17Card>

        <V17Card title={`Alpha 挖矿策略 (${alphaStrategies.length})`}>
          {alphaErr ? (
            <CardLoadError onRetry={() => void reloadAlpha()} />
          ) : alphaStrategies.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-6">
              暂无 Alpha 策略。运行 Optuna 公式搜索或 Alpha 挖矿以生成策略。
            </div>
          ) : (
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              {alphaStrategies.map((s) => (
                <AlphaStrategyCard
                  key={`${s.version}-${s.name}`}
                  s={s}
                  evidence={evidence?.get(`${s.name}@${s.version}`)}
                  onClick={() => {
                    selectTemplate(adaptSavedStrategy(s));
                    router.push("/research/factors");
                  }}
                  onDeployPaper={() => deployAlpha(s)}
                  deployingPaper={deployingAlpha === s.name}
                />
              ))}
            </div>
          )}
        </V17Card>

        <V17Card title={`最近调参结果 (${tuningResults.length})`}>
          {tuningErr ? (
            <CardLoadError onRetry={() => void reloadTuning()} />
          ) : tuningResults.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-6">
              暂无调参结果。运行 LLM 参数优化以生成最佳参数。
            </div>
          ) : (
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              {tuningResults.slice(0, 9).map((r, idx) => (
                <TuningResultCard
                  key={`${r.file_name}-${idx}`}
                  r={r}
                  applying={applyingTuning === r.strategy_id}
                  onClick={async () => {
                    setApplyingTuning(r.strategy_id);
                    await applyTuningResultBridge(r, router, setOptimization);
                    setApplyingTuning(null);
                  }}
                />
              ))}
            </div>
          )}
        </V17Card>

        <V17Card
          title={`已部署策略 (${deploymentList.length})`}
          right={
            <span className="flex items-center gap-1">
              {DEPLOY_FILTERS.map((f) => {
                const n = f.match(deploymentList).length;
                const on = deployFilter === f.key;
                return (
                  <button
                    key={f.key}
                    type="button"
                    onClick={() => setDeployFilter(f.key)}
                    aria-pressed={on}
                    className={`rounded px-1.5 py-1 text-[9px] transition-colors ${
                      on ? "bg-gold/15 text-gold" : "text-ink-4 hover:text-ink-2 hover:bg-surface-3"
                    }`}
                  >
                    {f.label} {n}
                  </button>
                );
              })}
            </span>
          }
        >
          {deployErr ? (
            <CardLoadError onRetry={() => void reloadDeployments()} />
          ) : deploymentList.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-6">
              暂无已部署策略。从模板库或 Alpha 策略部署以创建运行实例。
            </div>
          ) : visibleDeployments.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-6">
              当前筛选下无部署。
            </div>
          ) : (
            <>
              {/* 行级主入口:策略名直达工作台(此前只有卡片内 9px 小按钮,三级深) */}
              <div className="mb-2 flex flex-col gap-1">
                {visibleDeployments.map((d) => (
                  <a
                    key={d.id}
                    href={`/research/strategies-strategy-id?id=${encodeURIComponent(d.strategy_id)}`}
                    className="flex items-center gap-2 rounded border border-border-soft px-2 py-1 text-[10px] text-ink-2 hover:bg-muted"
                    title="打开策略工作台(参数/版本/监控/回测)"
                  >
                    <span className="flex-1 truncate">
                      {d.strategy_name ?? d.strategy_id}
                    </span>
                    <span className="font-mono text-[9px] text-ink-4">
                      {d.strategy_id}
                    </span>
                    <span className="text-[9px] text-sky-400">打开工作台 →</span>
                  </a>
                ))}
              </div>
              <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
                {visibleDeployments.map((d) => (
                  <DeploymentCard
                    key={d.id}
                    d={d}
                    onChanged={() => reloadDeployments()}
                  />
                ))}
              </div>
            </>
          )}
        </V17Card>
      </div>

      <LaunchPanel
        templates={templates}
        deployments={deploymentList}
        onDeployPaper={async (t) => openWizard(t, "paper")}
        onDeployLive={async (t) => openWizard(t, "live")}
      />

      <DeployWizardDialog
        template={wizardTemplate}
        initialMode={wizardMode}
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        onDeployed={() => void reloadDeployments()}
      />
      </div>
    </div>
  );
}
