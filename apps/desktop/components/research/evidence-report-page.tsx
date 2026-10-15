"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { getEvidenceReport, type EvidenceReport } from "@/lib/api/experiments";
import { GitBranch, Loader2, FileSearch, FlaskConical } from "lucide-react";

function fmtNum(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) return v.toLocaleString();
  return "—";
}

function fmtRate(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) return (v * 100).toFixed(1) + "%";
  return "—";
}

function fmtSigned(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) {
    return v > 0 ? `+${v.toFixed(3)}` : v.toFixed(3);
  }
  return "—";
}

// 证据报告 — "Git for Quant Research" 的展示层:把实验固化成可追溯结论。
// 数据来自 GET /api/v1/experiments/{id}/evidence-report(bare dict)
// ?id= 决定看哪条实验;blood-line 可视化 = ancestors ← 当前 → children
export function ExperimentEvidencePage() {
  const params = useSearchParams();
  const urlId = params.get("id") ?? undefined;
  const [expId, setExpId] = useState<string | undefined>(urlId);
  const [inputId, setInputId] = useState<string>(urlId ?? "");

  const { useApi } = useApiPage({ distinguish: true });
  const { data, error, isLoading, mutate } = useApi<EvidenceReport | null>(
    expId ? `/api/v1/experiments/${encodeURIComponent(expId)}/evidence-report` : null,
  );
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    if (urlId) {
      setExpId(urlId);
      setInputId(urlId);
    }
  }, [urlId]);

  const handleSubmit = () => {
    const id = inputId.trim();
    if (!id) return;
    setExpId(id);
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const report = data ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="flex shrink-0 items-center gap-2">
        <FileSearch className="h-4 w-4 text-primary" />
        <h2 className="text-[13px] font-semibold text-foreground">证据报告</h2>
        {expId && <Badge variant="outline" className="font-mono text-[10px]">{expId}</Badge>}
        <div className="ml-auto flex items-center gap-2">
          <input
            value={inputId}
            onChange={(e) => setInputId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            placeholder="exp-xxx"
            className="h-6 w-40 rounded-lg border border-border-soft bg-surface px-2 font-mono text-[11px] outline-none focus:border-primary"
          />
          <Button size="sm" variant="outline" className="h-6 gap-1 rounded-lg text-[10px]" onClick={handleSubmit}>
            打开
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 gap-1 rounded-lg text-[10px]"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            {refreshing ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            刷新
          </Button>
        </div>
      </div>

      {!expId ? (
        <EmptyState text="在上方输入实验 id(如 exp-xxxx)打开它的证据报告" />
      ) : isLoading ? (
        <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> 加载证据报告…
        </div>
      ) : error ? (
        <div className="flex h-full items-center justify-center text-xs text-destructive">
          证据服务不可用：{error.message}
        </div>
      ) : !report ? (
        <EmptyState text="实验不存在或证据为空" />
      ) : (
        <EvidenceReportBody report={report} onOpen={(id) => setExpId(id)} />
      )}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex flex-1 items-center justify-center text-center text-[12px] text-muted-foreground">
      <p className="max-w-[280px] leading-5">{text}</p>
    </div>
  );
}

