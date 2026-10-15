"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from "@/components/ui/table";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { CurveChart } from "@/components/strategy/shared-charts";
import { useApiPage } from "@/lib/hooks/use-api-page";
import {
  TOP_FORMULAS_ENDPOINT,
  VALIDATION_ENDPOINT,
  findValidationByHash,
  firstValidationDataset,
  type TopFormulasResult,
  type ValidationReport,
  type TopFormula,
  type ValidationFormula,
  type OverfitAudit,
} from "@/lib/api/alpha-mining";

const fmt = (v: number | null | undefined, digits = 3): string =>
  v == null || !Number.isFinite(v) ? "—" : v.toFixed(digits);

const fmtPct = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? "—" : `${(v * 100).toFixed(1)}%`;

function verdictBadge(verdict: string): React.JSX.Element {
  switch (verdict) {
    case "VALID":
      return <Badge variant="success">{verdict}</Badge>;
    case "SUSPICIOUS":
      return <Badge variant="warning">{verdict}</Badge>;
    case "INVALID":
      return <Badge variant="destructive">{verdict}</Badge>;
    default:
      return <Badge variant="outline">{verdict || "未判定"}</Badge>;
  }
}

function ConfigStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
      <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className="mt-1 truncate font-mono text-xs font-medium text-foreground" title={value}>
        {value}
      </p>
    </div>
  );
}

function AuditStat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
      <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className={`mt-1 font-mono text-sm font-medium ${tone ?? "text-foreground"}`}>{value}</p>
    </div>
  );
}

function IcChart({ formula }: { formula: ValidationFormula }) {
  const data = formula.rolling_ic.map((v, i) => ({ subperiod: i + 1, ic: Number(v.toFixed(4)) }));
  if (data.length < 2) {
    return <p className="text-sm text-muted-foreground">IC 序列不足。</p>;
  }
  return (
    <div className="h-[220px]">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis dataKey="subperiod" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} />
          <Tooltip />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.3)" />
          <ReferenceLine y={formula.train_mean_ic} stroke="#e3b650" strokeDasharray="4 3" /> {/* 正典金=var(--color-gold) */}
          <Line type="monotone" dataKey="ic" stroke="var(--color-accent)" strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function CumulativeIcCurve({ formula }: { formula: ValidationFormula }) {
  // 净值曲线:复用 house CurveChart,绘制跨子区间的累积 OOS IC(诚实标注,非合成净值)
  const cumulative = formula.rolling_ic.map((_, i) =>
    formula.rolling_ic.slice(0, i + 1).reduce((sum, v) => sum + v, 0)
  );
  return <CurveChart curve={cumulative} emptyLabel="暂无曲线数据" height={220} />;
}

function AuditCard({ audit }: { audit: OverfitAudit }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">DSR / PBO 审计</CardTitle>
        <CardDescription>诚实审计 — 未通过也如实展示全部数值</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-center gap-3">
          <Badge variant={audit.passed ? "success" : "destructive"}>{audit.passed ? "PASS" : "FAIL"}</Badge>
          <span className="text-xs text-muted-foreground">{audit.verdict}</span>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <AuditStat label="DSR" value={fmt(audit.deflated_sharpe_ratio)} tone={audit.passed ? "text-emerald-400" : "text-red-400"} />
          <AuditStat label="PBO" value={fmt(audit.pbo)} />
          <AuditStat label="Observed Sharpe (Ann.)" value={fmt(audit.observed_sharpe_annual)} />
          <AuditStat label="PSR vs 0" value={fmt(audit.psr_vs_zero)} />
          <AuditStat label="Haircut Sharpe" value={fmt(audit.haircut_sharpe_annual)} />
          <AuditStat label="Haircut %" value={fmtPct(audit.haircut_pct)} />
          <AuditStat label="MinTRL" value={fmt(audit.minimum_track_record_length)} />
          <AuditStat label="n_obs / n_trials" value={`${audit.n_obs} / ${audit.n_trials}`} />
          <AuditStat label="skew / kurtosis" value={`${fmt(audit.skew)} / ${fmt(audit.kurtosis)}`} />
          <AuditStat label="基准 SR0 (Ann.)" value={fmt(audit.deflation_benchmark_sr0_annual)} />
        </div>
      </CardContent>
    </Card>
  );
}

