"use client";

import { useCallback, useEffect, useState } from "react";
import { apiDirectRaw, ApiError } from "@/lib/api-client";
import { MiniLineChart, fmtNum } from "@/components/research/diagnostic-charts";
import { V17Card } from "@/components/shared/page-filter-bar";

// ashare router returns bare dicts on success (envelope only on error) —
// unwrap like ashare-fundamentals-page.tsx does
async function ashareGet<T>(path: string, init?: RequestInit): Promise<T> {
  const body = await apiDirectRaw<unknown>(path, init);
  if (
    body &&
    typeof body === "object" &&
    "data" in body &&
    "meta" in body
  ) {
    const envelope = body as { data: T | null; error?: { message?: string } };
    if (envelope.error) {
      throw new ApiError(envelope.error.message ?? "request failed", 500);
    }
    return envelope.data as T;
  }
  return body as T;
}

interface SpreadPoint {
  date: string;
  spread: number;
}

interface StyleInfo {
  regime: string;
  cum_spread?: number | null;
  n_days?: number;
  spread_series?: SpreadPoint[];
}

interface StyleReport {
  forward_col: string;
  lookback: number;
  n_symbols: number;
  n_days: number;
  styles: Record<string, StyleInfo>;
  orthogonal?: OrthogonalReport | null;
}

interface OrthogonalKept {
  factor: string;
  IC_mean: number | null;
  IR: number | null;
  IC_pos_rate: number | null;
  t_stat: number | null;
}

interface OrthogonalReport {
  kept: OrthogonalKept[];
  dropped: { factor: string; reason: string }[];
  order: string[];
  params: { min_abs_t: number; max_factors: number | null };
}

const STYLE_META: Record<
  string,
  { label: string; color: string; hint: string }
> = {
  SIZE: { label: "市值 SIZE", color: "#0f766e", hint: "正 spread = 小市值组跑赢" },
  BP: { label: "价值 BP", color: "#92400e", hint: "正 spread = 高账面市值比组跑赢" },
  YOY_NI: {
    label: "成长 YOY_NI",
    color: "#1d4ed8",
    hint: "正 spread = 高净利增速组跑赢",
  },
};

// 徽章：house 约定 = 语义色文字 + 低透明底（bg-<hue>/15 text-<hue>），
// 与 43 个文件的错误/状态样式同族。原先用 bg-emerald-100/bg-red-100 浅色底
// 但未设文字色 → 继承深主题的浅文字，对比度 1.01:1（文字近不可见）。
const REGIME_STYLES: Record<string, { badge: string; text: string }> = {
  leading: { badge: "bg-emerald-500/15 text-emerald-400", text: "领先" },
  lagging: { badge: "bg-red-500/15 text-red-400", text: "落后" },
  factor_absent: { badge: "bg-gray-500/15 text-gray-400", text: "无数据" },
  no_data: { badge: "bg-gray-500/15 text-gray-400", text: "无数据" },
};