function EvidenceReportBody({ report, onOpen }: {
  report: EvidenceReport;
  onOpen: (id: string) => void;
}) {
  const { experiment, data_source, config, version, result, robustness, risk, conclusion, lineage } = report;
  return (
    <div className="grid min-h-0 flex-1 auto-rows-min gap-3 overflow-y-auto md:grid-cols-2">
      {/* 血缘链 blood-line 可视化 */}
      <Card className="md:col-span-2">
        <CardHeader className="px-3 py-2.5">
          <CardTitle className="flex items-center gap-2 text-[13px] font-medium">
            <GitBranch className="h-4 w-4 text-violet-400" /> 实验血缘
            <Badge variant="outline" className="text-[10px]">
              {lineage.ancestors.length} 祖代 · {lineage.children.length} 子代
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-3 pt-0">
          <LineageFlow
            ancestors={lineage.ancestors}
            current={{ id: experiment.id, name: experiment.name, created_at: version.created_at }}
            children={lineage.children}
            onOpen={onOpen}
          />
        </CardContent>
      </Card>

      {/* 头部：身份 + 结论 */}
      <Card className="md:col-span-2">
        <CardContent className="space-y-2 px-3 py-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h3 className="text-[15px] font-semibold leading-6">{experiment.name}</h3>
              <p className="text-[11px] text-muted-foreground">
                {experiment.symbol ?? "—"} · {experiment.timeframe ?? "—"} · {experiment.strategy_version ?? "—"}
                {experiment.created_at && <span className="ml-2">登记 {experiment.created_at}</span>}
              </p>
            </div>
            {robustness.has_backtest ? (
              <Badge className="shrink-0 text-[10px]">已回测</Badge>
            ) : (
              <Badge variant="outline" className="shrink-0 text-[10px]">未回测</Badge>
            )}
          </div>
          {experiment.hypothesis && (
            <p className="text-[12px] leading-5 text-muted-foreground">
              <span className="font-medium text-foreground/80">假设：</span>
              {experiment.hypothesis}
            </p>
          )}
          <div className="rounded-lg border border-border-soft bg-surface px-3 py-2 text-[12px] leading-5">
            <span className="font-medium">结论：</span>
            {conclusion}
          </div>
        </CardContent>
      </Card>

      {/* 数据源 / 配置 / 版本 */}
      <V17Card title="数据源">
        <div className="grid grid-cols-2 gap-1.5">
          <StatBlock label="数据集" value={data_source.dataset ?? "—"} />
          <StatBlock label="品种" value={data_source.symbol ?? "—"} />
          <StatBlock label="周期" value={data_source.timeframe ?? "—"} />
        </div>
      </V17Card>
      <V17Card title="策略配置">
        <div className="grid grid-cols-2 gap-1.5">
          <StatBlock label="策略版本" value={config.strategy_version ?? "—"} />
          <StatBlock label="方向" value={config.setup_params?.direction ?? "—"} />
          <StatBlock label="入场" value={fmtNum(config.setup_params?.entry)} />
          <StatBlock label="止损" value={fmtNum(config.setup_params?.stop_loss)} />
          <StatBlock label="止盈" value={fmtNum(config.setup_params?.take_profit)} />
        </div>
      </V17Card>

      {/* 回测结果 */}
      <V17Card title="回测结果">
        <div className="grid grid-cols-2 gap-1.5">
          <StatBlock label="样本数" value={fmtNum(result.total_setups)} />
          <StatBlock label="胜率" value={fmtRate(result.win_rate)} />
          <StatBlock label="单笔期望" value={fmtSigned(result.expectancy)} cls={typeof result.expectancy === "number" && result.expectancy > 0 ? "text-pos" : "text-neg"} />
          <StatBlock label="利润因子" value={fmtRate(result.profit_factor)} />
          <StatBlock label="最大回撤" value={fmtRate(result.max_drawdown)} />
          <StatBlock label="均盈" value={fmtNum(result.avg_win)} />
        </div>
      </V17Card>

      {/* 稳健性 / 风险 */}
      <V17Card title="稳健性">
        {robustness.notes.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">无回测结果，暂无稳健性观察。</p>
        ) : (
          <ul className="list-disc space-y-1 pl-4 text-[11px] leading-4 text-foreground/80">
            {robustness.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </V17Card>
      <V17Card title="风险">
        <div className="grid grid-cols-2 gap-1.5">
          <StatBlock label="最大回撤" value={fmtRate(risk.max_drawdown)} />
          {risk.risk_per_trade ? (
            <>
              <StatBlock label="单次风险" value={fmtNum(risk.risk_per_trade.amount)} />
              <StatBlock label="方向" value={risk.risk_per_trade.direction} />
              <StatBlock label="入场→SL" value={`${fmtNum(risk.risk_per_trade.entry)} → ${fmtNum(risk.risk_per_trade.stop_loss)}`} />
            </>
          ) : (
            <StatBlock label="单次风险" value="—" />
          )}
        </div>
      </V17Card>
    </div>
  );
}

// 血缘横向流:祖先(左)→ 当前(中,高亮)→ 子代(右)。点击节点可跳转。
function LineageFlow({ ancestors, current, children, onOpen }: {
  ancestors: { id: string; name: string; created_at?: string | null }[];
  current: { id: string; name: string; created_at?: string | null };
  children: { id: string; name: string; created_at?: string | null }[];
  onOpen: (id: string) => void;
}) {
  const left = ancestors.length === 0 ? [] : [...ancestors].reverse(); // 根 → 父(左 → 右)
  return (
    <div className="flex items-start gap-1 overflow-x-auto pb-1">
      <LineageNode id="root" name="起源" kind="root" onOpen={onOpen} />
      {left.map((n) => (
        <span key={n.id} className="flex items-center gap-1">
          <LineageConnector />
          <LineageNode id={n.id} name={n.name} kind="ancestor" onOpen={onOpen} />
        </span>
      ))}
      <span className="flex items-center gap-1">
        <LineageConnector />
        <LineageNode id={current.id} name={current.name} kind="current" onOpen={onOpen} />
      </span>
      {children.map((n) => (
        <span key={n.id} className="flex items-center gap-1">
          <LineageConnector />
          <LineageNode id={n.id} name={n.name} kind="child" onOpen={onOpen} />
        </span>
      ))}
    </div>
  );
}

function LineageConnector() {
  return <span className="h-px w-3 shrink-0 bg-border-soft" />;
}

function LineageNode({ id, name, kind, onOpen }: {
  id: string;
  name: string;
  kind: "root" | "ancestor" | "current" | "child";
  onOpen: (id: string) => void;
}) {
  const isCurrent = kind === "current";
  const cls = isCurrent
    ? "border-primary bg-primary/10 text-foreground font-medium"
    : kind === "root"
      ? "border-border-soft bg-surface text-muted-foreground"
      : "border-violet-400/40 bg-surface text-foreground/80 hover:border-violet-400";
  return (
    <button
      onClick={() => onOpen(id)}
      className={`flex max-w-[140px] shrink-0 flex-col items-start rounded-lg border px-2 py-1 text-left text-[10px] leading-3 transition-colors ${cls}`}
      title={id}
    >
      <span className="max-w-full truncate">{name}</span>
      <span className="font-mono text-[8px] text-muted-foreground">{id}</span>
    </button>
  );
}
