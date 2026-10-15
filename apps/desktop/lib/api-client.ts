import type { APIResponse } from "@/lib/types";
import { getAccessToken, clearAccessToken, refreshToken } from "@/lib/auth";

const API_BASE = "";

// 直连基座(Next 代理 IPv6 bug 不修):新数据组件唯一入口
// 背景:next.config.ts rewrite localhost:8088 → 浏览器解析 IPv6 ::1,uvicorn 只绑 127.0.0.1
// host 来源统一走 resolveBackendHost(LOCAL_API_BASE 注入 → NEXT_PUBLIC_API_HOST → 127.0.0.1:8088)
import { resolveBackendHost } from "@/lib/backend-host";
const API_DIRECT_BASE = `http://${resolveBackendHost()}`;

// 不包装 APIResponse 的端点（返回 bare array/object）
// 来源：services/local-api/app/api/v1/{trading,portfolio,mcp,skills_api,decision_workflow}.py
const BARE_ENDPOINT_PATTERNS = [
  /^\/api\/v1\/trading\/positions/,
  /^\/api\/v1\/trading\/orders/,
  /^\/api\/v1\/trading\/pnl\//,
  /^\/api\/v1\/portfolio\/recommend/,
  /^\/api\/v1\/experiments/,
  /^\/api\/v1\/paper-plans/,
  /^\/api\/v1\/mcp/,
  /^\/api\/v1\/skills_api/,
  /^\/api\/v1\/analytics\//,
  /^\/api\/v1\/decision-workflow\//,
  // research_missions 全文件裸 dict(批3实机验证发现:apiPost 走 envelope 强解
  // .data → 裸响应解成 null → "开新实验验证"成功分支永不到达)
  /^\/api\/v1\/research\/missions/,
  // chat_sessions 全文件裸响应(list/dict,见 chat_sessions.py)。漏这条的后果:
  // 历史会话列表/消息全部解成 null —— 侧栏恒"暂无历史会话",且 delete/put 的
  // {status:"ok"} 也解成 null。2026-10-11 实机核对 8090 裸数组响应定位。
  /^\/api\/v1\/chat\//,
];

function isBareEndpoint(path: string): boolean {
  return BARE_ENDPOINT_PATTERNS.some((p) => p.test(path));
}

// FastAPI error bodies are {"detail": "..."}; our own envelope uses
// {"error": {code, details}}. Read whichever is present so backend verdicts
// (e.g. T4 deploy-gate 403 reason) reach the UI instead of "HTTP 403".
function parseErrorBody(body: unknown): { message?: string; code?: string; details?: Record<string, unknown> } {
  if (typeof body !== "object" || body === null) return {};
  const b = body as Record<string, unknown>;
  const err = b.error;
  if (typeof err === "object" && err !== null) {
    const e = err as Record<string, unknown>;
    return { code: typeof e.code === "string" ? e.code : undefined, details: (e.details as Record<string, unknown>) ?? undefined };
  }
  if (typeof b.detail === "string") return { message: b.detail };
  return {};
}

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: Record<string, unknown>;

  constructor(message: string, status: number, info?: { code?: string; details?: Record<string, unknown> }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = info?.code;
    this.details = info?.details;
  }
}

async function throwHttpError(res: Response, path: string): Promise<never> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    // ignore parse error
    body = undefined;
  }
  const errorInfo = parseErrorBody(body);
  throw new ApiError(errorInfo.message ?? `HTTP ${res.status} ${res.statusText} for ${path}`, res.status, errorInfo);
}

// ---------------------------------------------------------------------------
// In-flight GET deduplication: 同 URL 进行中时复用 Promise（参考旧代码 api-client.ts:14-26）
// ---------------------------------------------------------------------------
const _inflight = new Map<string, Promise<unknown>>();
const _controllers = new Set<AbortController>();

// GET 超时:后端挂起时快速失败,页面降级显示占位而非无限"加载中…"(2026-08-17 事故,
// 后端 LiteLLM 启动卡死让所有 GET 永不返回)。非 GET(回测/LLM 调参等长任务)不加超时。
const GET_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_FETCH_TIMEOUT_MS ?? 12_000);

