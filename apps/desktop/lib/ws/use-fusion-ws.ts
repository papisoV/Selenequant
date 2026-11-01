"use client";

import { resolveBackendHost } from "@/lib/backend-host";
import { useEffect, useRef, useState } from "react";
import type { StreamState } from "./use-markets-ws";

// ws/strategy — fusion 实时监控（多策略融合信号）
// 设计：复用后端既有 fusion 频道，不新建 alpha:monitor（无僵尸频道）。
//   频道   fusion:{user_id}:{symbol}            （services/local-api/app/api/v1/ws.py broadcast_fusion_signal）
//   订阅   action=subscribe_fusion（symbol/min_agreeing）
//   消息   type=fusion_signal，data={symbol, side: buy|sell, strength: 0-1, agreeing_count, total_count, strategy_ids, reason}
// 方向语义：后端 side(buy/sell) → 前端归一化 direction(long/short)；strength clamp 0-1。
// 本模块只提供 hook + 解析纯函数，不接 UI（实时监控面板为后续迭代）。

export type FusionDirection = "long" | "short";

export interface FusionSignal {
  symbol: string;
  direction: FusionDirection;
  strength: number;
  agreeingCount: number;
  totalCount: number;
  strategyIds: string[];
  reason: string;
  timestamp: number;
}

export interface FusionWsState {
  connected: boolean;
  streamState: StreamState;
  latest: FusionSignal | null;
  lastMessageAt: number | null;
}

interface UseFusionWsOptions {
  symbol?: string;
  minAgreeing?: number;
  enabled?: boolean;
  onSignal?: (sig: FusionSignal) => void;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const STALE_AFTER_MS = 30000; // fusion 信号频率低，30s stale 阈值

export function clampStrength(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.min(1, Math.max(0, n));
}

export function parseFusionSignal(raw: unknown): FusionSignal | null {
  if (!raw || typeof raw !== "object") return null;
  const m = raw as Record<string, unknown>;
  if (m.type !== "fusion_signal") return null;
  const d = m.data && typeof m.data === "object" ? (m.data as Record<string, unknown>) : null;
  if (!d) return null;
  const symbol = typeof d.symbol === "string" ? d.symbol : "";
  if (!symbol) return null;
  let direction: FusionDirection;
  if (d.side === "buy") direction = "long";
  else if (d.side === "sell") direction = "short";
  else return null;
  return {
    symbol,
    direction,
    strength: clampStrength(d.strength),
    agreeingCount: typeof d.agreeing_count === "number" ? d.agreeing_count : 0,
    totalCount: typeof d.total_count === "number" ? d.total_count : 0,
    strategyIds: Array.isArray(d.strategy_ids) ? d.strategy_ids.map(String) : [],
    reason: typeof d.reason === "string" ? d.reason : "",
    timestamp: typeof m.timestamp === "number" ? m.timestamp : 0,
  };
}

export function useFusionWs({
  symbol = "BTC/USDT",
  minAgreeing = 3,
  enabled = true,
  onSignal,
}: UseFusionWsOptions = {}): FusionWsState {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const staleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const intentionalCloseRef = useRef(false);
  const attemptRef = useRef(0);
  const onSignalRef = useRef(onSignal);

  useEffect(() => {
    onSignalRef.current = onSignal;
  });

  const [state, setState] = useState<FusionWsState>({
    connected: false,
    streamState: "recovering",
    latest: null,
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

    const apiHost = typeof window !== "undefined" ? window.location.host : resolveBackendHost();
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
      setState((s) => ({ ...s, connected: true, streamState: "recovering" }));
      try {
        ws.send(JSON.stringify({ action: "subscribe_fusion", symbol, min_agreeing: minAgreeing }));
      } catch {
        // ignore send error
      }
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as { type?: string };
        if (msg.type === "ping" || msg.type === "fusion_subscribed") return;
        const sig = parseFusionSignal(msg);
        if (!sig) return;
        setState((s) => ({
          ...s,
          streamState: "live",
          latest: sig,
          lastMessageAt: Date.now(),
        }));
        onSignalRef.current?.(sig);
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

  // staleness 检测定时器：超过 STALE_AFTER_MS 无消息 → degraded
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

  // 连接级 effect：symbol / minAgreeing 变化重建连接（重新 subscribe_fusion）
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
        try {
          wsRef.current.close();
        } catch {
          // ignore
        }
        wsRef.current = null;
      }
      attemptRef.current = 0;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, symbol, minAgreeing]);

  return {
    connected: state.connected,
    streamState: state.streamState,
    latest: state.latest,
    lastMessageAt: state.lastMessageAt,
  };
}
