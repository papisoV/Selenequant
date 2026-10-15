// lib/hooks/use-monitor-feed.ts — 底部监控条数据源:独立 WS(ALL 订阅)+ REST 初值 + demo 兜底
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  adaptAnomaly,
  adaptFundingRate,
  adaptLargeTrade,
  adaptLiquidation,
  adaptSignal,
  FEED_SUBSCRIPTIONS,
  makeSampleItems,
  mergeFeedItem,
  type AnomalyData,
  type FeedDraft,
  type FeedItem,
  type FundingRateData,
  type LargeTradeData,
  type LiquidationData,
  type SignalData,
} from "@/lib/monitor-feed";

// 直连后端(复用 tactical 组件已验证模式;不走 Next 代理,规避 localhost→IPv6 断链)
// 注意:必须用 127.0.0.1 而非 localhost —— Chromium 里 localhost 先解析 ::1,uvicorn 只绑 IPv4,
// 并发连接会卡死在 ::1 连接池(实测 8 并发全超时,127.0.0.1 全 200)
// host 统一走 resolveBackendHost(LOCAL_API_BASE 注入 → NEXT_PUBLIC_API_HOST → 127.0.0.1:8088)
import { resolveBackendHost } from "@/lib/backend-host";
const API_HOST = resolveBackendHost();
const WS_URL = `ws://${API_HOST}/api/v1/ws/markets`;
const REST_BASE = `http://${API_HOST}`;

export interface MonitorFeed {
  items: FeedItem[];
  source: "live" | "demo";
  connected: boolean;
  clearFeed: () => void;
}

const DEMO_FALLBACK_MS = 8000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;
// 订阅 ALL 是持续洪泛(实测单交易所 ~5k msg/s,双交易所翻倍):逐条 setState 会打满主线程。
// 按此间隔把缓冲区内所有草稿合并进单次 setItems,渲染频率固定在 ~5 次/s。
const FLUSH_MS = 200;

interface MarketMessage {
  type?: string;
  data?: unknown;
}

function isLiquidation(d: unknown): d is LiquidationData {
  return !!d && typeof d === "object";
}