function StyleCard({ name, info }: { name: string; info: StyleInfo }) {
  const meta = STYLE_META[name] ?? {
    label: name,
    color: "#44403c",
    hint: "",
  };
  const regime = REGIME_STYLES[info.regime] ?? REGIME_STYLES.no_data;
  const series = info.spread_series ?? [];
  return (
    <V17Card title={meta.label}>
      <div className="flex items-center gap-2">
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${regime.badge}`}
          data-testid={`style-badge-${name}`}
        >
          {regime.text}
        </span>
        <span className="text-xs text-muted-foreground">{meta.hint}</span>
      </div>
      {info.regime === "factor_absent" ? (
        <p className="mt-2 text-xs text-muted-foreground">
          面板中无该因子列（财务缓存缺失或未激活）
        </p>
      ) : (
        <>
          <div className="mt-2 flex gap-4 text-sm">
            <span>
              累计 spread（近 {info.n_days ?? "—"} 日）：
              <span className="font-mono">{fmtNum(info.cum_spread ?? null)}</span>
            </span>
          </div>
          <div className="mt-2">
            <MiniLineChart
              dates={series.map((p) => p.date)}
              series={[
                {
                  name: `${meta.label} 日度 spread`,
                  values: series.map((p) => p.spread),
                  color: meta.color,
                },
              ]}
              height={120}
            />
          </div>
        </>
      )}
    </V17Card>
  );
}

function OrthogonalCard({ report }: { report: OrthogonalReport }) {
  const kept = report.kept ?? [];
  return (
    <V17Card title="因子正交化（贪心残差）">
      <div className="text-xs text-muted-foreground">
        按 |t| 降序贪心保留，后续因子以残差 IC 复检；|残差 t| &lt;{" "}
        {report.params?.min_abs_t ?? 2.0} 剔除（与已保留因子共线或无独立预测力）
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="py-1 pr-3">保留因子</th>
              <th className="py-1 pr-3">IC均值</th>
              <th className="py-1 pr-3">IR</th>
              <th className="py-1 pr-3">IC&gt;0占比</th>
              <th className="py-1">|t|</th>
            </tr>
          </thead>
          <tbody>
            {kept.map((row) => (
              <tr key={row.factor} className="border-b last:border-0">
                <td className="py-1 pr-3 font-mono">{row.factor}</td>
                <td className="py-1 pr-3 font-mono">{fmtNum(row.IC_mean)}</td>
                <td className="py-1 pr-3 font-mono">{fmtNum(row.IR)}</td>
                <td className="py-1 pr-3 font-mono">
                  {row.IC_pos_rate != null
                    ? `${(row.IC_pos_rate * 100).toFixed(1)}%`
                    : "—"}
                </td>
                <td className="py-1 font-mono">
                  {row.t_stat != null ? Math.abs(row.t_stat).toFixed(2) : "—"}
                </td>
              </tr>
            ))}
            {kept.length === 0 && (
              <tr>
                <td colSpan={5} className="py-2 text-xs text-muted-foreground">
                  无因子通过残差 |t| 门槛
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {report.dropped?.length > 0 && (
        <div className="mt-2">
          <div className="text-xs font-medium text-muted-foreground">剔除：</div>
          <ul className="text-xs text-muted-foreground">
            {report.dropped.map((d) => (
              <li key={d.factor} className="font-mono">
                {d.factor} — {d.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </V17Card>
  );
}

export function AshareStylePage() {
  const [report, setReport] = useState<StyleReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await ashareGet<StyleReport>("/api/v1/ashare/style-report");
      setReport(body ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const unwrapped = await ashareGet<{ report: StyleReport | null }>(
        "/api/v1/ashare/style-report/refresh",
        { method: "POST" },
      );
      if (unwrapped?.report) {
        setReport(unwrapped.report);
      } else {
        await load();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto"><div className="space-y-4">
      <h1 className="text-sm font-semibold text-ink">A股风格监测</h1>
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          三风格横截面多空 spread 监测（hs300 全量，正 spread = 因子方向组跑赢）
        </div>
        <button
          className="rounded border px-3 py-1 text-sm hover:bg-muted disabled:opacity-50"
          disabled={refreshing || loading}
          onClick={() => void refresh()}
        >
          {refreshing ? "重算中…（约 1 分钟）" : "重新计算"}
        </button>
      </div>

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          {error}
        </div>
      )}

      {loading && (
        <div className="rounded border px-3 py-6 text-center text-sm text-muted-foreground">
          正在构建因子面板（hs300 × 日线 × 财务，约 1 分钟）…
        </div>
      )}

      {report && !loading && (
        <div className="grid gap-4 md:grid-cols-1">
          <div className="text-xs text-muted-foreground">
            覆盖 {report.n_symbols} 票 × {report.n_days} 日 · 前向列{" "}
            {report.forward_col} · regime 回看 {report.lookback} 日
          </div>
          {Object.entries(report.styles).map(([name, info]) => (
            <StyleCard key={name} name={name} info={info} />
          ))}
          {report.orthogonal && <OrthogonalCard report={report.orthogonal} />}
        </div>
      )}
      </div>
    </div>
  );
}
