"use client";

import { resolveBackendHost } from "@/lib/backend-host";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useMarketStore } from "@/lib/store/market";
import {
  extractSequence,
  MarketMessage,
  MarketMessageType,
  MarketsWsState,
  StreamState,
} from "./use-markets-ws";

// ws/markets 单例 Provider
// 目标：所有组件共享 1 条 WS 连接，组件通过 subscribe(symbols, handlers) 注册
// Provider 聚合所有 symbols 去重后订阅，消息按 msg.data.symbol 路由到匹配的 handler
//
// 关键不变量：
// - 同一时刻只有 1 个 new WebSocket(...)（Provider 内部）
// - 多组件订阅相同 symbol：消息广播给所有匹配 handler
// - symbols 集合为空时不连接（避免空连接）
// - 组件 unmount 自动 unsubscribe，symbols 减少时延迟 5s 退订（避免快速切换冷启动）

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const STALE_AFTER_MS = 5000;
const UNSUB_DELAY_MS = 5000;
// 从未收到首条消息的超时：超过即判 degraded（激活 REST 兜底轮询）。
// 防呆：正常后端启动 WS 1s 内握上，不会触发；后端真不可达时不再静默。
const FIRST_MESSAGE_TIMEOUT_MS = 10_000;

export interface MarketsWsSubscription {
  symbols: string[];
  exchange: string;
  marketType: string;
  interval: string;
  onMessage?: (msg: MarketMessage) => void;
  onMessageBatch?: (msgs: MarketMessage[]) => void;
  onSymbolsChange?: () => void;
  onIntervalChange?: (oldInterval: string, newInterval: string) => void;
}

export interface MarketsWsContextValue {
  state: MarketsWsState;
  subscribe: (sub: MarketsWsSubscription) => () => void;
}

const MarketsWsContext = createContext<MarketsWsContextValue | null>(null);

interface SubscriptionEntry extends MarketsWsSubscription {
  id: number;
}

interface AggregatedSub {
  // 所有 active subscriptions 的 union symbols（可能含 "ALL" 全局通配）
  symbols: Set<string>;
  // exchange / marketType 聚合为 Set（多组件可能订阅不同 exchange/marketType，
  // 每条 WS 连接需要按 exchange 分别发 subscribe）
  exchanges: Set<string>;
  marketTypes: Set<string>;
  // interval 也聚合为 Set（不同组件可能订阅不同 interval）
  intervals: Set<string>;
}

let nextSubId = 1;

