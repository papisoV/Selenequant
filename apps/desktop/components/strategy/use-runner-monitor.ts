"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getAccessToken } from "@/lib/auth";

// US-0c 部署监控喂数:REST 初值(runner/status 全 slot)+ 独立 paper WS 增量。
// 不复用 usePaperWs/共享 paper store:顶层 scene-ws-mount 挂 "paper-default",而
// runner.deploy 对 paper-default 会另建专属账户(strategy_runner.py:661),共享 store
// 双路互踩(reset 清对方数据);RunnerMonitorPanel 本就是父组件注入式设计,喂 props 即可。
// 不走 api-bridge:deploy 编译+warmup 可能超 api-client 12s 全局超时,动作用裸 fetch 30s。
// runner_status 仅在 K 线 close 时广播(1h 周期 = 每小时一拍),REST 初值是主体,WS 是增量。

export interface MonitorSignal {
  ts: number;
  strategy_id: string;
  deployment_id: string;
  signal: Record<string, unknown> | string;
  price?: number;
  symbol?: string;
}

export interface RunnerSlot {
  deployment_id: string;
  strategy_id: string;
  account_id: string;
  symbol: string;
  interval: string;
  position: string;
  bar_count: number;
  signals_emitted: number;
  orders_placed: number;
  errors: number;
  last_signal_ts: number;
  recent_signals?: Array<{
    ts: number;
    side: string;
    reason: string;
    strength?: number | null;
    price?: number;
    symbol?: string;
  }>;
}

// recent_signals(平铺 side/reason,旧→新)→ 面板 WsSignal 形状(signal 对象包裹,新→旧)
export function toMonitorSignals(slot: RunnerSlot): MonitorSignal[] {
  return [...(slot.recent_signals ?? [])].reverse().map((s) => ({
    ts: s.ts,
    strategy_id: slot.strategy_id,
    deployment_id: slot.deployment_id,
    signal: { side: s.side, reason: s.reason, strength: s.strength ?? null },
    price: s.price,
    symbol: s.symbol,
  }));
}

interface RunnerStatusBody {
  data?: { running?: boolean; deployments?: RunnerSlot[] } | null;
  error?: { message?: string } | null;
}

