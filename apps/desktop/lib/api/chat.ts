import { api, apiPost, apiDelete, apiPut } from "@/lib/api-client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ChatSession {
  id: string;
  strategy_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageRecord {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  extra: Record<string, unknown>;
  created_at: string;
}

export interface AgentChatContext {
  symbol?: string;
  timeframe?: string;
  exchange?: string;
  market_type?: string;
  intent?: string;
  mark_price?: number;
  contract_symbol?: string;
  positions?: Array<Record<string, unknown>>;
  backtest_id?: string;
  strategy_id?: string;
  strategy_name?: string;
  metrics?: Record<string, unknown>;
  trades?: Array<Record<string, unknown>>;
}

export interface AgentChatResult {
  sessionId?: string;
}

// ---------------------------------------------------------------------------
// SSE stream reader (with heartbeat + reconnect support)
// ---------------------------------------------------------------------------

const _HEARTBEAT_TIMEOUT_MS = 15_000;
const _MAX_RECONNECT_ATTEMPTS = 3;
const _RECONNECT_BASE_DELAY_MS = 2_000;

async function _readSSEStream(
  res: Response,
  onEvent: (event: { type: string; data: unknown }) => void,
  signal?: AbortSignal,
): Promise<{ sessionId?: string; receivedDone: boolean; disconnectReason?: string }> {
  const headerSessionId = res.headers.get("X-Session-ID") ?? undefined;
  let sseSessionId: string | undefined;
  let receivedDone = false;
  let timedOut = false;

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let heartbeatTimer: ReturnType<typeof setTimeout> | null = null;

  const resetHeartbeat = () => {
    if (heartbeatTimer) clearTimeout(heartbeatTimer);
    heartbeatTimer = setTimeout(() => {
      timedOut = true;
      reader.cancel().catch(() => {});
    }, _HEARTBEAT_TIMEOUT_MS);
  };

  resetHeartbeat();

  try {
    while (true) {
      if (signal?.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;

      resetHeartbeat();
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6);
          if (data === "[DONE]") {
            receivedDone = true;
            return { sessionId: headerSessionId ?? sseSessionId, receivedDone };
          }
          try {
            const parsed = JSON.parse(data);
            if (parsed.type === "done") {
              receivedDone = true;
              if (parsed.data?.session_id) {
                sseSessionId = parsed.data.session_id as string;
              }
            }
            onEvent(parsed);
          } catch {
            onEvent({ type: "text", data });
          }
        }
      }
    }
  } finally {
    if (heartbeatTimer) clearTimeout(heartbeatTimer);
  }

  const disconnectReason = timedOut
    ? `${_HEARTBEAT_TIMEOUT_MS / 1000}s 内未收到服务器响应（心跳超时）`
    : signal?.aborted
      ? "用户取消"
      : "服务器关闭了连接";

  return { sessionId: headerSessionId ?? sseSessionId, receivedDone, disconnectReason };
}

// ---------------------------------------------------------------------------
// Agent chat (SSE streaming)
// ---------------------------------------------------------------------------

