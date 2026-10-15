"use client";

import { resolveBackendHost } from "@/lib/backend-host";
import { useEffect, useRef, useState } from "react";
import { useMarketStore } from "@/lib/store/market";
import { useMarketsWsContext } from "./markets-ws-provider";

// ws/markets — per-symbol channel
// message: { type: "ticker"|"kline"|"depth"|..., data: {...}, timestamp: "...", seq?: number }
// 来源：services/local-api/app/api/v1/ws.py 的 ws_markets handler

export type MarketMessageType =
  | "ticker"
  | "kline"
  | "depth"
  | "kline_snapshot"
  | "large_trade"
  | "large_order"
  | "liquidation"
  | "liquidation_stats"
  | "funding_rate"
  | "footprint_bar"
  | "vpvr_update"
  | "options_ticker";

export interface MarketMessage {
  type: MarketMessageType;
  data: Record<string, unknown>;
  timestamp?: string;
  seq?: number;
}

// 流状态三态（参考旧代码 use-market-ws.ts:14）
// live: 正常接收消息 / degraded: 超过 STALE_AFTER_MS 无消息 / recovering: 重连中
export type StreamState = "live" | "degraded" | "recovering";

export interface MarketsWsState {
  connected: boolean;
  streamState: StreamState;
  lastMessageAt: number | null;
}

interface UseMarketsWsOptions {
  symbols: string[];
  exchange?: string;
  marketType?: string;
  interval?: string;
  onMessage?: (msg: MarketMessage) => void;
  enabled?: boolean;
  // 可选 RAF 批处理：高频消息（ticker/depth/kline）积到下一帧一次回调
  // 启用时 onMessageBatch 代替 onMessage，每帧积累的消息一次性传入
  onMessageBatch?: (msgs: MarketMessage[]) => void;
  // symbols 变化时回调（让消费者清旧 klines / ticker 等）
  onSymbolsChange?: () => void;
  // interval 变化时回调（让消费者清旧 interval klines）
  onIntervalChange?: (oldInterval: string, newInterval: string) => void;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const STALE_AFTER_MS = 5000;
// 延迟退订：用户切回原 symbol 5s 内可避免冷启动（参考旧代码 use-market-ws.ts:12）
const UNSUB_DELAY_MS = 5000;

// 序列号间隙检测（参考旧代码 use-market-ws.ts:37-44）
// 仅识别后端在 payload 顶层显式注入的全局连续 seq/sequence。
// 后端 broadcast_market_event 的 payload 形如 {type, symbol, data, timestamp}，
// 不注入顶层 seq；data 内的 u/update_id 是交易所原始字段（per-stream 单调，跨源/跨 symbol
// 不连续），绝不能用作全局序号——否则多源 WS 下必然跳号 → ws.close() 死循环
// （2026-08-07 事故：WS 连上 2s 即断，recovering 永不转 live）。
// 顶层无 seq/sequence 时返回 null → 间隙检测不触发，连接保持。
export function extractSequence(msg: unknown): number | null {
  if (!msg || typeof msg !== "object") return null;
  const m = msg as Record<string, unknown>;
  const raw = m.seq ?? m.sequence;
  const seq = typeof raw === "string" ? Number(raw) : raw;
  return typeof seq === "number" && Number.isFinite(seq) ? seq : null;
}

interface SubRecord {
  symbols: string[];
  exchange: string;
  marketType: string;
  interval: string;
}

export function useMarketsWsInternal({
  symbols,
  exchange = "binance",
  marketType = "futures",
  interval = "1h",
  onMessage,
  onMessageBatch,
  enabled = true,
  onSymbolsChange,
  onIntervalChange,
}: UseMarketsWsOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onMessage);
  const batchHandlerRef = useRef(onMessageBatch);
  const onSymbolsChangeRef = useRef(onSymbolsChange);
  const onIntervalChangeRef = useRef(onIntervalChange);
  // 把回调 ref 同步移进 effect,避免 render body 直接写 ref（React 19 并发渲染契约）
  useEffect(() => {
    handlerRef.current = onMessage;
    batchHandlerRef.current = onMessageBatch;
    onSymbolsChangeRef.current = onSymbolsChange;
    onIntervalChangeRef.current = onIntervalChange;
  });

