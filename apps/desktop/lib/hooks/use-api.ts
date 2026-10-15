"use client";

import useSWR from "swr";
import { ApiError } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";

// 后端挂起时快速失败:超时后 abort 返回 null,组件显示占位而不是无限骨架屏。
// 曾因后端 LiteLLM 启动卡死导致页面永不结束"加载中…"(2026-08-17 事故)。
const FETCH_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_FETCH_TIMEOUT_MS ?? 12_000);

// SWR fetcher: 失败抛 ApiError → SWR 默认 onErrorRetry 指数退避重试
// (曾吞错成 null,SWR 当成功数据永不重试:后端慢/429 时页面永久空态,只能手动刷新)
// 返回 null 仍表示"端点明确无数据"(No X data available),组件据此显示空状态
// 导出供 SWR preload(hover 预载)复用,保证预载数据形态与 useApi 一致
export async function fetcher<T>(path: string): Promise<T | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = getAccessToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const res = await fetch(path, { headers, signal: controller.signal });
    if (!res.ok) {
      throw new ApiError(`HTTP ${res.status} ${res.statusText} for ${path}`, res.status);
    }
    const body = await res.json();
    // BARE 端点(trading/positions 等)直接返回数据
    // APIResponse 包装端点返回 {data, meta, error}
    if (body && typeof body === "object" && "data" in body && "meta" in body) {
      if (body.error) {
        throw new ApiError(body.error.message ?? "API error", 500, {
          code: body.error.code,
          details: body.error.details,
        });
      }
      const data = body.data;
      // 后端 "No X data available" sentinel — 视为空数据
      if (data && typeof data === "object" && typeof data.message === "string" && /No .+ data available/i.test(data.message)) {
        return null;
      }
      return (data ?? null) as T;
    }
    return body as T;
  } catch (e) {
    throw e instanceof ApiError ? e : new ApiError(`fetch failed for ${path}`, 0);
  } finally {
    clearTimeout(timer);
  }
}

// 不带 fallback 的 SWR hook,返回 {data, error, isLoading}
// data === null 表示 fetch 失败/空,组件应显示空状态("—", "暂无数据")
// opts.refreshInterval — 轮询间隔 (ms)，如订单簿 1s
// opts.onErrorRetry — SWR 默认重试，传 false 关闭（1008/429 限流场景）
/**
 * @deprecated 使用 useApiPage 替代。distinguish=true 区分服务挂/真无数据,false 维持现状。
 * 新代码强制用 useApiPage,此 hook 仅向后兼容 43 页存量。
 */
export function useApi<T>(
  path: string | null,
  opts?: { refreshInterval?: number; onErrorRetry?: boolean | ((err: ApiError) => boolean) }
) {
  const { data, error, isLoading, mutate } = useSWR<T | null>(
    path,
    () => fetcher<T>(path as string),
    {
      revalidateOnFocus: false,
      dedupingInterval: 5000,
      refreshInterval: opts?.refreshInterval,
      // 注意:只在显式 false 时传 key——显式传 undefined 也会覆盖 SWR 内建默认重试
      ...(opts?.onErrorRetry === false ? { onErrorRetry: () => false } : {}),
    }
  );
  return { data, error: error as ApiError | undefined, isLoading, mutate };
}
