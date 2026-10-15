// lib/hooks/use-anomaly-proposals.ts — A股异动 suggest 档待批提案:轮询 + 裁定
"use client";

import { useCallback, useState } from "react";
import { apiPost } from "@/lib/api-client";
import { useApiPage } from "@/lib/hooks/use-api-page";

/** One pending anomaly proposal from GET /anomaly/proposals. */
export interface AnomalyProposal {
  dedup_id: string;
  symbol: string;
  kind: string;
  session_date: string;
  price: number;
  side: string;
  quantity: number;
  created_at_ms: number;
}

interface ProposalsResponse {
  proposals: AnomalyProposal[];
  mode: string | null;
  wired: boolean;
}

export interface AnomalyProposals {
  proposals: AnomalyProposal[];
  /** Resolved SELENE_ANOMALY_MODE, or null when no consumer is wired. */
  mode: string | null;
  /** False on a crypto-only deployment — distinguishes "none" from "n/a". */
  wired: boolean;
  loading: boolean;
  /** 读接口服务挂(与「无待批」区分开;否则后端一挂面板会假装"暂无提案")。 */
  error: string | null;
  /** dedup_ids with an in-flight verdict, so buttons can disable. */
  deciding: Set<string>;
  /** 裁定请求本身失败(与 error 分开:一个是读挂,一个是写挂)。 */
  decideError: string | null;
  decide: (dedupId: string, approve: boolean) => Promise<void>;
}

const POLL_MS = 10_000;

/**
 * Pending A-share anomaly proposals + the verdict round-trip.
 *
 * Approval records a human confirmation; it does **not** place an order. The
 * backend echoes ``executes_order: false`` and this hook never calls any
 * execution path — the UI states the same thing in words so the operator is
 * never misled into thinking a click trades.
 */
export function useAnomalyProposals(): AnomalyProposals {
  // 待批提案是交易动作的前置面:读接口挂掉必须显示"服务不可达",
  // 不能像旧 useApi 那样把错误吞成 null 再渲染成"暂无提案"。
  const { useApi } = useApiPage({ distinguish: true });
  const { data, error, isLoading, mutate } = useApi<ProposalsResponse>(
    "/api/v1/anomaly/proposals",
    { refreshInterval: POLL_MS }
  );
  const [deciding, setDeciding] = useState<Set<string>>(new Set());
  const [decideError, setDecideError] = useState<string | null>(null);

  const decide = useCallback(
    async (dedupId: string, approve: boolean) => {
      setDecideError(null);
      setDeciding((prev) => new Set(prev).add(dedupId));
      try {
        await apiPost("/api/v1/anomaly/approve", {
          dedup_id: dedupId,
          approve,
        });
        // Refetch so the decided item leaves the list immediately rather than
        // waiting up to POLL_MS for the next tick.
        await mutate();
      } catch (e) {
        setDecideError(e instanceof Error ? e.message : "裁定失败");
      } finally {
        setDeciding((prev) => {
          const next = new Set(prev);
          next.delete(dedupId);
          return next;
        });
      }
    },
    [mutate]
  );

  return {
    proposals: data?.proposals ?? [],
    mode: data?.mode ?? null,
    wired: data?.wired ?? false,
    loading: isLoading,
    error: error ? error.message : null,
    deciding,
    decideError,
    decide,
  };
}