export function abortAllPendingGets(): void {
  for (const c of _controllers) {
    try { c.abort(); } catch { /* ignore */ }
  }
  _controllers.clear();
  _inflight.clear();
}

function buildHeaders(init?: RequestInit): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> ?? {}),
  };
  const token = getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

let _refreshPromise: Promise<boolean> | null = null;

async function handle401<T>(path: string, init: RequestInit | undefined, base: string): Promise<T | null> {
  const refreshed = await (_refreshPromise ?? (_refreshPromise = refreshToken().finally(() => { _refreshPromise = null; })));
  if (refreshed) {
    const retryRes = await fetch(`${base}${path}`, {
      ...init,
      headers: buildHeaders(init),
    });
    if (retryRes.ok) return retryRes.json() as Promise<T>;
  }
  clearAccessToken();
  return null;
}

async function rawFetch<T>(path: string, init?: RequestInit, base: string = API_BASE): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const url = `${base}${path}`;

  // GET 去重：同 base+path 进行中则复用
  if (method === "GET") {
    const key = url;
    const existing = _inflight.get(key);
    if (existing) {
      return existing as Promise<T>;
    }
    const controller = new AbortController();
    _controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), GET_TIMEOUT_MS);
    const promise = (async () => {
      try {
        const res = await fetch(url, {
          ...init,
          signal: controller.signal,
          headers: buildHeaders(init),
        });
        if (res.status === 401) {
          const retried = await handle401<T>(path, { ...init, signal: controller.signal }, base);
          if (retried !== null) return retried;
        }
        if (!res.ok) {
          await throwHttpError(res, path);
        }
        return res.json() as Promise<T>;
      } finally {
        clearTimeout(timer);
        _inflight.delete(key);
        _controllers.delete(controller);
      }
    })();
    _inflight.set(key, promise);
    return promise;
  }

  // 非 GET（POST/PUT/DELETE）：取消所有 pending GET 释放连接
  abortAllPendingGets();

  const res = await fetch(url, {
    ...init,
    headers: buildHeaders(init),
  });

  if (res.status === 401) {
    const retried = await handle401<T>(path, init, base);
    if (retried !== null) return retried;
  }

  if (!res.ok) {
    await throwHttpError(res, path);
  }

  return res.json() as Promise<T>;
}

// 包装端点 fetcher：自动取 resp.data
export async function fetchEnvelope<T>(path: string, init?: RequestInit, base: string = API_BASE): Promise<T> {
  const wrapped = await rawFetch<APIResponse<T>>(path, init, base);
  if (wrapped.error) {
    throw new ApiError(wrapped.error.message, 500, {
      code: wrapped.error.code,
      details: wrapped.error.details,
    });
  }
  return (wrapped.data ?? null) as T;
}

// 直连模式 fetcher：http://{NEXT_PUBLIC_API_HOST} + 解 envelope
// 与 fetchEnvelope 同语义(复用 401 refresh / inflight 去重 / ApiError)，仅换 base
export async function apiDirect<T>(path: string, init?: RequestInit): Promise<T> {
  return fetchEnvelope<T>(path, init, API_DIRECT_BASE);
}

// 直连裸 fetcher：不解 envelope（ashare 等 router 成功时返回裸 dict，错误才有 envelope）
export async function apiDirectRaw<T>(path: string, init?: RequestInit): Promise<T> {
  return rawFetch<T>(path, init, API_DIRECT_BASE);
}

// 裸端点 fetcher：直接返回
export async function fetchBare<T>(path: string, init?: RequestInit): Promise<T> {
  return rawFetch<T>(path, init);
}

// 自动选择：根据 path 匹配 BARE_ENDPOINT_PATTERNS 决定是否解包
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  if (isBareEndpoint(path)) {
    return fetchBare<T>(path, init);
  }
  return fetchEnvelope<T>(path, init);
}

// POST / PUT / DELETE 包装
export async function apiPost<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return api<T>(path, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, {
    method: "PUT",
    body: body ? JSON.stringify(body) : undefined,
  });
}

export async function apiDelete<T>(path: string): Promise<T> {
  return api<T>(path, { method: "DELETE" });
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, {
    method: "PATCH",
    body: body ? JSON.stringify(body) : undefined,
  });
}