export function useMonitorFeed(): MonitorFeed {
  const [items, setItems] = useState<FeedItem[]>([]);
  const [connected, setConnected] = useState(false);
  const [source, setSource] = useState<MonitorFeed["source"]>("live");
  const seqRef = useRef(0);
  const gotLiveRef = useRef(false);
  const demoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // WS 洪泛缓冲:onmessage 只入队,FLUSH_MS 定时器批量合并
  const pendingRef = useRef<FeedDraft[]>([]);

  const clearFeed = useCallback(() => {
    setItems([]);
  }, []);

  // demo 兜底:mount 后 ~8s 仍无 live 数据时探测一次健康——
  // 只有确认不可达(网络级错误,如连接拒绝)才注入演示条目;
  // 2xx/非 2xx/超时都算"可达或状态不明",不上 demo,避免健康但流静默时误闪
  useEffect(() => {
    demoTimerRef.current = setTimeout(async () => {
      if (gotLiveRef.current) return;
      try {
        await fetch(`${REST_BASE}/api/v1/health`, { signal: AbortSignal.timeout(3000) });
      } catch (e) {
        // AbortSignal.timeout 抛 DOMException name="TimeoutError" —— 状态不明,不 demo
        if (e instanceof DOMException && e.name === "TimeoutError") return;
        if (gotLiveRef.current) return;
        setSource("demo");
        setItems(makeSampleItems(Date.now()));
      }
    }, DEMO_FALLBACK_MS);
    return () => {
      if (demoTimerRef.current) clearTimeout(demoTimerRef.current);
    };
  }, []);

  // REST 初值(尽力而为,不阻塞 UI)
  useEffect(() => {
    let cancelled = false;
    const getJson = async (path: string): Promise<unknown> => {
      try {
        const res = await fetch(`${REST_BASE}${path}`);
        if (!res.ok) return null;
        return await res.json();
      } catch {
        return null;
      }
    };
    const applyDrafts = async (
      raw: unknown,
      extract: (raw: unknown) => unknown[],
      adapt: (d: unknown) => FeedDraft | null,
    ) => {
      if (cancelled || gotLiveRef.current) return;
      const drafts = extract(raw)
        .map(adapt)
        .filter((d): d is FeedDraft => d !== null);
      if (drafts.length === 0) return;
      gotLiveRef.current = true;
      if (demoTimerRef.current) {
        clearTimeout(demoTimerRef.current);
        demoTimerRef.current = null;
      }
      setSource("live");
      // 逆序(最旧在前)逐个 merge,最新的落在队首;增量合并,不覆盖此前已上屏条目
      setItems((prev) => {
        let acc = prev;
        for (const d of drafts.reverse()) {
          seqRef.current += 1;
          acc = mergeFeedItem(acc, d, seqRef.current, Date.now());
        }
        return acc;
      });
    };
    // 三端点独立跑:任一先返回就先上屏,不被最慢端点(如冷启动的 liquidations)阻塞
    void getJson("/api/v1/derivatives/liquidations/recent?limit=20").then((liq) =>
      applyDrafts(
        liq,
        (raw) => (raw as { data?: { events?: unknown[] } })?.data?.events ?? [],
        (e) => (isLiquidation(e) ? adaptLiquidation(e) : null),
      ),
    );
    void getJson("/api/v1/derivatives/large-trades/recent?limit=20").then((lt) =>
      applyDrafts(
        lt,
        (raw) => (raw as { data?: { events?: unknown[] } })?.data?.events ?? [],
        (e) => (!!e && typeof e === "object" ? adaptLargeTrade(e as LargeTradeData) : null),
      ),
    );
    void getJson("/api/v1/signals/dashboard?exchange=binance").then((sig) =>
      applyDrafts(
        sig,
        (raw) => (Array.isArray(raw) ? raw : (raw as { data?: unknown[] })?.data ?? []),
        (e) => (!!e && typeof e === "object" ? adaptSignal(e as SignalData) : null),
      ),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // 批量节流:onmessage 只标记 live + 入缓冲,不逐条 setState
  useEffect(() => {
    const timer = setInterval(() => {
      const pending = pendingRef.current;
      if (pending.length === 0) return;
      pendingRef.current = [];
      const base = seqRef.current + 1;
      seqRef.current += pending.length;
      setItems((prev) => {
        let acc = prev.filter((f) => !f.id.startsWith("demo-"));
        for (let i = 0; i < pending.length; i += 1) {
          acc = mergeFeedItem(acc, pending[i], base + i, Date.now());
        }
        return acc;
      });
    }, FLUSH_MS);
    return () => clearInterval(timer);
  }, []);

  // 独立 WS,指数退避重连
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retries = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        retries = 0;
        setConnected(true);
        for (const { exchange, market_type } of FEED_SUBSCRIPTIONS) {
          ws?.send(
            JSON.stringify({ action: "subscribe", symbols: ["ALL"], exchange, market_type })
          );
        }
      };
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data as string) as MarketMessage;
          const d = msg.data;
          if (!d || typeof d !== "object") return;
          let draft: FeedDraft | null = null;
          switch (msg.type) {
            case "liquidation":
              draft = adaptLiquidation(d as LiquidationData);
              break;
            case "large_trade":
            case "large_order":
              draft = adaptLargeTrade(d as LargeTradeData);
              break;
            case "funding_rate":
              draft = adaptFundingRate(d as FundingRateData);
              break;
            case "signal":
              draft = adaptSignal(d as SignalData);
              break;
            case "anomaly":
              draft = adaptAnomaly(d as AnomalyData);
              break;
          }
          if (!draft) return;
          // 首条真实数据:标记 live、清 demo 兜底(只做 ref/一次性 setState,不逐条重渲染)
          if (!gotLiveRef.current) {
            gotLiveRef.current = true;
            if (demoTimerRef.current) {
              clearTimeout(demoTimerRef.current);
              demoTimerRef.current = null;
            }
            setSource("live");
          }
          pendingRef.current.push(draft);
        } catch {
          // 忽略坏帧
        }
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          // 已关闭
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (closed) return;
        retries += 1;
        const delay = Math.min(RECONNECT_BASE_MS * 2 ** (retries - 1), RECONNECT_MAX_MS);
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      closed = true;
      setConnected(false);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try {
        ws?.close();
      } catch {
        // 已关闭
      }
    };
  }, [setSource]);

  return { items, source, connected, clearFeed };
}
