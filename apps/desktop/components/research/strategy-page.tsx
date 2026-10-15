"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { StrategyDetailView } from "./strategy-detail-view";
import { StrategyEntryView } from "./strategy-entry-view";
import { StrategyWorkbench } from "./strategy-workbench";
import { type MissionsListResponse } from "./missions-types";
import { type StrategyStateResponse } from "./strategy-types";

/**
 * STRATEGY 页 — slug "strategy-cockpit",/research/strategy-cockpit。
 * 无 ?id → 入口(从 Mission subject 找可打开的策略);?id=strat-xxx → 驾驶舱。
 * 数据走真实链路:GET /research/strategy/{id}/state + /state/{n}(Episode State(T))。
 */
function StrategyInner() {
  const params = useSearchParams();
  const id = params.get("id");
  const directWorkbench = params.get("view") === "workbench";
  const { useApi } = useApiPage({ distinguish: true });

  // 两个 useApi 无条件顶层调用;SWR 对 null path 不请求
  const missionsApi = useApi<MissionsListResponse>(id ? null : "/api/v1/research/missions");
  const stateApi = useApi<StrategyStateResponse>(id ? `/api/v1/research/strategy/${id}/state` : null);

  // view=workbench 直入工作台,不等 State(state 经 dev 代理偶发 20s+,见 detail 页)
  if (id && directWorkbench) {
    return <StrategyWorkbench id={id} onExit={() => { window.location.search = `?id=${encodeURIComponent(id)}`; }} />;
  }
  if (id) {
    return <StrategyDetailView id={id} api={stateApi} />;
  }
  return <StrategyEntryView missions={missionsApi.data?.missions ?? null} loading={missionsApi.isLoading} errorStatus={missionsApi.error?.status} />;
}

export function StrategyCockpitPage() {
  return (
    <Suspense fallback={<div className="h-full" />}>
      <StrategyInner />
    </Suspense>
  );
}