export function MarketsWsProvider({ children }: { children: ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const lastSeqRef = useRef<number | null>(null);
  const staleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedAtRef = useRef<number>(Date.now());

  // 所有 active subscriptions
  const subsRef = useRef<Map<number, SubscriptionEntry>>(new Map());
  // 当前已发送给 ws 的订阅状态（用于 onopen 重连恢复、变化检测）
  const sentRef = useRef<AggregatedSub>({
    symbols: new Set(),
    exchanges: new Set(["binance"]),
    marketTypes: new Set(["futures"]),
    intervals: new Set(["1h"]),
  });
  // 延迟退订 timer handle
  const pendingUnsubRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [state, setState] = useState<MarketsWsState>({
    connected: false,
    streamState: "recovering",
    lastMessageAt: null,
  });

  // 聚合所有 active subs，返回 union symbols + exchanges/marketTypes/intervals
  const aggregate = useCallback((): AggregatedSub => {
    const symbols = new Set<string>();
    const intervals = new Set<string>();
    const exchanges = new Set<string>();
    const marketTypes = new Set<string>();
    subsRef.current.forEach((s) => {
      s.symbols.forEach((sym) => symbols.add(sym));
      if (s.interval) intervals.add(s.interval);
      if (s.exchange) exchanges.add(s.exchange);
      if (s.marketType) marketTypes.add(s.marketType);
    });
    if (intervals.size === 0) intervals.add("1h");
    if (exchanges.size === 0) exchanges.add("binance");
    if (marketTypes.size === 0) marketTypes.add("futures");
    return { symbols, exchanges, marketTypes, intervals };
  }, []);

  const sendSubscribe = useCallback(
    (
      ws: WebSocket,
      syms: string[],
      ex: string,
      mt: string,
      ivl: string,
    ) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      if (syms.length === 0) return;
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
        // ignore
      }
    },
    [],
  );

  const sendUnsubscribe = useCallback(
    (
      ws: WebSocket,
      syms: string[],
      ex: string,
      mt: string,
      ivl?: string,
    ) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      if (syms.length === 0) return;
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
        // ignore
      }
    },
    [],
  );

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

  const connect = useCallback(() => {
    if (intentionalCloseRef.current) return;
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }
    // 无订阅时不连接（避免空连接）
    if (subsRef.current.size === 0) return;

    // 走同源代理：浏览器从 :3000 页面连 ws://localhost:3000/api/v1/ws/markets，
    // 由 Next rewrite 转发到 :8088。同源连接不触发本地代理(Clash 7897)拦截——
    // 直连 ws://localhost:8088 会被 Clash 系统代理在握手前关闭
    // （"WebSocket is closed before the connection is established"）。
    // SSR 安全：服务端无 window，回退到 localhost:8088（仅占位，实际 WS 只在浏览器建）。
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
      // 重连后用 sentRef 恢复订阅（按每个 exchange 分别发 subscribe）
      const cur = sentRef.current;
      const symArr = Array.from(cur.symbols);
      cur.intervals.forEach((ivl) => {
        cur.exchanges.forEach((ex) => {
          cur.marketTypes.forEach((mt) => {
            sendSubscribe(ws, symArr, ex, mt, ivl);
          });
        });
      });
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as MarketMessage;
        if ((msg as { type?: string }).type === "ping") return;

        // 序列号间隙检测
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

        // 按 symbol 路由：找所有 sub 中 symbols 包含此 symbol 的
        // 若 msg.data.symbol 缺失（如 liquidation_stats 全局消息），广播给所有 sub
        // 若 sub.symbols 含 "ALL"（全局通配），接收所有消息（不管 symbol）
        const msgSymbol =
          msg.data && typeof msg.data === "object"
            ? (msg.data as Record<string, unknown>).symbol
            : undefined;
        const msgSymbolStr =
          typeof msgSymbol === "string" ? msgSymbol : undefined;
        // 消息的 exchange（ticker 等事件 data 里带 exchange 字段）
        const msgExchange =
          msg.data && typeof msg.data === "object"
            ? (msg.data as Record<string, unknown>).exchange
            : undefined;
        const msgExchangeStr =
          typeof msgExchange === "string" ? msgExchange : undefined;

        const matched: SubscriptionEntry[] = [];
        subsRef.current.forEach((s) => {
          // exchange 过滤：若消息带 exchange 且 sub 声明了 exchange，需匹配
          if (msgExchangeStr && s.exchange && s.exchange !== msgExchangeStr) {
            return;
          }
          // symbol 匹配：ALL 通配或精确匹配，或消息无 symbol 广播所有
          if (
            s.symbols.includes("ALL") ||
            !msgSymbolStr ||
            s.symbols.includes(msgSymbolStr)
          ) {
            matched.push(s);
          }
        });

        // 区分 batch vs single handler
        const batchHandlers: SubscriptionEntry[] = [];
        const singleHandlers: SubscriptionEntry[] = [];
        matched.forEach((s) => {
          if (s.onMessageBatch) batchHandlers.push(s);
          else if (s.onMessage) singleHandlers.push(s);
        });

        // batch handlers: 各自积到下一帧（每个 sub 独立 RAF）
        if (batchHandlers.length > 0) {
          // 简化：直接调用各 batch handler（RAF 由 use-raf-batch 或组件内自己做）
          // 这里同步 push，batch handler 自己决定是否 RAF
          batchHandlers.forEach((s) => s.onMessageBatch!([msg]));
        }
        if (singleHandlers.length > 0) {
          singleHandlers.forEach((s) => s.onMessage!(msg));
        }
      } catch {
        // ignore parse error
      }
    };

    ws.onerror = () => {
      // 从未收到过消息时不拨回 recovering——否则会覆盖 staleness 已判定的
      // degraded，状态位随重连周期闪烁（degraded→recovering→degraded 每秒循环）
      setState((s) => ({
        ...s,
        streamState: s.lastMessageAt === null ? s.streamState : "recovering",
      }));
    };

    ws.onclose = () => {
      wsRef.current = null;
      setState((s) => ({
        ...s,
        connected: false,
        streamState: s.lastMessageAt === null ? s.streamState : "recovering",
      }));
      scheduleReconnect();
    };
  }, [scheduleReconnect, sendSubscribe]);

  // staleness 检测定时器
  useEffect(() => {
    staleTimerRef.current = setInterval(() => {
      setState((s) => {
        // 从未收到消息：有真实订阅且超 FIRST_MESSAGE_TIMEOUT_MS 仍无首条 →
        // degraded。原逻辑 lastMessageAt === null 永远 return，导致"从未连上"
        // 永远卡 recovering、REST 兜底永不激活（2026-09-30 WS 全瘫两周零报警盲区）。
        // 无订阅时 Provider 不建 WS，守卫防非行情页误报。
        if (s.lastMessageAt === null) {
          if (
            sentRef.current.symbols.size > 0 &&
            Date.now() - mountedAtRef.current > FIRST_MESSAGE_TIMEOUT_MS
          ) {
            return { ...s, streamState: "degraded" };
          }
          return s;
        }
        if (s.streamState === "recovering") return s;
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
  }, []);

  // 同步 streamState 到 market store
  useEffect(() => {
    useMarketStore.getState().setMarketsStreamState(state.streamState);
  }, [state.streamState]);

  // REST polling fallback: degraded 时按当前 sentRef.symbols 轮询 ticker
  useEffect(() => {
    if (state.streamState !== "degraded") return;
    if (sentRef.current.symbols.size === 0) return;

    const apiHost =
      typeof window !== "undefined"
        ? window.location.host
        : resolveBackendHost();
    const poller = setInterval(async () => {
      if (state.streamState !== "degraded") return;
      const syms = Array.from(sentRef.current.symbols);
      for (const sym of syms) {
        try {
          const resp = await fetch(
            `http://${apiHost}/api/v1/markets/${encodeURIComponent(sym)}/ticker`,
          );
          if (!resp.ok) continue;
          const json = await resp.json();
          const data = json?.data ?? json;
          if (!data) continue;
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
          // 路由给匹配 sub（ALL 通配同样命中）
          const matched: SubscriptionEntry[] = [];
          subsRef.current.forEach((s) => {
            if (s.symbols.includes("ALL") || s.symbols.includes(sym)) {
              matched.push(s);
            }
          });
          matched.forEach((s) => {
            if (s.onMessageBatch) s.onMessageBatch([msg]);
            else s.onMessage?.(msg);
          });
        } catch {
          // ignore
        }
      }
    }, 5000);

    return () => clearInterval(poller);
  }, [state.streamState]);

  // 当 subs 变化时重算聚合，发 sub/unsub 消息（不重建 ws）
  const syncSubscriptions = useCallback(() => {
    const agg = aggregate();
    const prev = sentRef.current;

    const ws = wsRef.current;
    const isReady = ws && ws.readyState === WebSocket.OPEN;

    // 计算新增 symbols（agg 有，prev 没有）
    const newSyms = Array.from(agg.symbols).filter((s) => !prev.symbols.has(s));
    // 计算移除的 symbols（prev 有，agg 没有）
    const removedSyms = Array.from(prev.symbols).filter((s) => !agg.symbols.has(s));

    // 新增 intervals
    const newIntervals = Array.from(agg.intervals).filter((i) => !prev.intervals.has(i));
    // 移除 intervals
    const removedIntervals = Array.from(prev.intervals).filter((i) => !agg.intervals.has(i));

    // 新增 exchanges/marketTypes（组合订阅新 symbols）
    const newExchanges = Array.from(agg.exchanges).filter((e) => !prev.exchanges.has(e));
    const removedExchanges = Array.from(prev.exchanges).filter((e) => !agg.exchanges.has(e));
    const newMarketTypes = Array.from(agg.marketTypes).filter((m) => !prev.marketTypes.has(m));
    const removedMarketTypes = Array.from(prev.marketTypes).filter((m) => !agg.marketTypes.has(m));

    if (isReady && ws) {
      // 立即订阅新增 symbols（每个 exchange × marketType × interval 组合都订阅）
      if (newSyms.length > 0) {
        agg.exchanges.forEach((ex) => {
          agg.marketTypes.forEach((mt) => {
            agg.intervals.forEach((ivl) => {
              sendSubscribe(ws, newSyms, ex, mt, ivl);
            });
          });
        });
      }
      // 退订移除 intervals 的所有 symbols
      if (removedIntervals.length > 0) {
        removedIntervals.forEach((ivl) => {
          prev.exchanges.forEach((ex) => {
            prev.marketTypes.forEach((mt) => {
              sendUnsubscribe(ws, Array.from(prev.symbols), ex, mt, ivl);
            });
          });
        });
      }
      // 订阅新增 intervals 的所有 symbols
      if (newIntervals.length > 0) {
        newIntervals.forEach((ivl) => {
          agg.exchanges.forEach((ex) => {
            agg.marketTypes.forEach((mt) => {
              sendSubscribe(ws, Array.from(agg.symbols), ex, mt, ivl);
            });
          });
        });
      }
      // 新增 exchange/marketType：为其订阅当前全部 symbols
      if (newExchanges.length > 0 || newMarketTypes.length > 0) {
        agg.exchanges.forEach((ex) => {
          agg.marketTypes.forEach((mt) => {
            if (newExchanges.includes(ex) || newMarketTypes.includes(mt)) {
              agg.intervals.forEach((ivl) => {
                sendSubscribe(ws, Array.from(agg.symbols), ex, mt, ivl);
              });
            }
          });
        });
      }
      // 移除 exchange/marketType：退订其全部 symbols
      if (removedExchanges.length > 0 || removedMarketTypes.length > 0) {
        prev.exchanges.forEach((ex) => {
          prev.marketTypes.forEach((mt) => {
            if (removedExchanges.includes(ex) || removedMarketTypes.includes(mt)) {
              prev.intervals.forEach((ivl) => {
                sendUnsubscribe(ws, Array.from(prev.symbols), ex, mt, ivl);
              });
            }
          });
        });
      }

      // 延迟退订移除的 symbols（5s 后若仍未回来则发 unsubscribe）
      if (removedSyms.length > 0) {
        if (pendingUnsubRef.current) {
          clearTimeout(pendingUnsubRef.current);
          pendingUnsubRef.current = null;
        }
        const oldExchanges = Array.from(prev.exchanges);
        const oldMarketTypes = Array.from(prev.marketTypes);
        pendingUnsubRef.current = setTimeout(() => {
          const currentWs = wsRef.current;
          if (currentWs && currentWs.readyState === WebSocket.OPEN) {
            oldExchanges.forEach((ex) => {
              oldMarketTypes.forEach((mt) => {
                sendUnsubscribe(currentWs, removedSyms, ex, mt);
              });
            });
          }
          pendingUnsubRef.current = null;
        }, UNSUB_DELAY_MS);
      }
    }

    sentRef.current = agg;

    // 若当前未连接但有订阅，触发连接
    if (
      agg.symbols.size > 0 &&
      !wsRef.current &&
      !intentionalCloseRef.current
    ) {
      connect();
    }

    // 若无订阅且有连接，关闭
    if (agg.symbols.size === 0 && wsRef.current) {
      intentionalCloseRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      try { wsRef.current.close(); } catch { /* ignore */ }
      wsRef.current = null;
      intentionalCloseRef.current = false;
    }

    // 通知 sub onSymbolsChange（symbols 集合变化时）
    // 简化：只在 newSyms/removedSyms 非空时通知
    if (newSyms.length > 0 || removedSyms.length > 0) {
      subsRef.current.forEach((s) => {
        // 只通知自身 symbols 集合发生变化的 sub
        const selfChanged =
          s.symbols.some((sym) => newSyms.includes(sym) || removedSyms.includes(sym));
        if (selfChanged) s.onSymbolsChange?.();
      });
    }

    // 通知 interval 变化
    if (newIntervals.length > 0 || removedIntervals.length > 0) {
      subsRef.current.forEach((s) => {
        if (s.onIntervalChange && s.interval) {
          if (newIntervals.includes(s.interval) || removedIntervals.includes(s.interval)) {
            // 找 prev interval（取 removed 中第一个或 prev 主 interval）
            const oldIvl = removedIntervals[0] ?? Array.from(prev.intervals)[0] ?? s.interval;
            s.onIntervalChange(oldIvl, s.interval);
          }
        }
      });
    }
  }, [aggregate, connect, sendSubscribe, sendUnsubscribe]);

  // subscribe: 注册一个 sub，返回 unsubscribe 函数
  const subscribe = useCallback(
    (sub: MarketsWsSubscription) => {
      const id = nextSubId++;
      const entry: SubscriptionEntry = { ...sub, id };
      subsRef.current.set(id, entry);

      // 同步聚合状态，发 sub 消息
      syncSubscriptions();

      return () => {
        subsRef.current.delete(id);
        syncSubscriptions();
      };
    },
    [syncSubscriptions],
  );

  // cleanup on unmount
  // 注意：不设 intentionalCloseRef.current = true。
  // React 18 StrictMode dev 双调用 effect（mount→cleanup→mount），cleanup 设 true 后
  // 第二次 mount 的 connect() 被 `if (intentionalCloseRef.current) return` 跳过，
  // 导致 WS 永不重连（2026-08-07 事故：onclose 1006 后无 new WebSocket，卡 recovering）。
  // 真正 unmount 时组件销毁，closure GC，无需 intentionalCloseRef 阻止重连；
  // 重连 timer 已 clearTimeout，不会在卸载后触发。
  useEffect(() => {
    return () => {
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
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* ignore */ }
        wsRef.current = null;
      }
    };
  }, []);

  const value = useMemo<MarketsWsContextValue>(
    () => ({ state, subscribe }),
    [state, subscribe],
  );

  return (
    <MarketsWsContext.Provider value={value}>
      {children}
    </MarketsWsContext.Provider>
  );
}

export function useMarketsWsContext(): MarketsWsContextValue {
  const ctx = useContext(MarketsWsContext);
  if (!ctx) {
    throw new Error(
      "useMarketsWsContext must be used within a MarketsWsProvider",
    );
  }
  return ctx;
}

// 兼容类型导出（让旧 import 路径仍可用）
export type { MarketMessageType, StreamState, MarketsWsState };