  // RAF 批处理缓冲：当 onMessageBatch 提供时启用
  const pendingBatchRef = useRef<MarketMessage[]>([]);
  const rafIdRef = useRef<number | null>(null);

  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const lastSeqRef = useRef<number | null>(null);
  const staleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 当前订阅状态（用于 ws.onopen 重连后恢复订阅、symbols/interval 变化检测）
  const subRef = useRef<SubRecord>({
    symbols,
    exchange,
    marketType,
    interval,
  });
  // 延迟退订 timer handle
  const pendingUnsubRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 上一次 interval（变化检测）
  const prevIntervalRef = useRef<string | undefined>(undefined);
  // 上一次 symbols join（变化检测）
  const prevSymbolsKeyRef = useRef<string | undefined>(undefined);

  const [state, setState] = useState<MarketsWsState>({
    connected: false,
    streamState: "recovering",
    lastMessageAt: null,
  });

  // 指数退避重连（参考旧代码 use-market-ws.ts:246-251）
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

  // 发送 subscribe 消息（运行时增订阅，不重建 ws）
  const sendSubscribe = (
    ws: WebSocket,
    syms: string[],
    ex: string,
    mt: string,
    ivl: string,
  ) => {
    if (ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(
        JSON.stringify({
          action: "subscribe",
          symbols: syms,
          exchange: ex,
          market_type: mt,
          interval: ivl,
        }),
      );
    } catch {
      // ignore send error
    }
  };

  // 发送 unsubscribe 消息（可只退订特定 interval 的 kline stream）
  const sendUnsubscribe = (
    ws: WebSocket,
    syms: string[],
    ex: string,
    mt: string,
    ivl?: string,
  ) => {
    if (ws.readyState !== WebSocket.OPEN) return;
    try {
      const payload: Record<string, unknown> = {
        action: "unsubscribe",
        symbols: syms,
        exchange: ex,
        market_type: mt,
      };
      if (ivl !== undefined) payload.interval = ivl;
      ws.send(JSON.stringify(payload));
    } catch {
      // ignore send error
    }
  };