export async function sendAgentChat(
  message: string,
  onEvent: (event: { type: string; data: unknown }) => void,
  context?: AgentChatContext,
  sessionId?: string,
  signal?: AbortSignal,
  options?: { resume?: boolean; onSessionId?: (sessionId: string) => void },
): Promise<AgentChatResult> {
  let attempt = 0;
  let resolvedSessionId: string | undefined = sessionId;
  let isResume = options?.resume ?? false;

  while (attempt <= _MAX_RECONNECT_ATTEMPTS) {
    try {
      const body: Record<string, unknown> = { message };
      if (context) body.context = context;
      if (resolvedSessionId) body.session_id = resolvedSessionId;
      if (isResume) body.resume = true;

      const headers: Record<string, string> = { "Content-Type": "application/json" };
      const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }
      const res = await fetch(`/api/v1/agent/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal,
      });

      if (!res.ok || !res.body) {
        let detail = `HTTP ${res.status}`;
        try {
          const errBody = await res.text();
          if (errBody) {
            const parsed = JSON.parse(errBody);
            const msg = parsed?.error?.message || parsed?.detail || parsed?.message || errBody.slice(0, 200);
            detail = `Server error (${res.status}): ${msg}`;
          }
        } catch {
          // ignore parse errors
        }
        onEvent({ type: "error", data: detail });
        return { sessionId: resolvedSessionId };
      }

      // X-Session-ID 随响应头先到,body 还在流中。立刻回报,让调用方在
      // 流结束前就拿到 session id —— 审批卡在此刻已可点(后端 producer 正
      // await 那个 Future),若等到流收尾才落 id,首轮「批准」会静默 no-op。
      const sid = res.headers.get("X-Session-ID") ?? undefined;
      if (sid) {
        resolvedSessionId = sid;
        if (options?.onSessionId) options.onSessionId(sid);
      }

      const { sessionId: streamSid, receivedDone, disconnectReason } = await _readSSEStream(res, onEvent, signal);
      if (streamSid && streamSid !== resolvedSessionId) {
        resolvedSessionId = streamSid;
        if (options?.onSessionId) options.onSessionId(streamSid);
      }

      if (receivedDone) {
        return { sessionId: resolvedSessionId };
      }

      if (signal?.aborted) {
        return { sessionId: resolvedSessionId };
      }

      // Stream ended without done — treat as disconnect
      attempt++;
      if (attempt > _MAX_RECONNECT_ATTEMPTS) {
        const reason = disconnectReason || "未知原因";
        onEvent({ type: "error", data: `连接多次中断（${_MAX_RECONNECT_ATTEMPTS} 次重连均失败）\n原因：${reason}\n请检查 LLM 配置和网络后重试。` });
        return { sessionId: resolvedSessionId };
      }

      isResume = true;
      const delay = _RECONNECT_BASE_DELAY_MS * Math.pow(2, attempt - 1);
      onEvent({ type: "reconnecting", data: { attempt, maxAttempts: _MAX_RECONNECT_ATTEMPTS, delayMs: delay } });
      await new Promise((r) => setTimeout(r, delay));
      onEvent({ type: "reconnected", data: { attempt } });

    } catch (err: unknown) {
      if (signal?.aborted || (err instanceof DOMException && err.name === "AbortError")) {
        onEvent({ type: "error", data: "请求已取消" });
        return { sessionId: resolvedSessionId };
      }

      const errMsg = err instanceof Error ? err.message : String(err);
      attempt++;
      if (attempt > _MAX_RECONNECT_ATTEMPTS) {
        onEvent({ type: "error", data: `连接失败（${errMsg}）。请检查后端服务是否运行、网络是否正常后重试。` });
        return { sessionId: resolvedSessionId };
      }

      isResume = true;
      const delay = _RECONNECT_BASE_DELAY_MS * Math.pow(2, attempt - 1);
      onEvent({ type: "reconnecting", data: { attempt, maxAttempts: _MAX_RECONNECT_ATTEMPTS, delayMs: delay } });
      await new Promise((r) => setTimeout(r, delay));
      onEvent({ type: "reconnected", data: { attempt } });
    }
  }

  return { sessionId: resolvedSessionId };
}

// ---------------------------------------------------------------------------
// Tool approval (B0 confirmation gate)
// ---------------------------------------------------------------------------

export async function approveToolCall(
  sessionId: string,
  toolCallId: string,
  approve: boolean,
): Promise<{ ok: boolean; errorCode?: string }> {
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const res = await fetch(`/api/v1/agent/approve`, {
      method: "POST",
      headers,
      body: JSON.stringify({ session_id: sessionId, tool_call_id: toolCallId, approve }),
    });
    const body = await res.json().catch(() => null);
    // Success requires a parsed envelope carrying an explicit null error. A
    // null body (non-JSON reply) or a non-2xx status is a failure, never an
    // implicit success — this resolves a human consent prompt, so a silent
    // "ok" on a dropped request would misreport the gate as satisfied.
    if (res.ok && body !== null && body.error == null) {
      return { ok: true };
    }
    return { ok: false, errorCode: body?.error?.code ?? `HTTP ${res.status}` };
  } catch (err: unknown) {
    return { ok: false, errorCode: err instanceof Error ? err.message : String(err) };
  }
}

// ---------------------------------------------------------------------------
// Chat session CRUD
// ---------------------------------------------------------------------------

export async function listChatSessions(
  strategyId?: string,
  limit = 50,
): Promise<ChatSession[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (strategyId) qs.set("strategy_id", strategyId);
  return api<ChatSession[]>(`/api/v1/chat/sessions?${qs}`);
}

export async function getChatSessionMessages(
  sessionId: string,
  limit = 200,
): Promise<ChatMessageRecord[]> {
  return api<ChatMessageRecord[]>(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}`);
}

export async function getChatSession(sessionId: string): Promise<ChatSession> {
  return api<ChatSession>(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`);
}

export async function saveChatSessionMessages(
  sessionId: string,
  messages: Array<{ id: string; role: string; content: string; extra?: unknown }>,
): Promise<{ status: string }> {
  return apiPut<{ status: string }>(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`, { messages });
}

export async function deleteChatSession(
  sessionId: string,
): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`);
}
