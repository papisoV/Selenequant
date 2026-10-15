"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// 单个 WS 连接的订阅式流: 连接/重连/收消息, 把消息交给 onMessage。
// 从 strategy-status.tsx 的内联 effect 抽出(行为不变: 固定 3s 重连)。

const RECONNECT_DELAY_MS = 3000;

export interface ReconnectingWs {
  /** 是否已连接。 */
  connected: boolean;
  /** 连接建立后发送一帧(未连接时静默丢弃)。 */
  send: (frame: unknown) => void;
}

/**
 * 连到 `url`, 断线后固定 3s 重连; 每条消息原样交给 onMessage。
 * `onMessage` 需自行 try/catch 解析错误。`onOpen`(可选)在连接建立时被调用。
 */
export function useReconnectingWs(
  url: string,
  onMessage: (event: MessageEvent) => void,
  onOpen?: () => void,
): ReconnectingWs {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const onMessageRef = useRef(onMessage);
  const onOpenRef = useRef(onOpen);
  onMessageRef.current = onMessage;
  onOpenRef.current = onOpen;

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      try {
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onopen = () => {
          setConnected(true);
          onOpenRef.current?.();
        };
        ws.onclose = () => {
          setConnected(false);
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
        };
        ws.onerror = () => setConnected(false);
        ws.onmessage = (event) => onMessageRef.current(event);
      } catch {
        setConnected(false);
      }
    };

    connect();
    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [url]);

  const send = useCallback((frame: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame));
    }
  }, []);

  return { connected, send };
}