  const connect = () => {
    if (intentionalCloseRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    // 走同源代理：window.location.host = :3000，Next rewrite 转发到 :8088。
    // 同源连接避免本地代理(Clash 7897)拦截 WS 握手——直连 :8088 会被握手前关闭。
    const apiHost =
      typeof window !== "undefined"
        ? window.location.host
        : resolveBackendHost();
    const wsUrl = `ws://${apiHost}/api/v1/ws/markets`;
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
      // 重连后用 subRef 里的当前订阅状态恢复订阅（涵盖 ws 重建场景）
      const cur = subRef.current;
      sendSubscribe(ws, cur.symbols, cur.exchange, cur.marketType, cur.interval);
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as MarketMessage;
        if ((msg as { type?: string }).type === "ping") return;

        // 序列号间隙检测：跳号 >1 → 触发重连
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

        // RAF 批处理：onMessageBatch 提供时积到下一帧，否则直接回调
        if (batchHandlerRef.current) {
          pendingBatchRef.current.push(msg);
          if (rafIdRef.current === null) {
            rafIdRef.current = requestAnimationFrame(() => {
              rafIdRef.current = null;
              const items = pendingBatchRef.current;
              pendingBatchRef.current = [];
              if (items.length > 0 && batchHandlerRef.current) {
                batchHandlerRef.current(items);
              }
            });
          }
        } else {
          handlerRef.current?.(msg);
        }
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

  // 连接级 effect：仅 exchange / marketType / enabled 变化才重建 ws
  // symbols / interval 变化走下方单独的 effect 发 sub/unsub 消息，不重建 ws
  useEffect(() => {
    if (!enabled || symbols.length === 0) return;
    intentionalCloseRef.current = false;
    // 重置 subRef 与当前 props 同步（exchange/marketType 切换时新连接用新参数）
    subRef.current = {
      symbols,
      exchange,
      marketType,
      interval,
    };
    prevSymbolsKeyRef.current = symbols.join(",");
    prevIntervalRef.current = interval;
    connect();

    return () => {
      // 不设 intentionalCloseRef=true：StrictMode 双调用会导致 mount2 的 connect() 被跳过，WS 永不重连
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (pendingUnsubRef.current) {
        clearTimeout(pendingUnsubRef.current);
        pendingUnsubRef.current = null;
      }
      if (staleTimerRef.current) {
        clearInterval(staleTimerRef.current);
        staleTimerRef.current = null;
      }
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
      pendingBatchRef.current = [];
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* ignore */ }
        wsRef.current = null;
      }
      attemptRef.current = 0;
      lastSeqRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchange, marketType, enabled]);

  // symbols 变化：sub/unsub 消息驱动，不重建 ws
  // 立即发 subscribe(new)，延迟 5s 发 unsubscribe(old)
  // 5s 内切回原 symbol → cancel pending unsub（避免冷启动）
  useEffect(() => {
    const symbolsKey = symbols.join(",");
    const prevKey = prevSymbolsKeyRef.current;
    // 首次 mount 由连接级 effect 处理（prevKey === undefined），这里跳过
    if (prevKey === undefined) {
      prevSymbolsKeyRef.current = symbolsKey;
      return;
    }
    if (symbolsKey === prevKey) return;

    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      // 立即订阅新 symbols（含当前 exchange/marketType/interval）
      sendSubscribe(ws, symbols, exchange, marketType, interval);

      // 取消任何针对 NEW symbols 的 pending unsub（用户切回原 symbol）
      if (pendingUnsubRef.current) {
        clearTimeout(pendingUnsubRef.current);
        pendingUnsubRef.current = null;
      }

      // 延迟退订 OLD symbols（5s 后若未被 cancel 则发 unsubscribe）
      const oldSymbols = subRef.current.symbols;
      const oldExchange = subRef.current.exchange;
      const oldMarketType = subRef.current.marketType;
      pendingUnsubRef.current = setTimeout(() => {
        const currentWs = wsRef.current;
        if (currentWs && currentWs.readyState === WebSocket.OPEN) {
          sendUnsubscribe(currentWs, oldSymbols, oldExchange, oldMarketType);
        }
        pendingUnsubRef.current = null;
      }, UNSUB_DELAY_MS);
    }

    // 更新 subRef（interval/exchange/marketType 维持当前值）
    subRef.current = {
      symbols,
      exchange: subRef.current.exchange,
      marketType: subRef.current.marketType,
      interval: subRef.current.interval,
    };
    prevSymbolsKeyRef.current = symbolsKey;
    lastSeqRef.current = null;
    // 通知消费者清旧 symbols 的 klines / ticker 等
    onSymbolsChangeRef.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols.join(",")]);

