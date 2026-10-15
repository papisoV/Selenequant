"use client";

import { resolveBackendHost } from "@/lib/backend-host";
import { useEffect, useRef, useState } from "react";
import { useStrategyStore } from "@/lib/store/strategy";
import type { StrategyWSMessage } from "@/lib/types/strategy";
import type { StreamState } from "./use-markets-ws";

// ws/strategy — 策略信号实时流
// 来源：旧代码 apps/desktop/lib/use-strategy-ws.ts:22-110
// 升级点（参考 use-paper-ws.ts 三态模式）：
//  1. streamState 三态 live/degraded/recovering（旧代码只有 connected boolean）
//  2. 序列号间隙检测（旧代码没有，跳号 >1 触发重连）
//  3. 指数退避重连（旧代码固定 3000ms，改为 1s 起步 30s 封顶）
//  4. 数据写进 useStrategyStore（zustand），不在 hook state 里持有数据
// 激活条件：scene-ws-mount 顶层一直 mount（策略信号一直连，不受 mode 影响）

export interface StrategyWsState {
  connected: boolean;
  streamState: StreamState;
  lastMessageAt: number | null;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const STALE_AFTER_MS = 30000; // 策略信号频率很低，30s stale 阈值

function extractSequence(msg: unknown): number | null {
  if (!msg || typeof msg !== "object") return null;
  const m = msg as Record<string, unknown>;
  const d = m.data && typeof m.data === "object" ? (m.data as Record<string, unknown>) : null;
  const raw = m.seq ?? m.sequence ?? d?.seq ?? d?.sequence;
  const seq = typeof raw === "string" ? Number(raw) : raw;
  return typeof seq === "number" && Number.isFinite(seq) ? seq : null;
}

export function useStrategyWs() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);
  const attemptRef = useRef(0);
  const lastSeqRef = useRef<number | null>(null);
  const staleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const setFromMessage = useStrategyStore((s) => s.setFromMessage);
  const reset = useStrategyStore((s) => s.reset);
  const setStreamState = useStrategyStore((s) => s.setStreamState);
  const setConnected = useStrategyStore((s) => s.setConnected);

  const [state, setState] = useState<StrategyWsState>({
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
    if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    const apiHost =
      typeof window !== "undefined"
        ? window.location.host
        : resolveBackendHost();
    const wsUrl = `ws://${apiHost}/api/v1/ws/strategy`;
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
        ws.send(JSON.stringify({ action: "subscribe", channel: "strategy" }));
      } catch {
        // ignore send error
      }
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as StrategyWSMessage;
        if (msg.type === "ping" || msg.type === "subscribed" || msg.type === "unsubscribed") return;

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
  }, [setStreamState]);

  useEffect(() => {
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
  }, []);

  return { wsRef, state };
}
