"use client"

import { api, apiPost, apiPut, apiDelete, fetchBare } from "@/lib/api-client"
import type {
  StrategyData,
  BacktestData,
  BacktestTradeData,
  KlineData,
  ChatSession,
  AgentChatContext,
  AgentChatResult,
} from "./types"

export type {
  StrategyData,
  BacktestData,
  BacktestTradeData,
  KlineData,
  ChatSession,
  AgentChatContext,
  AgentChatResult,
}

// API bridge: wraps new frontend's api() calls with the shapes
// the old strategy components expect (returning {data, error} style)

interface ApiResult<T> {
  data: T | null
  error?: string
}

function wrap<T>(promise: Promise<T>): Promise<ApiResult<T>> {
  return promise
    .then((data) => ({ data }))
    .catch((e) => ({ data: null, error: String(e?.message ?? e) }))
}

// --- Klines ---
export function getKlines(
  symbol: string,
  interval: string,
  limit?: number,
  _exchange?: string,
  beforeTs?: number
): Promise<ApiResult<KlineData[]>> {
  const params = new URLSearchParams()
  params.set("symbol", symbol)
  params.set("interval", interval)
  if (limit) params.set("limit", String(limit))
  if (beforeTs) params.set("before", String(beforeTs))
  return wrap(api<KlineData[]>(`/api/v1/klines?${params.toString()}`))
}

// --- Backtest ---
export function getBacktest(id: string): Promise<ApiResult<BacktestData>> {
  return wrap(api<BacktestData>(`/api/v1/backtests/${id}`))
}

export function runBacktest(body: unknown): Promise<ApiResult<BacktestData>> {
  return wrap(apiPost<BacktestData>("/api/v1/backtests", body))
}

// --- Strategy ---
export function getStrategy(id: string): Promise<ApiResult<StrategyData>> {
  return wrap(api<StrategyData>(`/api/v1/strategies/${id}`))
}

export function listStrategies(): Promise<ApiResult<StrategyData[]>> {
  return wrap(api<StrategyData[]>("/api/v1/strategies"))
}

export function createStrategy(body: unknown): Promise<ApiResult<StrategyData>> {
  return wrap(apiPost<StrategyData>("/api/v1/strategies", body))
}

export function updateStrategy(id: string, body: unknown): Promise<ApiResult<StrategyData>> {
  return wrap(apiPut<StrategyData>(`/api/v1/strategies/${id}`, body))
}

export function deleteStrategy(id: string): Promise<ApiResult<void>> {
  return wrap(apiDelete<void>(`/api/v1/strategies/${id}`))
}

export function getStrategyVersions(
  strategyId: string
): Promise<ApiResult<unknown[]>> {
  return wrap(api<unknown[]>(`/api/v1/strategies/${strategyId}/versions`))
}

// --- Strategy deployment ---
// 真端点 = deploy-paper / deploy-live / stop-deployment(strategies.py:853/1133/999)
// main 侧曾删除旧假端点版(/deploy /stop);此处为 qd_celue 重写的真端点版,合并时保留。
export function deployStrategy(
  strategyId: string,
  body?: unknown
): Promise<ApiResult<unknown>> {
  return wrap(apiPost<unknown>(`/api/v1/strategies/${strategyId}/deploy-paper`, body))
}

export function stopStrategy(
  strategyId: string
): Promise<ApiResult<unknown>> {
  return wrap(apiPost<unknown>(`/api/v1/strategies/${strategyId}/stop-deployment`))
}

// --- Chat sessions ---
// 真端点族 = /chat/sessions(chat_sessions.py),裸 JSON 无信封;title 更新为 PATCH
export function listChatSessions(
  strategyId: string
): Promise<ApiResult<ChatSession[]>> {
  return wrap(
    fetchBare<ChatSession[]>(`/api/v1/chat/sessions?strategy_id=${encodeURIComponent(strategyId)}&limit=50`)
  )
}

export function createChatSession(
  strategyId: string,
  title: string
): Promise<ApiResult<ChatSession>> {
  return wrap(
    fetchBare<ChatSession>(`/api/v1/chat/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strategy_id: strategyId, title }),
    })
  )
}

export function getChatSessionMessages(
  sessionId: string
): Promise<ApiResult<unknown[]>> {
  return wrap(fetchBare<unknown[]>(`/api/v1/chat/sessions/${sessionId}/messages`))
}

export function saveChatSessionMessages(
  sessionId: string,
  messages: unknown[]
): Promise<ApiResult<void>> {
  return wrap(
    fetchBare<void>(`/api/v1/chat/sessions/${sessionId}/messages`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    })
  )
}

export function deleteChatSession(
  sessionId: string
): Promise<ApiResult<void>> {
  return wrap(fetchBare<void>(`/api/v1/chat/sessions/${sessionId}`, { method: "DELETE" }))
}

export function updateChatSessionTitle(
  sessionId: string,
  title: string
): Promise<ApiResult<void>> {
  return wrap(
    fetchBare<void>(`/api/v1/chat/sessions/${sessionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    })
  )
}

// --- Agent chat (SSE streaming) ---
// B0/B2 教训:同一份 SSE 归约实现维护两份必然分叉。本面曾自抄一份,有三处失真:
//   1) 不认 `data: [DONE]` 哨兵 → 落进 catch 当 text_delta,把字面 "[DONE]" 渲染进气泡;
//   2) 不跟踪流内真正的 done 事件;
//   3) 流一结束就**无条件伪造** `done` —— 而 B0 门下后端 producer 会阻塞在审批
//      Future 上、流保持开启直到人类裁决(agent.py:962 await asyncio.wait_for(fut, 300)),
//      一旦连接先行结束(断线,或 e2e 桩),这个假 done 会立刻把待审批卡清掉。
// 统一委托给 lib/api/chat.ts 的唯一实现(其余 agent 面同源),不再本地重抄。
export { sendAgentChat } from "@/lib/api/chat"
