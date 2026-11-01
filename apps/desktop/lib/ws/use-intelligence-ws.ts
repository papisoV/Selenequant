"use client";

import { resolveBackendHost } from "@/lib/backend-host";
import { useEffect, useRef, useState } from "react";

// ws/intelligence — 4 named channel: news / events / pulse / alerts
// 注意：发裸名（events），后端 ws.py:1392 用 _INTELLIGENCE_CHANNELS 校验、内部加 intel: 前缀，带前缀会订阅失败
// message: { type: "news_update"|"event_signal"|"pulse_update"|"alert"|"tg_signal", data, timestamp, seq? }
// 来源：services/local-api/app/api/v1/ws.py 的 ws_intelligence handler

export type IntelligenceMessageType =
  | "news_update"
  | "event_signal"
  | "pulse_update"
  | "alert"
  | "tg_signal";

export interface IntelligenceMessage {
  type: IntelligenceMessageType;
  data: Record<string, unknown>;
  timestamp?: string;
  seq?: number;
}

// 复用 use-markets-ws.ts 的 StreamState 类型，避免 index.ts re-export 冲突
import type { StreamState } from "./use-markets-ws";

export interface IntelligenceWsState {
  connected: boolean;
  streamState: StreamState;
  lastMessageAt: number | null;
}

interface UseIntelligenceWsOptions {
  channels?: Array<"news" | "events" | "pulse" | "alerts">;
  onMessage?: (msg: IntelligenceMessage) => void;
  enabled?: boolean;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const STALE_AFTER_MS = 10000; // intelligence 消息频率低，10s stale 阈值

function extractSequence(msg: unknown): number | null {
  if (!msg || typeof msg !== "object") return null;
  const m = msg as Record<string, unknown>;
  const d = m.data && typeof m.data === "object" ? (m.data as Record<string, unknown>) : null;
  const raw = m.seq ?? m.sequence ?? d?.seq ?? d?.sequence ?? d?.update_id ?? d?.u;
  const seq = typeof raw === "string" ? Number(raw) : raw;
  return typeof seq === "number" && Number.isFinite(seq) ? seq : null;
}

export function useIntelligenceWs({
  channels = ["news", "events", "pulse", "alerts"],
  onMessage,
  enabled = true,
}: UseIntelligenceWsOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onMessage);
  // 把回调 ref 同步移进 effect,避免 render body 直接写 ref（React 19 并发渲染契约）
  useEffect(() => {
    handlerRef.current = onMessage;
  });

  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const lastSeqRef = useRef<number | null>(null);
  const staleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [state, setState] = useState<IntelligenceWsState>({
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
    const wsUrl = `ws://${apiHost}/api/v1/ws/intelligence`;
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
      try {
        ws.send(JSON.stringify({ action: "subscribe", channels }));
      } catch {
        // ignore
      }
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as IntelligenceMessage;
        if ((msg as { type?: string }).type === "ping") return;

        const seq = extractSequence(msg);
        if (seq !== null) {
          const prev = lastSeqRef.current;
          if (prev !== null && seq > prev + 1) {
            setState((s) => ({ ...s, streamState: "recovering" }));
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
        handlerRef.current?.(msg);
      } catch {
        // ignore parse error
      }
    };

    ws.onerror = () => {
      setState((s) => ({ ...s, streamState: "recovering" }));
    };

    ws.onclose = () => {
      wsRef.current = null;
      setState((s) => ({ ...s, connected: false, streamState: "recovering" }));
      scheduleReconnect();
    };
  };

  useEffect(() => {
    if (!enabled) return;
    staleTimerRef.current = setInterval(() => {
      setState((s) => {
        if (s.streamState === "recovering" || s.lastMessageAt === null) return s;
        const since = Date.now() - s.lastMessageAt;
        if (since > STALE_AFTER_MS && s.streamState !== "degraded") {
          return { ...s, streamState: "degraded" };
        }
        return s;
      });
    }, 1000);
    return () => {
      if (staleTimerRef.current) clearInterval(staleTimerRef.current);
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    intentionalCloseRef.current = false;
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
  }, [channels.join(","), enabled]);

  return { wsRef, state };
}
