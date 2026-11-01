"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/hooks/use-api";
import type { ApiError } from "@/lib/api-client";
import { ictStructurePath } from "@/lib/api/ict-structure";
import type { IctStructureResponse, IctStructureQuery } from "@/lib/api/ict-structure";

/**
 * ICT 结构叠加层数据。
 *
 * 单独写而不用 useApi 的原因：该品种/周期没 K 线时后端返 404，这是正常空态。
 * SWR 默认按指数退避重试，会让一个"本来就没数据"的分区反复打请求。
 * 这里 404 直接停（data 保持 null，图表不画），其余错误照常重试。
 */
export function useIctStructure(
  query: IctStructureQuery | null,
  opts?: { refreshInterval?: number },
): {
  data: IctStructureResponse | null;
  error: ApiError | undefined;
  isLoading: boolean;
  mutate: () => Promise<unknown>;
} {
  // 调用方必须给 limit：结构点/时段带是按 bar 覆盖的，覆盖窗口一旦短于
  // 图表窗口，远端就整段没有叠加层（曾硬编码 500，而 kf 拉 1500 根，
  // 时段带只画在右三分之一）。默认值只作兜底，别依赖它。
  const key = query ? ictStructurePath({ limit: 500, ...query }) : null;
  const { data, error, isLoading, mutate } = useSWR<IctStructureResponse | null>(
    key,
    () => fetcher<IctStructureResponse>(key as string),
    {
      revalidateOnFocus: false,
      dedupingInterval: 30_000,
      refreshInterval: opts?.refreshInterval,
      onErrorRetry: (err: ApiError, _key, _config, revalidate, { retryCount }) => {
        if (err?.status === 404) return;
        if (retryCount >= 3) return;
        setTimeout(revalidate, 5_000 * (retryCount + 1));
      },
    },
  );

  return {
    data: (data as IctStructureResponse | null) ?? null,
    error: error as ApiError | undefined,
    isLoading,
    mutate,
  };
}
