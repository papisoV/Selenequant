"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/hooks/use-api";
import { identityTranslate, type Translate } from "@/lib/i18n/types";

/**
 * 策略卡证据聚合 — 列表页一次 GET /api/v1/backtests?limit=1000,
 * 客户端按 strategy_id 归约。禁每卡一请求(120/min 限流)。
 * 只认 status==="completed" 的行(失败/运行中的任务不是证据)。
 */
export interface StrategyEvidence {
  count: number;
  bestSharpe: number | null;
  lastDate: string | null;
  lastEpoch: number | null;
}

interface AggRow {
  status?: unknown;
  strategy_id?: unknown;
  created_at?: unknown;
  sharpe_ratio?: unknown;
}

export function aggregateEvidence(rows: AggRow[] | null): Map<string, StrategyEvidence> {
  const map = new Map<string, StrategyEvidence>();
  if (!rows) return map;
  for (const r of rows) {
    if (r.status !== "completed") continue;
    if (typeof r.strategy_id !== "string") continue;
    const sid = r.strategy_id;
    const created = typeof r.created_at === "number" ? r.created_at : null;
    const sharpe = typeof r.sharpe_ratio === "number" ? r.sharpe_ratio : null;
    const cur = map.get(sid) ?? { count: 0, bestSharpe: null, lastDate: null, lastEpoch: null };
    const isNewer = created !== null && (cur.lastEpoch === null || created > cur.lastEpoch);
    map.set(sid, {
      count: cur.count + 1,
      bestSharpe:
        sharpe !== null && (cur.bestSharpe === null || sharpe > cur.bestSharpe)
          ? sharpe
          : cur.bestSharpe,
      lastDate: isNewer
        ? new Date((created as number) * 1000).toLocaleDateString("zh-CN", {
            month: "2-digit",
            day: "2-digit",
          })
        : cur.lastDate,
      lastEpoch: isNewer ? created : cur.lastEpoch,
    });
  }
  return map;
}

export function useStrategyEvidence() {
  const { data, isLoading } = useSWR<Map<string, StrategyEvidence>>(
    "/api/v1/backtests?limit=1000",
    () =>
      fetcher<Array<Record<string, unknown>>>("/api/v1/backtests?limit=1000").then((rows) =>
        aggregateEvidence(rows),
      ),
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );
  return { evidence: data ?? null, isLoading };
}

export function evidenceLineText(
  e: StrategyEvidence | null | undefined,
  tr: Translate = identityTranslate,
): string {
  if (!e || e.count === 0) return tr("未验证");
  const sharpe = e.bestSharpe !== null ? ` · sharpe ${e.bestSharpe.toFixed(2)}` : "";
  const date = e.lastDate ? ` · ${e.lastDate}` : "";
  return `${tr("{n} 次回测", { n: e.count })}${sharpe}${date}`;
}