export function AlphaMiningResultsPanel() {
  const { useApi } = useApiPage();
  const top = useApi<TopFormulasResult>(TOP_FORMULAS_ENDPOINT);
  const val = useApi<ValidationReport>(VALIDATION_ENDPOINT);

  const loading = top.isLoading || val.isLoading;
  const [selectedRank, setSelectedRank] = useState<number>(1);

  const topRows: TopFormula[] = top.data?.top_formulas ?? [];
  const selectedTop = topRows.find((r) => r.rank === selectedRank) ?? topRows[0] ?? null;
  const selectedVal = selectedTop ? findValidationByHash(val.data, selectedTop.canonical_hash) : null;
  const audit = selectedVal ? firstValidationDataset(selectedVal)?.overfit ?? null : null;

  if (loading) {
    return (
      <div className="space-y-4 p-4">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (!top.data && !val.data) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="max-w-md text-center">
          <h3 className="text-base font-semibold text-foreground">未发现搜索运行</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            未找到 top_formulas / validation_report 产物(internal-archive/)。请先运行确定性遗传搜索
            (python -m selene_space_factor_research.mining.runner),或确认后端已实现产物读取端点。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      {/* 搜索任务配置(只读) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">搜索任务</CardTitle>
          <CardDescription>确定性遗传搜索元信息(只读)</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
            <ConfigStat label="数据集" value={top.data ? top.data.symbols.join(" / ") : "—"} />
            <ConfigStat label="周期" value={top.data?.interval ?? "—"} />
            <ConfigStat label="K线/符号" value={top.data ? String(top.data.bars_per_symbol) : "—"} />
            <ConfigStat label="Seed" value={top.data ? String(top.data.seed) : "—"} />
            <ConfigStat label="预算" value={top.data ? `${top.data.evaluations_used}/${top.data.eval_budget}` : "—"} />
            <ConfigStat label="代数" value={top.data ? String(top.data.generations) : "—"} />
            <ConfigStat label="停止原因" value={top.data?.stopped_reason ?? "—"} />
            {val.data ? <ConfigStat label="折数" value={String(val.data.config.fold_config.n_folds)} /> : null}
            {val.data ? <ConfigStat label="OOS IC 阈值" value={String(val.data.config.thresholds.oos_ic_min)} /> : null}
            {val.data ? <ConfigStat label="DSR 阈值" value={String(val.data.config.thresholds.dsr_threshold)} /> : null}
          </div>
        </CardContent>
      </Card>

      {/* Top-N 公式表 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Top-N 公式</CardTitle>
          <CardDescription>按 fitness 排序;判定来自验证报告(未生成时显示未判定)</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">#</TableHead>
                <TableHead>公式</TableHead>
                <TableHead className="text-right">Fitness</TableHead>
                <TableHead className="text-right">Rank-IC</TableHead>
                <TableHead className="text-right">LS Sharpe</TableHead>
                <TableHead className="text-right">OOS IC</TableHead>
                <TableHead className="text-right">OOS Sharpe</TableHead>
                <TableHead className="text-right">判定</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {topRows.map((r) => {
                const v = findValidationByHash(val.data, r.canonical_hash);
                return (
                  <TableRow
                    key={r.canonical_hash}
                    className={`cursor-pointer ${r.rank === selectedTop?.rank ? "bg-accent/40" : ""}`}
                    onClick={() => setSelectedRank(r.rank)}
                  >
                    <TableCell className="font-mono text-xs">{r.rank}</TableCell>
                    <TableCell className="max-w-[280px]">
                      <span className="block truncate font-mono text-xs" title={r.readable}>
                        {r.readable}
                      </span>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmt(r.score)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmt(r.rank_ic)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmt(r.ls_net_sharpe)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmt(r.oos_rank_ic)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmt(r.oos_ls_net_sharpe)}</TableCell>
                    <TableCell className="text-right">
                      {v ? verdictBadge(v.verdict) : <Badge variant="outline">未判定</Badge>}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* 选中公式详情:指标 + IC 面板/净值曲线 + DSR/PBO 审计 */}
      {selectedTop ? (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">公式详情 — Rank #{selectedTop.rank}</CardTitle>
              <CardDescription className="font-mono text-xs">{selectedTop.readable}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
                <AuditStat label="Fitness" value={fmt(selectedTop.score)} />
                <AuditStat label="Rank-IC" value={fmt(selectedTop.rank_ic)} />
                <AuditStat label="OOS Rank-IC" value={fmt(selectedTop.oos_rank_ic)} />
                <AuditStat label="LS Sharpe" value={fmt(selectedTop.ls_net_sharpe)} />
                <AuditStat label="OOS LS Sharpe" value={fmt(selectedTop.oos_ls_net_sharpe)} />
                <AuditStat label="换手" value={fmtPct(selectedTop.turnover)} />
                <AuditStat label="IC 稳定性" value={fmt(selectedTop.ic_stability)} />
                <AuditStat label="子区间一致" value={fmt(selectedTop.subperiod_ic_consistency)} />
              </div>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">IC 面板</CardTitle>
                <CardDescription>滚动 OOS Rank-IC(子区间)</CardDescription>
              </CardHeader>
              <CardContent>
                {selectedVal ? (
                  <IcChart formula={selectedVal} />
                ) : (
                  <p className="text-sm text-muted-foreground">验证报告未生成,暂无 IC 序列。</p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">净值曲线</CardTitle>
                <CardDescription>累积 OOS Rank-IC(跨子区间)</CardDescription>
              </CardHeader>
              <CardContent>
                {selectedVal ? (
                  <CumulativeIcCurve formula={selectedVal} />
                ) : (
                  <p className="text-sm text-muted-foreground">验证报告未生成,暂无曲线。</p>
                )}
              </CardContent>
            </Card>
          </div>

          {audit && selectedVal ? <AuditCard audit={audit} /> : null}
        </div>
      ) : null}

      {/* 判定表 */}
      {val.data && val.data.formulas.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">判定表</CardTitle>
            <CardDescription>验证协议输出 — 总览与逐公式判定</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-4 flex flex-wrap items-center gap-2">
              {Object.entries(val.data.summary).map(([k, n]) => (
                <Badge
                  key={k}
                  variant={
                    k === "VALID" ? "success" : k === "SUSPICIOUS" ? "warning" : k === "INVALID" ? "destructive" : "outline"
                  }
                >
                  {k} × {n}
                </Badge>
              ))}
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>公式</TableHead>
                  <TableHead className="text-right">判定</TableHead>
                  <TableHead className="text-right">决策</TableHead>
                  <TableHead>原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {val.data.formulas.map((f) => (
                  <TableRow key={f.canonical_hash}>
                    <TableCell className="max-w-[280px]">
                      <span className="block truncate font-mono text-xs" title={f.readable}>
                        {f.readable}
                      </span>
                    </TableCell>
                    <TableCell className="text-right">{verdictBadge(f.verdict)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{f.decision}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{f.reason}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent>
            <p className="py-4 text-sm text-muted-foreground">
              验证报告未生成 — 运行验证协议后可见判定表。
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
