"use client";

import { resolveBackendHost } from "@/lib/backend-host";
import { useEffect, useRef, useState } from "react";
import { usePaperStore } from "@/lib/store/paper";
import { getAccessToken } from "@/lib/auth";
import type { PaperWSMessage } from "@/lib/types/paper";
import type { StreamState } from "./use-markets-ws";

// ws/paper — paper 账户实时数据（持仓/订单/成交/净值/信号）
// 来源：旧代码 apps/desktop/lib/use-paper-ws.ts:53-246
// 升级点（参考 use-markets-ws.ts 三态模式）：
//  1. streamState 三态 live/degraded/recovering（旧代码只有 connected boolean）
//  2. 序列号间隙检测（旧代码没有，跳号 >1 触发重连）
//  3. 指数退避重连（旧代码固定 3000ms）
// 数据写进 usePaperStore（zustand），消费组件用 selector 订阅，不在 hook state 里持有数据

export interface PaperWsState {
  connected: boolean;
  streamState: StreamState;
  lastMessageAt: number | null;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const STALE_AFTER_MS = 10000; // paper 消息频率较低，10s stale 阈值

function extractSequence(msg: unknown): number | null {
  if (!msg || typeof msg !== "object") return null;
  const m = msg as Record<string, unknown>;
  const d = m.data && typeof m.data === "object" ? (m.data as Record<string, unknown>) : null;
  const raw = m.seq ?? m.sequence ?? d?.seq ?? d?.sequence ?? d?.update_id ?? d?.u;
  const seq = typeof raw === "string" ? Number(raw) : raw;
  return typeof seq === "number" && Number.isFinite(seq) ? seq : null;
}

export function usePaperWs(accountId: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);
  const attemptRef = useRef(0);
  const lastSeqRef = useRef<number | null>(null);
  const staleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const accountIdRef = useRef(accountId);
  // 把 accountId ref 同步移进 effect,避免 render body 直接写 ref（React 19 并发渲染契约）
  useEffect(() => {
    accountIdRef.current = accountId;
  });

  const setFromMessage = usePaperStore((s) => s.setFromMessage);
  const reset = usePaperStore((s) => s.reset);
  const setStreamState = usePaperStore((s) => s.setStreamState);
  const setConnected = usePaperStore((s) => s.setConnected);

  const [state, setState] = useState<PaperWsState>({
    connected: false,
    streamState: "recovering",
    lastMessageAt: null,
  });

  const scheduleReconnect = () => {
    if (intentionalCloseRef.current) return;
    const attempt = attemptRef.current;
    const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, attempt), RECONNECT_MAX_MS);
    attemptRef.current += 1;
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      connect();
    }, delay);
  };

  const connect = () => {
    if (intentionalCloseRef.current) return;
    if (!accountId) return;
    if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    const apiHost =
      typeof window !== "undefined"
        ? window.location.host
        : resolveBackendHost();
    const token = getAccessToken();
    const wsUrl = token
      ? `ws://${apiHost}/api/v1/ws/paper?token=${encodeURIComponent(token)}`
      : `ws://${apiHost}/api/v1/ws/paper`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch {
      scheduleReconnect();
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      attemptRef.current = 0;
      lastSeqRef.current = null;
      setState((s) => ({ ...s, connected: true, streamState: "recovering" }));
      setConnected(true);
      try {
        ws.send(JSON.stringify({ action: "subscribe", account_id: accountId }));
      } catch {
        // ignore send error
      }
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as PaperWSMessage;
        if (msg.type === "ping" || msg.type === "subscribed" || msg.type === "unsubscribed") return;
        if (msg.account_id && msg.account_id !== accountIdRef.current) return;

        // 序列号间隙检测：跳号 >1 → 触发重连
        const seq = extractSequence(msg);
        if (seq !== null) {
          const prev = lastSeqRef.current;
          if (prev !== null && seq > prev + 1) {
            setState((s) => ({ ...s, streamState: "recovering" }));
            setStreamState("recovering");
            try { ws.close(); } catch { /* ignore */ }
            return;
          }
          lastSeqRef.current = seq;
        }

        setState((s) => ({
          ...s,
          streamState: "live",
          lastMessageAt: Date.now(),
        }));
        setStreamState("live");

        // 写进 zustand store
        setFromMessage(msg);
      } catch {
        // ignore parse error
      }
    };

    ws.onerror = () => {
      setState((s) => ({ ...s, streamState: "recovering" }));
      setStreamState("recovering");
    };

    ws.onclose = () => {
      wsRef.current = null;
      setState((s) => ({ ...s, connected: false, streamState: "recovering" }));
      setConnected(false);
      setStreamState("recovering");
      scheduleReconnect();
    };
  };

  // staleness 检测定时器：超过 STALE_AFTER_MS 无消息 → degraded
  useEffect(() => {
    if (!accountId) return;
    staleTimerRef.current = setInterval(() => {
      setState((s) => {
        if (s.streamState === "recovering" || s.lastMessageAt === null) return s;
        const since = Date.now() - s.lastMessageAt;
        if (since > STALE_AFTER_MS && s.streamState !== "degraded") {
          setStreamState("degraded");
          return { ...s, streamState: "degraded" };
        }
        return s;
      });
    }, 1000);
    return () => {
      if (staleTimerRef.current) clearInterval(staleTimerRef.current);
    };
  }, [accountId, setStreamState]);

  useEffect(() => {
    if (!accountId) return;
    intentionalCloseRef.current = false;
    reset();
    connect();

    return () => {
      // 不设 intentionalCloseRef=true：StrictMode 双调用会导致 mount2 的 connect() 被跳过，WS 永不重连
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
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
      lastSeqRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId]);

  return { wsRef, state };
}
