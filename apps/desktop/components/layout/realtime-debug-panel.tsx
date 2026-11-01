"use client";

import { useEffect, useRef, useState } from "react";
import { useMarketStore } from "@/lib/store/market";
import { usePaperStore } from "@/lib/store/paper";
import { useLiveTradingStore } from "@/lib/store/live-trading";
import { useUiText } from "@/components/i18n/use-ui-text"

function StreamDot({ state }: { state: string }) {
  const color =
    state === "live" ? "#10b981" : state === "degraded" ? "#f59e0b" : "#6b7280";
  return (
    <span
      style={{
        display: "inline-block",
        width: 6,
        height: 6,
        borderRadius: "50%",
        background: color,
        marginRight: 6,
        boxShadow: state === "live" ? `0 0 6px ${color}` : "none",
      }}
    />
  );
}

export function RealtimeDebugPanel() {
  const tr = useUiText()

  const [enabled, setEnabled] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [showInfo, setShowInfo] = useState(false);
  const [pos, setPos] = useState({ x: 12, y: 0 });
  const [tick, setTick] = useState(0);
  const dragState = useRef<{
    startX: number;
    startY: number;
    panelX: number;
    panelY: number;
    moved: boolean;
    dragging: boolean;
  } | null>(null);
  const sizeRef = useRef<{ w: number; h: number }>({ w: 240, h: 140 });

  useEffect(() => {
    const read = () => {
      try {
        return localStorage.getItem("selenespace-debug") === "1";
      } catch {
        return false;
      }
    };
    setEnabled(read());
    const onStorage = (e: StorageEvent) => {
      if (e.key === "selenespace-debug") setEnabled(read());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const marketsStreamState = useMarketStore((s) => s.marketsStreamState);
  const pageFilter = useMarketStore((s) => s.pageFilter);
  const paperStreamState = usePaperStore((s) => s.streamState);
  const liveStreamState = useLiveTradingStore((s) => s.streamState);

  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [enabled]);

  // 初始位置：左下角（client-only,SSR 时 window 不存在）
  useEffect(() => {
    if (enabled) {
      setPos((p) => (p.y === 0 ? { x: 12, y: Math.max(12, window.innerHeight - 160) } : p));
    }
  }, [enabled]);

  const onHeaderMouseDown = (e: React.MouseEvent) => {
    const panel = (e.currentTarget as HTMLElement).closest("[data-debug-panel]") as HTMLElement | null;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    sizeRef.current = { w: rect.width, h: rect.height };
    dragState.current = {
      startX: e.clientX,
      startY: e.clientY,
      panelX: rect.left,
      panelY: rect.top,
      moved: false,
      dragging: true,
    };
    document.body.classList.add("dragging");
    e.preventDefault();
  };

  useEffect(() => {
    if (!enabled) return;
    const onMouseMove = (e: MouseEvent) => {
      const st = dragState.current;
      if (!st || !st.dragging) return;
      const dx = e.clientX - st.startX;
      const dy = e.clientY - st.startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) st.moved = true;
      const newX = st.panelX + dx;
      const newY = st.panelY + dy;
      const w = sizeRef.current.w;
      const h = sizeRef.current.h;
      const clampedX = Math.max(4, Math.min(window.innerWidth - w - 4, newX));
      const clampedY = Math.max(4, Math.min(window.innerHeight - h - 4, newY));
      setPos({ x: clampedX, y: clampedY });
    };
    const onMouseUp = () => {
      const st = dragState.current;
      if (st) st.dragging = false;
      dragState.current = null;
      document.body.classList.remove("dragging");
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [enabled]);

  if (!enabled) return null;

  const exchange = pageFilter.exchange === "all" ? "binance" : pageFilter.exchange;
  const symbol = pageFilter.symbol || "BTC";

  const panelStyle: React.CSSProperties = {
    position: "fixed",
    left: pos.x,
    top: pos.y,
    zIndex: 50,
    borderRadius: 6,
    border: "1px solid rgba(255,255,255,0.08)",
    background: "rgba(20,24,36,0.95)",
    backdropFilter: "blur(6px)",
    fontSize: 11,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    color: "rgba(255,255,255,0.7)",
    boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
    minWidth: 200,
  };

  const headerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "6px 10px",
    cursor: "move",
    userSelect: "none",
    fontWeight: 600,
    color: "rgba(255,255,255,0.9)",
    borderBottom: collapsed ? "none" : "1px solid rgba(255,255,255,0.06)",
  };

  const btnStyle: React.CSSProperties = {
    background: "transparent",
    border: "none",
    color: "rgba(255,255,255,0.5)",
    cursor: "pointer",
    fontSize: 12,
    padding: "0 4px",
    lineHeight: 1,
  };

  return (
    <div style={panelStyle} data-debug-panel="1">
      <div
        style={headerStyle}
        onMouseDown={onHeaderMouseDown}
        title={tr("拖拽移动 · 显示 market/paper/live 三路 WebSocket 连接状态")}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span
            style={{
              cursor: "help",
              borderBottom: "1px dotted rgba(255,255,255,0.3)",
            }}
            onMouseEnter={() => setShowInfo(true)}
            onMouseLeave={() => setShowInfo(false)}
          >
            Realtime Debug
          </span>
          <span style={{ opacity: 0.4, fontWeight: 400 }}>· {tick}s</span>
          {showInfo && (
            <div
              style={{
                position: "absolute",
                top: 28,
                left: 10,
                background: "rgba(10,14,24,0.98)",
                border: "1px solid rgba(255,255,255,0.12)",
                borderRadius: 4,
                padding: "6px 8px",
                fontSize: 10,
                color: "rgba(255,255,255,0.75)",
                maxWidth: 260,
                zIndex: 60,
                pointerEvents: "none",
                lineHeight: 1.4,
              }}
            >
              {tr("显示 market/paper/live 三路 WebSocket 连接状态（live/degraded/recovering），用于诊断实时数据流是否正常。拖拽标题栏可移动，点击 − 折叠。")}
            </div>
          )}
        </div>
        <button
          style={btnStyle}
          onClick={(e) => { e.stopPropagation(); setCollapsed((v) => !v); }}
          title={collapsed ? tr("展开") : tr("折叠")}
        >
          {collapsed ? "＋" : "−"}
        </button>
      </div>
      {!collapsed && (
        <div style={{ padding: "6px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
          <div>
            <StreamDot state={marketsStreamState} />
            market: <span style={{ color: "rgba(255,255,255,0.85)" }}>{marketsStreamState}</span>
          </div>
          <div>
            <StreamDot state={paperStreamState} />
            paper: <span style={{ color: "rgba(255,255,255,0.85)" }}>{paperStreamState}</span>
          </div>
          <div>
            <StreamDot state={liveStreamState} />
            live: <span style={{ color: "rgba(255,255,255,0.85)" }}>{liveStreamState}</span>
          </div>
          <div style={{ opacity: 0.5, marginTop: 2 }}>
            sub: {exchange}/futures/{symbol}
          </div>
          <LiveStatusDebugSection />
        </div>
      )}
    </div>
  );
}

function LiveStatusDebugSection() {
  const [status, setStatus] = useState<{
    ws_health?: { is_healthy?: boolean; heartbeat_age_s?: number | null; reconnect_count_1h?: number };
    wal_lag?: number;
    killswitch_state?: { is_active?: boolean };
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval>;
    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/v1/live/status");
        if (!res.ok) return;
        const body = await res.json();
        if (!cancelled) setStatus(body?.data ?? null);
      } catch {
        // swallow — debug panel must not throw
      }
    };
    fetchStatus();
    timer = setInterval(fetchStatus, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const ws = status?.ws_health;
  const ks = status?.killswitch_state;
  const walLag = status?.wal_lag ?? 0;
  const wsColor = !ws?.is_healthy
    ? "rgba(255,80,80,0.95)"
    : (ws?.heartbeat_age_s ?? 0) > 2
      ? "rgba(255,200,80,0.95)"
      : "rgba(120,220,120,0.95)";
  const walColor = walLag > 5 ? "rgba(255,80,80,0.95)" : walLag > 0 ? "rgba(255,200,80,0.95)" : "rgba(120,220,120,0.95)";
  const ksColor = ks?.is_active ? "rgba(255,80,80,0.95)" : "rgba(120,220,120,0.95)";

  return (
    <div style={{ marginTop: 4, paddingTop: 4, borderTop: "1px solid rgba(255,255,255,0.06)" }}>
      <div style={{ color: "rgba(255,255,255,0.5)", fontSize: 9, marginBottom: 2 }}>— live status (Phase 5) —</div>
      <div>
        ws:{" "}
        <span style={{ color: wsColor }}>
          {ws?.is_healthy ? "OK" : "BAD"} · {ws?.heartbeat_age_s?.toFixed(1) ?? "—"}s · rc {ws?.reconnect_count_1h ?? 0}/h
        </span>
      </div>
      <div>
        wal: <span style={{ color: walColor }}>{walLag}</span>
      </div>
      <div>
        ks: <span style={{ color: ksColor }}>{ks?.is_active ? "ON" : "OFF"}</span>
      </div>
    </div>
  );
}
