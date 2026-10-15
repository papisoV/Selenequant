"use client";

import useSWR, { type KeyedMutator } from "swr";
import { ApiError } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";

export interface PageApiConfig {
  /**
   * true: HTTP/网络错误通过 SWR error 通道抛 ApiError,组件可 distinguish
   *      "服务挂"(error != null) vs "真无数据"(data === null)。
   * false(默认): 错误吞成 null,与现有 43 页行为一致。
   */
  distinguish?: boolean;
}

export interface UseApiOptions {
  refreshInterval?: number;
  onErrorRetry?: boolean | ((err: ApiError) => boolean);
}

export interface UseApiResult<T> {
  data: T | null;
  error: ApiError | undefined;
  isLoading: boolean;
  mutate: KeyedMutator<T | null>;
}

// distinguish=false fetcher: 错误吞 null(现状)
async function fetcherSilent<T>(path: string): Promise<T | null> {
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = getAccessToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const res = await fetch(path, { headers });
    if (!res.ok) return null;
    const body = await res.json();
    if (body && typeof body === "object" && "data" in body && "meta" in body) {
      if (body.error) return null;
      const data = body.data;
      if (
        data &&
        typeof data === "object" &&
        typeof data.message === "string" &&
        /No .+ data available/i.test(data.message)
      ) {
        return null;
      }
      return (data ?? null) as T;
    }
    return body as T;
  } catch {
    return null;
  }
}

// distinguish=true fetcher: 错误抛 ApiError 走 SWR error 通道
async function fetcherStrict<T>(path: string): Promise<T | null> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(path, { headers });
  if (!res.ok) {
    // 保留后端判决:Envelope {"error":{message}} 优先,其次 FastAPI {"detail"}。
    // 丢掉 detail 会把业务错误(如 404「策略不存在」)伪装成 "HTTP 404 …",
    // 组件只能瞎猜成"服务没起"。
    let message = `HTTP ${res.status} ${res.statusText} for ${path}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object") {
        const err = (body as Record<string, unknown>).error;
        const detail = (body as Record<string, unknown>).detail;
        if (err && typeof err === "object" && typeof (err as { message?: unknown }).message === "string") {
          message = (err as { message: string }).message;
        } else if (typeof detail === "string") {
          message = detail;
        }
      }
    } catch {
      // 非 JSON 错误体 → 沿用 HTTP 文案
    }
    throw new ApiError(message, res.status);
  }
  const body = await res.json();
  if (body && typeof body === "object" && "data" in body && "meta" in body) {
    if (body.error) {
      const err = body.error;
      throw new ApiError(err.message ?? "API error", 500, {
        code: err.code,
        details: err.details,
      });
    }
    const data = body.data;
    if (
      data &&
      typeof data === "object" &&
      typeof data.message === "string" &&
      /No .+ data available/i.test(data.message)
    ) {
      return null;
    }
    return (data ?? null) as T;
  }
  return body as T;
}

/**
 * 页面级 useApi hook。pageConfig.distinguish 一次声明,返回的 useApi 继承该策略。
 *
 * 用法:
 *   const { useApi } = useApiPage({ distinguish: true });
 *   const { data, error, isLoading } = useApi<T>(path);
 *
 * distinguish=true 时:fetcher 抛 ApiError → SWR error 通道有值,
 *   组件可区分 "服务挂"(error) vs "真无数据"(data===null && !error) vs "正常"(data!=null)。
 * distinguish=false(默认):维持现状,错误吞 null,43 页不破坏。
 *
 * Note: useApiPage 自身是 hook (调用 useSWR 间接通过返回的 useApi)。
 * 返回的 useApi 必须在组件顶层无条件调用,不可包 useCallback(hooks 不能 memoize)。
 * distinguish 在 useApiPage 闭包里捕获,每次渲染产生新 useApi 函数,但 SWR 按 path 缓存,不会重渲染。
 */
export function useApiPage(pageConfig: PageApiConfig = {}): {
  useApi: <T>(path: string | null, opts?: UseApiOptions) => UseApiResult<T>;
} {
  const distinguish = pageConfig.distinguish === true;

  // 不用 useCallback:useApi 内部调用 useSWR 是 hook,hooks 不能 memoize。
  // 闭包捕获 distinguish,每次渲染新函数引用无副作用(SWR 按 key 缓存)。
  const useApi = function useApi<T>(
    path: string | null,
    opts?: UseApiOptions
  ): UseApiResult<T> {
    const fetcher = distinguish ? fetcherStrict<T> : fetcherSilent<T>;
    const swr = useSWR<T | null>(
      path,
      () => (path === null ? null : fetcher(path)),
      {
        revalidateOnFocus: false,
        dedupingInterval: 5000,
        refreshInterval: opts?.refreshInterval,
        // 429 限时重试一次(60s,与 agent-chat-panel 429 恢复路径同款);其余错误不重试
        onErrorRetry: (err, key, config, revalidate, { retryCount }) => {
          if (err.status === 429 && retryCount === 0) {
            setTimeout(revalidate, 60_000);
            return;
          }
        },
      }
    );
    return {
      data: swr.data ?? null,
      error: swr.error as ApiError | undefined,
      isLoading: swr.isLoading,
      mutate: swr.mutate,
    };
  };

  return { useApi };
}
