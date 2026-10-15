"use client";

import { useApiPage } from "@/lib/hooks/use-api-page";
import { TodayView } from "./today-view";
import type { TodayResponse } from "./today-types";
import type { MemoryResponse } from "./memory-surface-page";

/** TODAY 页容器 — B4 聚合端点,distinguish=true 分"服务挂"vs"真安静" */
export function TodayPage() {
  const { useApi } = useApiPage({ distinguish: true });
  const api = useApi<TodayResponse>("/api/v1/research/today", { refreshInterval: 60000 });
  // 记忆环真值(七环最后一环):knowledge+clues 计数;失败静默显 "—"
  const mem = useApi<MemoryResponse>("/api/v1/research/graph/memory", {
    refreshInterval: 60000,
  });
  const memCount = mem.data
    ? (mem.data.counts?.knowledge ?? 0) + (mem.data.counts?.clues ?? 0)
    : null;

  return <TodayView data={api.data} loading={api.isLoading} errorStatus={api.error?.status} memCount={memCount} />;
}