  // interval 变化：增量切换，不重建 ws
  // 只退订旧 interval 的 kline stream（ticker/depth 不受影响）
  useEffect(() => {
    const currentInterval = interval || "1h";
    const prevInterval = prevIntervalRef.current;

    if (prevInterval !== undefined && prevInterval !== currentInterval) {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        // 退订旧 interval 的 kline stream（只传 interval，不影响 ticker/depth）
        sendUnsubscribe(
          ws,
          subRef.current.symbols,
          subRef.current.exchange,
          subRef.current.marketType,
          prevInterval,
        );
        // 订阅新 interval
        sendSubscribe(
          ws,
          subRef.current.symbols,
          subRef.current.exchange,
          subRef.current.marketType,
          currentInterval,
        );
      }
      // 通知消费者清旧 interval klines
      onIntervalChangeRef.current?.(prevInterval, currentInterval);
    }

    // 更新 subRef 的 interval 字段
    subRef.current = {
      symbols: subRef.current.symbols,
      exchange: subRef.current.exchange,
      marketType: subRef.current.marketType,
      interval: currentInterval,
    };
    prevIntervalRef.current = currentInterval;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval]);

  // REST polling fallback: when streamState is "degraded", poll ticker endpoint
  // for each subscribed symbol at 5s intervals. Stop when streamState recovers to "live".
  useEffect(() => {
    if (!enabled || symbols.length === 0) return;

    if (state.streamState !== "degraded") return;

    const apiHost =
      typeof window !== "undefined"
        ? window.location.host
        : resolveBackendHost();
    const interval = setInterval(async () => {
      // Re-check state inside the interval — may have recovered
      if (state.streamState !== "degraded") return;

      for (const sym of symbols) {
        try {
          const resp = await fetch(
            `http://${apiHost}/api/v1/markets/${encodeURIComponent(sym)}/ticker`
          );
          if (!resp.ok) continue;
          const json = await resp.json();
          const data = json?.data ?? json;
          if (!data) continue;
          // Emit as a ticker message so consumers update naturally
          const msg: MarketMessage = {
            type: "ticker",
            data: {
              symbol: sym,
              last_price: data.last_price,
              bid: data.bid,
              ask: data.ask,
              volume_24h: data.volume_24h,
              change_24h_pct: data.change_24h_pct,
            },
            timestamp: new Date().toISOString(),
          };
          if (batchHandlerRef.current) {
            batchHandlerRef.current([msg]);
          } else {
            handlerRef.current?.(msg);
          }
        } catch {
          // ignore fetch errors during degraded fallback
        }
      }
    }, 5000);

    return () => clearInterval(interval);
  }, [enabled, symbols, state.streamState]);

  // 同步 streamState 到 market store，让 use-klines 监听 recovering→live 触发 gap 补齐
  useEffect(() => {
    useMarketStore.getState().setMarketsStreamState(state.streamState);
  }, [state.streamState]);

  return { wsRef, state };
}

// ============================================================================
// 兼容 wrapper：通过 MarketsWsProvider 共享单例 WS
// 旧调用方（kf-page / mk-page / use-scene-ws）无需改 import 即可走单例
// 行为差异：底层 WS 只 1 条；消息按 msg.data.symbol 路由到匹配 sub
// ============================================================================
export function useMarketsWsSingleton(options: UseMarketsWsOptions) {
  const { state, subscribe } = useMarketsWsContext();

  // 用 ref 保存最新 options，避免 subscribe 频繁重建
  // 把 options 同步移进 effect,避免 render body 直接写 ref（React 19 并发渲染契约）
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });

  useEffect(() => {
    const unsub = subscribe({
      symbols: optionsRef.current.symbols,
      exchange: optionsRef.current.exchange ?? "binance",
      marketType: optionsRef.current.marketType ?? "futures",
      interval: optionsRef.current.interval ?? "1h",
      onMessage: optionsRef.current.onMessage,
      onMessageBatch: optionsRef.current.onMessageBatch,
      onSymbolsChange: optionsRef.current.onSymbolsChange,
      onIntervalChange: optionsRef.current.onIntervalChange,
    });
    return unsub;
  }, [
    subscribe,
    options.symbols.join(","),
    options.exchange ?? "binance",
    options.marketType ?? "futures",
    options.interval ?? "1h",
  ]);

  // wsRef 兼容：返回 Provider 内部 ws（消费方一般只用 state）
  return { wsRef: { current: null as WebSocket | null }, state };
}

// 默认导出别名：useMarketsWs → 走单例 Provider
// 旧调用方（kf-page / mk-page / use-scene-ws）无需改 import 即共享单例 WS
export const useMarketsWs = useMarketsWsSingleton;
