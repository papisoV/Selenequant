"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { MissionsListView } from "./missions-list-view";
import { MissionDetailView } from "./missions-detail-view";
import { toThesisScorecard, type ThesisScorecardRaw } from "./missions-thesis";
import { errorHintText } from "./strategy-parts";
import { type MissionDetailResponse, type MissionsListResponse } from "./missions-types";

/**
 * Mission 页 — slug "missions",/research/missions。
 * 无 ?id → 列表(Daily Brief 雏形);?id=msn-xxx → 详情。
 * 数据走真实链路:GET /research/missions + GET /research/missions/{id};
 * 详情页决策(POST decision)成功后 mutate 刷新列表+详情(环转起来)。
 */
function MissionsInner() {
  const params = useSearchParams();
  const id = params.get("id");
  const { useApi } = useApiPage({ distinguish: true });

  // 两个 useApi 必须无条件在顶层调用;按 id 决定走哪条 path(SWR 对 null path 不请求)
  const listApi = useApi<MissionsListResponse>(id ? null : "/api/v1/research/missions");
  const detailApi = useApi<MissionDetailResponse>(id ? `/api/v1/research/missions/${id}` : null);
  const thesisApi = useApi<ThesisScorecardRaw>(id ? `/api/v1/research/missions/${id}/thesis` : null);

  if (id) {
    if (detailApi.isLoading && detailApi.data === null) {
      return <div className="p-4 text-[11px] text-ink-3">加载中…</div>;
    }
    if (detailApi.error) {
      const is404 = detailApi.error.status === 404;
      return (
        <div className="flex h-full items-center justify-center gap-2 text-xs text-ink-3">
          <span>
            {is404
              ? `未找到 Mission:${id}`
              : errorHintText(detailApi.error.status)}
          </span>
          <a className="text-accent hover:underline" href="/research/missions">
            返回列表
          </a>
        </div>
      );
    }
    const d = detailApi.data;
    if (!d?.mission) {
      return (
        <div className="flex h-full items-center justify-center gap-2 text-xs text-ink-3">
          <span>未找到 Mission:{id}</span>
          <a className="text-accent hover:underline" href="/research/missions">
            返回列表
          </a>
        </div>
      );
    }
    return (
      <MissionDetailView
        mission={d.mission}
        activity={d.activity ?? []}
        scorecard={toThesisScorecard(thesisApi.data)}
        onChanged={() => {
          detailApi.mutate();
          listApi.mutate();
        }}
      />
    );
  }

  return <MissionsListView missions={listApi.data?.missions ?? null} loading={listApi.isLoading} errorStatus={listApi.error?.status} />;
}

export function MissionsPage() {
  return (
    <Suspense fallback={<div className="h-full" />}>
      <MissionsInner />
    </Suspense>
  );
}