const MAX_SIGNALS = 50;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function useRunnerMonitor(strategyId: string) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [slot, setSlot] = useState<RunnerSlot | null>(null);
  const [signals, setSignals] = useState<MonitorSignal[]>([]);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState<"deploy" | "stop" | null>(null);
  const strategyIdRef = useRef(strategyId);
  useEffect(() => {
    strategyIdRef.current = strategyId;
  });

  // REST 初值:runner 内存 slot = 实时真值;无匹配 slot = 该策略无活跃部署
  const refresh = useCallback(async (): Promise<RunnerSlot | null> => {
    const r = await fetch("/api/v1/strategies/runner/status", {
      signal: AbortSignal.timeout(30_000),
    });
    const body = (await r.json()) as RunnerStatusBody;
    if (!r.ok) throw new Error(body?.error?.message ?? `HTTP ${r.status}`);
    const mine =
      (body?.data?.deployments ?? []).find(
        (s) => s.strategy_id === strategyIdRef.current,
      ) ?? null;
    setSlot(mine);
    setSignals(mine ? toMonitorSignals(mine) : []);
    return mine;
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadError(null);
    const load = async (attempt: number): Promise<void> => {
      try {
        await refresh();
        if (alive) setLoading(false);
      } catch (e: unknown) {
        if (!alive) return;
        if (attempt < 3) {
          await new Promise((res) => setTimeout(res, 1500 * (attempt + 1)));
          return load(attempt + 1);
        }
        if (alive) {
          setLoadError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      }
    };
    void load(0);
    return () => {
      alive = false;
    };
  }, [strategyId, refresh]);

  // WS 增量:订阅部署实例专属账户(runner 广播目标);事件稀疏,断线指数退避重连
  const accountId = slot?.account_id ?? null;
  useEffect(() => {
    if (!accountId) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let retries = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      const apiHost = typeof window !== "undefined" ? window.location.host : "";
      if (!apiHost) return;
      const token = getAccessToken();
      const url = token
        ? `ws://${apiHost}/api/v1/ws/paper?token=${encodeURIComponent(token)}`
        : `ws://${apiHost}/api/v1/ws/paper`;
      try {
        ws = new WebSocket(url);
      } catch {
        retries += 1;
        timer = setTimeout(connect, Math.min(RECONNECT_BASE_MS * 2 ** (retries - 1), RECONNECT_MAX_MS));
        return;
      }
      ws.onopen = () => {
        retries = 0;
        setConnected(true);
        try {
          ws?.send(JSON.stringify({ action: "subscribe", account_id: accountId }));
        } catch {
          // ignore
        }
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string) as {
            type?: string;
            data?: Record<string, unknown>;
            timestamp?: number;
          };
          if (
            !msg.type ||
            msg.type === "ping" ||
            msg.type === "subscribed" ||
            msg.type === "unsubscribed"
          )
            return;
          const d = msg.data ?? {};
          if (String(d.strategy_id ?? "") !== strategyIdRef.current) return;
          if (msg.type === "runner_status") {
            // 广播无 recent_signals 字段,spread 保留 REST 初值里的信号历史
            setSlot((prev) => ({ ...(prev ?? ({} as RunnerSlot)), ...d }) as RunnerSlot);
          } else if (msg.type === "strategy_signal") {
            const sig: MonitorSignal["signal"] =
              typeof d.signal === "object" && d.signal !== null
                ? (d.signal as Record<string, unknown>)
                : String(d.signal ?? "");
            const s = typeof sig === "object" ? sig : {};
            setSignals((prev) =>
              [
                {
                  ts: typeof msg.timestamp === "number" ? msg.timestamp : Date.now(),
                  strategy_id: String(d.strategy_id ?? ""),
                  deployment_id: String(d.deployment_id ?? ""),
                  signal: sig,
                  price: typeof s.price === "number" ? s.price : undefined,
                  symbol: typeof s.symbol === "string" ? s.symbol : undefined,
                },
                ...prev,
              ].slice(0, MAX_SIGNALS)
            );
          }
        } catch {
          // 坏帧忽略
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
        timer = setTimeout(connect, Math.min(RECONNECT_BASE_MS * 2 ** (retries - 1), RECONNECT_MAX_MS));
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      try {
        ws?.close();
      } catch {
        // 已关闭
      }
    };
  }, [accountId]);

  const deployPaper = useCallback(async (): Promise<string | null> => {
    setBusy("deploy");
    try {
      const r = await fetch(`/api/v1/strategies/${strategyIdRef.current}/deploy-paper`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: "paper-default" }),
        signal: AbortSignal.timeout(30_000),
      });
      const body = (await r.json()) as {
        data?: { runner_status?: string; runner_error?: string | null } | null;
        error?: { message?: string } | null;
      };
      if (!r.ok) return body?.error?.message ?? `HTTP ${r.status}`;
      if (body.data?.runner_status === "failed") return body.data.runner_error ?? "部署失败";
      await refresh();
      return null;
    } catch (e: unknown) {
      return e instanceof Error ? e.message : String(e);
    } finally {
      setBusy(null);
    }
  }, [refresh]);

  const stopPaper = useCallback(async (): Promise<string | null> => {
    setBusy("stop");
    try {
      const r = await fetch(`/api/v1/strategies/${strategyIdRef.current}/stop-deployment`, {
        method: "POST",
        signal: AbortSignal.timeout(30_000),
      });
      const body = (await r.json()) as { error?: { message?: string } | null };
      if (!r.ok) return body?.error?.message ?? `HTTP ${r.status}`;
      setSlot(null);
      setSignals([]);
      setConnected(false);
      return null;
    } catch (e: unknown) {
      return e instanceof Error ? e.message : String(e);
    } finally {
      setBusy(null);
    }
  }, []);

  const retry = useCallback(async () => {
    setLoadError(null);
    setLoading(true);
    try {
      await refresh();
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [refresh]);

  return {
    loading,
    loadError,
    slot,
    signals,
    connected,
    busy,
    deployment: slot
      ? { deploymentId: slot.deployment_id, accountId: slot.account_id }
      : null,
    deployPaper,
    stopPaper,
    refresh: retry,
  };
}
