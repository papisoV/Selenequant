"use client";

import { resolveBackendHost } from "@/lib/backend-host";
import { useEffect, useRef, useState, useCallback } from "react";
import { api } from "@/lib/api-client";

// 调参进度 WS 实时推送 hook
// 来源：旧代码 lib/use-tuning-ws.ts（132 行）
// 升级点 vs 旧代码：
//  1. WS 断时自动 fallback 回 3s 轮询（旧代码只重连 WS，无 fallback）
//  2. 复用新前端 streamState 三态（live/degraded/recovering）
//  3. 指数退避重连（旧代码固定 3000ms）
// 数据流：WS 实时推 tuning_progress/completed/failed/cancelled 4 种消息
// WS 断时：3s 轮询 GET /research/tuning/tasks/{id} 拿同一份 taskState

export type TuningStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export interface CompletedStrategy {
  strategy: string;
  exchange: string;
  sharpe: number;
  skipped?: string;
  interval?: string;
  total_return?: number;
  score?: number;
}

export interface TuningProgressData {
  current: number;
  total: number;
  current_strategy?: string;
  current_interval?: string;
  current_exchange?: string;
  current_market_type?: string;
  best_sharpe_so_far: number;
  elapsed_seconds: number;
  eta_seconds: number | null;
  completed_strategies: CompletedStrategy[];
}

export interface TuningTaskState {
  task_id: string;
  status: TuningStatus;
  config: Record<string, unknown>;
  progress: TuningProgressData;
  result_file: string | null;
  error: string | null;
  created_at: number;
}

type StreamState = "live" | "degraded" | "recovering";

interface UseTuningWsOptions {
  taskId: string | null;
  onCompleted?: (s: TuningTaskState) => void;
  onFailed?: (s: TuningTaskState) => void;
}

interface UseTuningWsResult {
  connected: boolean;
  streamState: StreamState;
  taskState: TuningTaskState | null;
  progress: TuningProgressData | null;
  status: TuningStatus;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const POLL_FALLBACK_MS = 3000;
const STALE_AFTER_MS = 10000;

export function useTuningWs(options: UseTuningWsOptions): UseTuningWsResult {
  const { taskId, onCompleted, onFailed } = options;
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const staleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const intentionalCloseRef = useRef(false);
  const attemptRef = useRef(0);
  const taskIdRef = useRef(taskId);
  const onCompletedRef = useRef(onCompleted);
  const onFailedRef = useRef(onFailed);
  // 把回调 ref 同步移进 effect,避免 render body 直接写 ref（React 19 并发渲染契约）
  useEffect(() => {
    taskIdRef.current = taskId;
    onCompletedRef.current = onCompleted;
    onFailedRef.current = onFailed;
  });

  const [connected, setConnected] = useState(false);
  const [streamState, setStreamState] = useState<StreamState>("recovering");
  const [taskState, setTaskState] = useState<TuningTaskState | null>(null);

  const applyTaskState = useCallback((state: TuningTaskState) => {
    setTaskState(state);
    if (state.status === "completed") onCompletedRef.current?.(state);
    if (state.status === "failed" || state.status === "cancelled") onFailedRef.current?.(state);
  }, []);

  // 轮询 fallback：WS 断时 3s 轮一次
  const startPolling = useCallback(() => {
    if (pollTimerRef.current) return;
    const poll = async () => {
      const id = taskIdRef.current;
      if (!id) return;
      try {
        const data = await api<TuningTaskState>(`/api/v1/research/tuning/tasks/${id}`);
        if (data) applyTaskState(data);
      } catch {
        // ignore poll errors
      }
    };
    poll();
    pollTimerRef.current = setInterval(poll, POLL_FALLBACK_MS);
  }, [applyTaskState]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (intentionalCloseRef.current) return;
    const attempt = attemptRef.current;
    const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, attempt), RECONNECT_MAX_MS);
    attemptRef.current += 1;
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      connect();
    }, delay);
  }, []);

  function connect() {
    if (intentionalCloseRef.current) return;
    const id = taskIdRef.current;
    if (!id) return;
    if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    const apiHost =
      typeof window !== "undefined"
        ? window.location.host
        : resolveBackendHost();
    const wsUrl = `ws://${apiHost}/api/v1/ws/research`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch {
      setStreamState("recovering");
      startPolling();
      scheduleReconnect();
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      attemptRef.current = 0;
      setConnected(true);
      setStreamState("live");
      stopPolling();
      try {
        ws.send(JSON.stringify({ action: "subscribe", task_id: id }));
      } catch {
        // ignore send error
      }
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "ping" || msg.type === "subscribed" || msg.type === "unsubscribed") return;
        if (!msg.data) return;
        const state = msg.data as TuningTaskState;
        setStreamState("live");
        applyTaskState(state);
      } catch {
        // ignore parse error
      }
    };

    ws.onerror = () => {
      setStreamState("recovering");
    };

    ws.onclose = () => {
      wsRef.current = null;
      setConnected(false);
      setStreamState("recovering");
      startPolling();
      scheduleReconnect();
    };
  }

  // stale 检测：WS 连着但 10s 无消息 → degraded
  useEffect(() => {
    if (!taskId) return;
    let lastMsg = Date.now();
    staleTimerRef.current = setInterval(() => {
      const cur = taskState;
      if (cur && (cur.status === "completed" || cur.status === "failed" || cur.status === "cancelled")) {
        return; // terminal state, no stale check
      }
      if (streamState === "live") {
        const since = Date.now() - lastMsg;
        if (since > STALE_AFTER_MS) setStreamState("degraded");
        lastMsg = Date.now(); // reset baseline each tick; if no new msg, since grows next tick
      }
    }, 1000);
    return () => {
      if (staleTimerRef.current) clearInterval(staleTimerRef.current);
    };
  }, [taskId, streamState, taskState]);

  useEffect(() => {
    if (!taskId) return;
    intentionalCloseRef.current = false;
    setTaskState(null);
    setStreamState("recovering");
    connect();
    return () => {
      // 不设 intentionalCloseRef=true：StrictMode 双调用会导致 mount2 的 connect() 被跳过，WS 永不重连
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      if (staleTimerRef.current) {
        clearInterval(staleTimerRef.current);
        staleTimerRef.current = null;
      }
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* ignore */ }
        wsRef.current = null;
      }
      attemptRef.current = 0;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  return {
    connected,
    streamState,
    taskState,
    progress: taskState?.progress ?? null,
    status: taskState?.status ?? "pending",
  };
}
