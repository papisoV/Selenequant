"use client";

import { useApiPage } from "@/lib/hooks/use-api-page";
import { RESEARCH_POLL_LIVE_MS, RESEARCH_POLL_SLOW_MS } from "@/lib/hooks/research-poll";
import { TodayView } from "./today-view";
import type { AgentActivityResponse, TodayResponse } from "./today-types";
import type { MemoryResponse } from "./memory-surface-page";
import type { SensorSnapshot } from "@/lib/api/research-graph";

/** TODAY 页容器 — B4 聚合端点,distinguish=true 分"服务挂"vs"真安静" */
export function TodayPage() {
  const { useApi } = useApiPage({ distinguish: true });
  const api = useApi<TodayResponse>("/api/v1/research/today", {
    refreshInterval: RESEARCH_POLL_SLOW_MS,
  });
  // 实时活动:调查会在几分钟内推进,5s 轮询才看得见视角逐个亮起
  const activity = useApi<AgentActivityResponse>("/api/v1/research/agent-activity", {
    refreshInterval: RESEARCH_POLL_LIVE_MS,
  });
  // 记忆环真值(七环最后一环):knowledge+clues 计数;失败静默显 "—"
  const mem = useApi<MemoryResponse>("/api/v1/research/graph/memory", {
    refreshInterval: RESEARCH_POLL_SLOW_MS,
  });
  const memCount = mem.data
    ? (mem.data.counts?.knowledge ?? 0) + (mem.data.counts?.clues ?? 0)
    : null;
  // ① 传感器快照:开着没发现 vs 没开必须分得清;接口取不到时 strip 不渲染
  const sensor = useApi<SensorSnapshot>("/api/v1/research/graph/sensor", {
    refreshInterval: RESEARCH_POLL_SLOW_MS,
  });

  return (
    <TodayView
      data={api.data}
      loading={api.isLoading}
      errorStatus={api.error?.status}
      memCount={memCount}
      activity={activity.data}
      sensor={sensor.data ?? null}
    />
  );
}
