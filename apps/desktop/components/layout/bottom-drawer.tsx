"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useContextStore } from "@/lib/store/context";
import { usePaperStore } from "@/lib/store/paper";
import { useLiveTradingStore } from "@/lib/store/live-trading";
import { useTradingModeStore } from "@/lib/store/trading-mode";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { clickable } from "@/lib/a11y/clickable";
import {
  useIntelligenceWs,
  type IntelligenceMessage,
} from "@/lib/ws/use-intelligence-ws";
import type {
  PaperPositionData,
  PaperOrderData,
  PaperFillData,
} from "@/lib/types/paper";
import type {
  TradingPosition,
  TradingOrder,
  TradingFill,
} from "@/lib/types/live-trading";
import { useShallow } from "zustand/react/shallow";
import { api, apiPost, apiDelete } from "@/lib/api-client";
import { toastSuccess, toastError, toastInfo, useToastContainer } from "@/lib/toast";
import { useResize } from "@/lib/hooks/use-resize";
import { useAnomalyProposals } from "@/lib/hooks/use-anomaly-proposals";
import { CmdkPanel } from "./cmdk-panel";
import { TerminalPanel } from "./terminal-panel";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

interface TabDef {
  id: string;
  icon: string;
  label: string;
  badge?: string;
  badgeKind?: "default" | "warn" | "live";
}

const TABS: { group: string; tabs: TabDef[] }[] = [
  {
    group: "交易",
    tabs: [
      { id: "positions", icon: "⊞", label: "持仓" },
      { id: "events", icon: "⚡", label: "事件流" },
    ],
  },
  {
    group: "运维",
    tabs: [
      { id: "log", icon: "≡", label: "日志" },
      { id: "terminal", icon: "›_", label: "终端" },
    ],
  },
];

interface PosRow {
  id?: string;
  market: "futures" | "spot" | "stock_a" | "stock_us";
  sym: string;
  side: "买" | "卖" | "多" | "空";
  qty: string;
  price: string;
  tif?: string;
  status?: string;
  pnl?: string;
  cls: "pos" | "neg";
}

// --- adapt functions (from pos-page.tsx) ---
// 接收 PaperPositionData | TradingPosition union,paper/live 共用
function adaptPosition(p: PaperPositionData | TradingPosition): PosRow {
  const pnl = Number(p.unrealized_pnl || 0);
  const qty = Number(p.quantity_base || (p as { quantity?: number }).quantity || 0);
  return {
    market: "futures",
    sym: p.symbol || "?",
    side: p.side === "buy" ? "多" : "空",
    qty: String(qty),
    price: String(p.avg_entry_price ?? 0),
    pnl: (pnl >= 0 ? "+" : "") + pnl.toFixed(1),
    cls: pnl >= 0 ? "pos" : "neg",
  };
}

function adaptPendingOrder(o: PaperOrderData | TradingOrder): PosRow {
  const qty = Number(o.quantity || 0);
  return {
    id: String((o as { id?: string }).id ?? ""),
    market: "futures",
    sym: o.symbol || "?",
    side: o.side === "buy" ? "买" : "卖",
    qty: String(qty),
    price: o.price != null ? String(o.price) : ((o as { avg_fill_price?: number }).avg_fill_price != null ? String((o as { avg_fill_price?: number }).avg_fill_price) : "—"),
    tif: (o.type || "limit").toUpperCase().slice(0, 2),
    status: o.status || "accepted",
    cls: "pos",
  };
}

function adaptClosedOrder(o: PaperFillData | TradingFill): PosRow {
  const qty = Number(o.quantity_base || (o as { quantity?: number }).quantity || 0);
  const price = Number(o.price ?? 0);
  const pnl = Number((o as { realized_pnl?: number }).realized_pnl ?? 0);
  return {
    market: "futures",
    sym: o.symbol || "?",
    side: o.side === "buy" ? "多" : "空",
    qty: String(qty),
    price: String(price),
    pnl: (pnl >= 0 ? "+" : "") + pnl.toFixed(1),
    cls: pnl >= 0 ? "pos" : "neg",
  };
}

const MARKET_TABS = [
  { id: "all", label: "全部" },
  { id: "futures", label: "合约" },
  { id: "spot", label: "现货" },
  { id: "stock_a", label: "A股" },
  { id: "stock_us", label: "美股" },
];

const ACTIONBAR = [
  { id: "closeLong", icon: "⇓", label: "平多", kind: "danger", confirm: true },
  { id: "closeShort", icon: "⇑", label: "平空", kind: "danger", confirm: true },
  { id: "closeAll", icon: "✕", label: "全平 KILL", kind: "danger", confirm: true },
  { id: "reduce50", icon: "½", label: "Reduce 50%", kind: "danger", confirm: true },
  { id: "cancelAll", icon: "⊘", label: "撤单", kind: "gold", confirm: true },
  { id: "hedge", icon: "⇄", label: "对冲", kind: "gold", confirm: true },
  { id: "reverse", icon: "↻", label: "反手", kind: "gold", confirm: true },
  { id: "multiChart", icon: "⌗", label: "多图监控", kind: "gold", confirm: false },
  { id: "resetPaper", icon: "⟲", label: "清零 paper", kind: "danger", confirm: true },
] as const;

interface EventItem {
  time: string;
  kind: string;
  text: string;
  level: "normal" | "warn" | "danger";
  meta?: string;
  flash?: boolean;
}

// GET /research/tg-factors/regime — per-symbol TG volatility-regime coverage
interface RegimeRow {
  symbol: string;
  full_symbol: string;
  whitelisted_types: string[];
  regime_mult: number;
  window_after_hours: number;
  active: boolean;
  active_windows: { event_ts: number; end_ts: number; event_type: string; multiplier: number }[];
  now_ms: number;
}

const EVENT_SUBTABS = [
  { id: "all", label: "全部" },
  { id: "signal", label: "信号" },
  { id: "alert", label: "告警" },
  { id: "liq", label: "爆仓" },
  { id: "news", label: "新闻" },
] as const;

function mkLabel(mk: PosRow["market"]) {
  const map = { futures: "合约", spot: "现货", stock_a: "A股", stock_us: "美股" } as const;
  return <span className={`mk-tag mk-${mk}`}>{map[mk]}</span>;
}

export function BottomDrawer() {
  const [active, setActive] = useState("positions");
  // folded 持久化到 localStorage（同 trading-mode/settings 先例）：刷新后保留上次折叠态
  const [folded, setFolded] = useState(() => {
    if (typeof window === "undefined") return true;
    try {
      return window.localStorage.getItem("selenespace-drawer-folded") !== "0";
    } catch {
      return true;
    }
  });
  const [maxed, setMaxed] = useState(false);
  const [postab, setPostab] = useState<"pending" | "open" | "closed">("open");
  const [marketFilter, setMarketFilter] = useState("all");
  const [eventSub, setEventSub] = useState<string>("all");
  // P1-6 事件流跟单按钮
  const [followed, setFollowed] = useState<Set<number>>(new Set());
  const toggleFollow = (i: number) => {
    setFollowed((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };
  const [ctxMenu, setCtxMenu] = useState<{ sym: string; x: number; y: number } | null>(null);
  // P0b 改单 inline 表单 state（P0a 先接线，P0b 实现 inline UI）
  const [amendTarget, setAmendTarget] = useState<{ id: string; price: string } | null>(null);
  // P0-3 两步确认：5 个危险按钮首次 click 进 confirming 态（3s 倒计时 + 橙色 pulse），第二次 click 才真执行
  const [confirming, setConfirming] = useState<Record<string, number>>({});
  // P0-2 hedge/reverse 走 radix Dialog 确认（内置 Escape + focus trap a11y）
  const [hedgeReverseOpen, setHedgeReverseOpen] = useState<"hedge" | "reverse" | null>(null);
  const { setSymBase } = useContextStore();
  const { toasts } = useToastContainer();
  // 抽屉顶边拖拽：写 --drawer-h 到 :root（maxed 时不响应拖拽）
  const drawerResize = useResize({ target: "drawer-h", axis: "y", min: 30, max: 600, default: 240, invert: true });

  // AgentFab 定位依赖 drawer 实际高度（folded 30px / maxed 85vh 与 --drawer-h 变量脱钩），广播状态给 CSS
  useEffect(() => {
    document.documentElement.dataset.drawer = folded ? "folded" : maxed ? "maxed" : "open";
  }, [folded, maxed]);

  // folded 变化落盘（首帧跳过——初值即持久值，无需回写）
  const foldedFirstRef = useRef(true);
  useEffect(() => {
    if (foldedFirstRef.current) {
      foldedFirstRef.current = false;
      return;
    }
    try {
      window.localStorage.setItem("selenespace-drawer-folded", folded ? "1" : "0");
    } catch {
      // ignore storage error
    }
  }, [folded]);

  // --- 真实数据源 (按 mode 切 paper/live store) ---
  const mode = useTradingModeStore((s) => s.mode);
  const accountId = useAccountId();
  const positions = usePaperStore(useShallow((s) => s.positions));
  const livePositions = useLiveTradingStore(useShallow((s) => s.positions));
  const orders = usePaperStore(useShallow((s) => s.orders));
  const liveOrders = useLiveTradingStore(useShallow((s) => s.orders));
  const fills = usePaperStore(useShallow((s) => s.fills));
  const liveFills = useLiveTradingStore(useShallow((s) => s.fills));

  // mode === "live" 时用 live store,否则 paper store
  const realPositions = mode === "live" ? livePositions : positions;
  const realOrders = mode === "live" ? liveOrders : orders;
  const realFills = mode === "live" ? liveFills : fills;

  const PENDING = useMemo<PosRow[]>(() => {
    if (!realOrders || realOrders.length === 0) return [];
    return realOrders
      .filter((o) => o.status && ["working", "pending", "new", "partial", "active", "accepted"].includes(o.status))
      .map(adaptPendingOrder);
  }, [realOrders]);

  const OPEN_POS = useMemo<PosRow[]>(() => {
    if (!realPositions || realPositions.length === 0) return [];
    return realPositions.map(adaptPosition);
  }, [realPositions]);

  const CLOSED = useMemo<PosRow[]>(() => {
    if (!realFills || realFills.length === 0) return [];
    return realFills.map(adaptClosedOrder);
  }, [realFills]);

  // --- TG 信号事件流（intel:events WS → 事件流 tab 的 信号/告警）---
  // 后端 tg_signal_poller 轮询 macro_events.db 的 tg_events，推 tg_signal 事件。
  // payload: { event_type, alert_level, affected, half_life_h, magnitude, urgency, message }
  const [events, setEvents] = useState<EventItem[]>([]);
  const [regime, setRegime] = useState<RegimeRow[] | null>(null);
  const MAX_EVENTS = 200;

  // A股异动 suggest 档待批提案(轮询 /anomaly/proposals)。approve 只落人工确认,
  // 不下单——面板里明写这一句,免得操作员以为点一下就成交。
  const {
    proposals: anomalyProposals,
    mode: anomalyMode,
    wired: anomalyWired,
    deciding: anomalyDeciding,
    error: anomalyLoadError,
    decideError: anomalyDecideError,
    decide: decideAnomaly,
  } = useAnomalyProposals();

  const fmtTime = (tsMs: number) => {
    try {
      return new Date(tsMs).toTimeString().slice(0, 8);
    } catch {
      return "—";
    }
  };

  // EventGuard 事件类型 → 事件流 sub-tab（巨鲸算"信号"，安全/监管/地缘算"告警"）
  const tgKindOf = (eventType: string) => (eventType === "WHALE" ? "信号" : "告警");
  // 行背景分级：RED 最高危；巨鲸默认轻度（无色）；其余 YELLOW/ORANGE 走 warn
  // alert_level 落库是枚举小写值（models.py AlertLevel.value），要大小写不敏感比较
  const tgLevelOf = (eventType: string, level: string): EventItem["level"] => {
    if (level.toLowerCase() === "red") return "danger";
    if (eventType === "WHALE") return "normal";
    return "warn";
  };

  const onIntelMessage = (msg: IntelligenceMessage) => {
    if (msg.type !== "tg_signal") return;
    const d = msg.data ?? {};
    const et = String(d.event_type ?? "TG");
    const affected = String(d.affected ?? "");
    const halfLife = d.half_life_h != null ? Number(d.half_life_h) : null;
    const mag = d.magnitude != null ? Number(d.magnitude) : null;
    const urg = d.urgency != null ? Number(d.urgency) : null;
    const meta = [
      et,
      affected && affected !== "*" ? affected : null,
      halfLife != null ? `τ½=${halfLife}h` : null,
      mag != null && mag > 0 ? `M${mag.toFixed(2)}` : null,
      urg != null && urg > 0 ? `U${urg.toFixed(2)}` : null,
    ]
      .filter((s): s is string => !!s)
      .join(" · ");
    const item: EventItem = {
      time: fmtTime(d.timestamp != null ? Number(d.timestamp) : Date.now()),
      kind: tgKindOf(et),
      text: String(d.message ?? ""),
      level: tgLevelOf(et, String(d.alert_level ?? "")),
      meta: meta || undefined,
      flash: true,
    };
    setEvents((prev) => [item, ...prev].slice(0, MAX_EVENTS));
    // 单次 pulse 高亮后清除 flash（勿常驻闪烁）
    window.setTimeout(() => {
      setEvents((prev) =>
        prev.some((e) => e.flash) ? prev.map((e) => (e.flash ? { ...e, flash: false } : e)) : prev,
      );
    }, 400);
  };

  const intelWs = useIntelligenceWs({
    channels: ["events"],
    onMessage: onIntelMessage,
    enabled: true,
  });

  // TG 波动 regime 48h 减仓状态轮询（只读 GET /research/tg-factors/regime）
  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        const data = await api<RegimeRow[]>("/api/v1/research/tg-factors/regime");
        if (!disposed) setRegime(data ?? []);
      } catch {
        if (!disposed) setRegime([]);
      }
    };
    load();
    const id = window.setInterval(load, 60_000);
    return () => {
      disposed = true;
      window.clearInterval(id);
    };
  }, []);
  const wsState = intelWs.state.streamState;
  const wsLive = wsState === "live";
  const wsText =
    wsLive ? "intel:events live"
    : wsState === "degraded" ? "intel:events 信号中断"
    : "intel:events 连接中…";

  useEffect(() => {
    const ids = Object.keys(confirming);
    if (ids.length === 0) return;
    const id = setTimeout(() => setConfirming({}), 3000);
    return () => clearTimeout(id);
  }, [confirming]);

  const selectSymbol = (sym: string) => {
    const bare = sym.replace(/[-/].*$/, "");
    // setSymBase 同时同步 symBase 与 pageFilter.symbol,避免两个渲染源分叉
    setSymBase(bare);
  };

  const setMainChart = (sym: string) => {
    selectSymbol(sym);
    setCtxMenu(null);
    toastInfo(`主图切换为 ${sym}`);
  };

  const onContextMenu = (e: React.MouseEvent, sym: string) => {
    e.preventDefault();
    setCtxMenu({ sym, x: e.clientX, y: e.clientY });
  };

  useEffect(() => {
    if (!ctxMenu) return;
    const close = () => setCtxMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [ctxMenu]);

  const closePosition = async (side: "long" | "short" | "all", percent: number | null = null) => {
    const symbol = `${OPEN_POS[0]?.sym ?? "BTC-PERP"}`;
    const bare = symbol.replace(/-PERP$/, "-USDT-SWAP");
    try {
      const body: Record<string, unknown> = {
        symbol: bare,
        account_id: accountId,
      };
      if (percent !== null) {
        const target = OPEN_POS.find((p) => p.sym === symbol);
        const qty = target ? parseFloat(target.qty) : 0;
        body.amount = (qty * percent) / 100;
      }
      if (side === "long") {
        const target = OPEN_POS.find((p) => p.side === "多");
        if (target) body.symbol = target.sym.replace(/-PERP$/, "-USDT-SWAP");
      } else if (side === "short") {
        const target = OPEN_POS.find((p) => p.side === "空");
        if (target) body.symbol = target.sym.replace(/-PERP$/, "-USDT-SWAP");
      }
      await apiPost("/api/v1/trading/positions/close", body);
      toastSuccess(`平仓请求已发送 (${side})`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastError(`平仓失败: ${msg}`);
    }
  };

  const cancelAll = async () => {
    try {
      await apiDelete(`/api/v1/trading/orders?account_id=${encodeURIComponent(accountId)}`);
      toastSuccess("全部挂单已撤");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastError(`撤单失败: ${msg}`);
    }
  };

  // 单撤某挂单（L547 挂单行"撤单"按钮用）
  const cancelOrder = async (orderId: string) => {
    try {
      await apiDelete(`/api/v1/trading/orders/${encodeURIComponent(orderId)}?account_id=${encodeURIComponent(accountId)}`);
      toastSuccess(`订单 ${orderId} 已撤`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastError(`撤单失败: ${msg}`);
    }
  };

  const hedgeOrReverse = async (mode: "hedge" | "reverse") => {
    const target = OPEN_POS[0];
    if (!target) {
      toastError("无持仓可操作");
      return;
    }
    const sym = target.sym.replace(/-PERP$/, "-USDT-SWAP");
    try {
      await apiPost("/api/v1/trading/positions/close", {
        symbol: sym,
        account_id: accountId,
      });
      if (mode === "reverse") {
        const oppositeSide = target.side === "多" ? "sell" : "buy";
        await apiPost("/api/v1/trading/orders", {
          symbol: sym,
          side: oppositeSide,
          order_type: "market",
          quantity: parseFloat(target.qty),
          leverage: 3,
          account_id: accountId,
        });
      }
      toastSuccess(mode === "hedge" ? "对冲请求已发送" : "反手请求已发送");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastError(`${mode === "hedge" ? "对冲" : "反手"}失败: ${msg}`);
    }
  };

  const runAction = (id: string, needConfirm?: boolean) => {
    // P0-2 hedge/reverse 走 radix Dialog 确认（Escape + focus trap 内置）
    if (id === "hedge" || id === "reverse") {
      setHedgeReverseOpen(id);
      return;
    }
    // P0-3 两步确认：第一次 click 进 confirming 态，第二次 click 才执行
    if (needConfirm) {
      if (!confirming[id]) {
        // 首次：进 confirming 态
        setConfirming({ [id]: Date.now() });
        toastInfo(`${ACTIONBAR.find((b) => b.id === id)?.label} · 3s 内再次点击确认`);
        return;
      }
      // 第二次：清 confirming + 执行
      setConfirming({});
    }
    switch (id) {
      case "closeLong":
        return closePosition("long");
      case "closeShort":
        return closePosition("short");
      case "closeAll":
        return closePosition("all");
      case "reduce50":
        return closePosition("all", 50);
      case "cancelAll":
        return cancelAll();
      case "hedge":
        return hedgeOrReverse("hedge");
      case "reverse":
        return hedgeOrReverse("reverse");
      case "multiChart":
        return toastInfo("多图监控暂未实现");
      case "resetPaper":
        return resetPaperAccount();
    }
  };

  const resetPaperAccount = async () => {
    if (mode !== "paper") {
      toastError("仅 paper 模式可清零");
      return;
    }
    try {
      await apiPost(`/api/v1/paper/accounts/${encodeURIComponent(accountId)}/reset`, {});
      toastSuccess(`paper 账户 ${accountId} 已清零`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastError(`清零失败: ${msg}`);
    }
  };

  const filterByMarket = (arr: PosRow[]) =>
    marketFilter === "all" ? arr : arr.filter((r) => r.market === marketFilter);

  const pendingF = filterByMarket(PENDING);
  const openF = filterByMarket(OPEN_POS);
  const closedF = filterByMarket(CLOSED);
  const badgeStr = (f: PosRow[], total: PosRow[]) =>
    f.length === total.length ? `${total.length}` : `${f.length}/${total.length}`;

  const filteredEvents =
    eventSub === "all"
      ? events
      : events.filter((e) => {
          if (eventSub === "signal") return e.kind === "信号";
          if (eventSub === "alert") return e.kind === "告警";
          if (eventSub === "liq") return e.kind === "爆仓";
          if (eventSub === "news") return e.kind === "新闻";
          return true;
        });

  return (
    <div className={`drawer ${folded ? "folded" : ""} ${maxed ? "maxed" : ""}`}>
      <div
        className={`drawer-resizer ${maxed ? "maxed" : ""}`}
        onMouseDown={maxed ? undefined : drawerResize.onMouseDown}
        onDoubleClick={drawerResize.onDoubleClick}
        title="拖拽调整抽屉高度 · 双击重置"
      />
      <div className="drawer-tabs">
        {TABS.map((g) => (
          <div key={g.group} className="flex items-stretch">
            <span
              style={{
                fontSize: 7,
                color: "var(--color-ink-4)",
                padding: "0 6px",
                display: "flex",
                alignItems: "center",
                letterSpacing: 0.5,
              }}
            >
              {g.group}
            </span>
            <div className="tab-group">
              {g.tabs.map((t) => {
                // 待批异动挂到「事件流」tab:抽屉默认折叠(仅 30px 高),
                // 不挂 badge 的话提案要等人碰巧展开抽屉才看得见。
                const tab: TabDef =
                  t.id === "events" && anomalyWired && anomalyProposals.length > 0
                    ? { ...t, badge: String(anomalyProposals.length), badgeKind: "warn" }
                    : t;
                return (
                <div
                  key={tab.id}
                  className={`dtab ${active === tab.id ? "active" : ""}`}
                  aria-pressed={active === tab.id}
                  {...clickable(() => {
                    setActive(tab.id);
                    setFolded(false);
                  })}
                >
                  <span className="dtab-icon">{tab.icon}</span>
                  {tab.label}
                  {tab.badge && (
                    <span className={`dtab-badge ${tab.badgeKind ?? ""}`}>{tab.badge}</span>
                  )}
                </div>
                );
              })}
            </div>
            <div className="tab-group-sep" />
          </div>
        ))}
        <div className="drawer-actions">
          <div className="da-btn max" title="最大化" {...clickable(() => setMaxed((v) => !v))}>
            <svg width="12" height="12" viewBox="0 0 12 12">
              <rect x="1" y="1" width="10" height="10" fill="none" stroke="currentColor" />
              <rect x="3.5" y="3.5" width="5" height="5" fill="currentColor" opacity="0.6" />
            </svg>
          </div>
          <div
            className="da-btn fold"
            title="折叠 (ESC)"
            aria-expanded={!folded}
            {...clickable(() => setFolded((v) => !v))}
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path
                d={folded ? "M2 8 L6 4 L10 8" : "M2 4 L6 8 L10 4"}
                stroke="currentColor"
                strokeWidth="1.5"
                fill="none"
              />
            </svg>
          </div>
        </div>
      </div>
      <div className="drawer-body">
        {active === "positions" && (
          <div className="trade-panel">
            <div className="pos-actionbar">
              {ACTIONBAR.filter((b) => b.id !== "resetPaper" || mode === "paper").map((b) => {
                const isConfirming = !!confirming[b.id];
                return (
                  <div
                    key={b.id}
                    className={`qb-btn ${b.kind} ${isConfirming ? "confirming" : ""}`}
                    title={b.label}
                    onClick={() => runAction(b.id, b.confirm)}
                  >
                    <span className="qb-ico">{b.icon}</span>
                    <span className="qb-label">{isConfirming ? "确认?" : b.label}</span>
                  </div>
                );
              })}
            </div>
            <div className="pos-tabs">
              <div
                className={`pos-tab ${postab === "pending" ? "active" : ""}`}
                onClick={() => setPostab("pending")}
              >
                挂单 <span className="pos-tab-badge">{badgeStr(pendingF, PENDING)}</span>
              </div>
              <div
                className={`pos-tab ${postab === "open" ? "active" : ""}`}
                onClick={() => setPostab("open")}
              >
                持仓中 <span className="pos-tab-badge">{badgeStr(openF, OPEN_POS)}</span>
              </div>
              <div
                className={`pos-tab ${postab === "closed" ? "active" : ""}`}
                onClick={() => setPostab("closed")}
              >
                历史平仓 <span className="pos-tab-badge">{badgeStr(closedF, CLOSED)}</span>
              </div>
            </div>
            <div className="market-filter">
              {MARKET_TABS.map((m) => (
                <div
                  key={m.id}
                  className={`mf-chip ${marketFilter === m.id ? "active" : ""}`}
                  onClick={() => setMarketFilter(m.id)}
                >
                  {m.label}
                </div>
              ))}
            </div>
            <div className="pos-body">
              {postab === "pending" && (
                <div className="dtable">
                  <div
                    className="dt-row head"
                    style={{ gridTemplateColumns: "48px 1fr 40px 60px 80px 40px 70px 96px" }}
                  >
                    <span>市场</span>
                    <span>品种</span>
                    <span>方向</span>
                    <span className="num">数量</span>
                    <span className="num">价格</span>
                    <span>TIF</span>
                    <span>状态</span>
                    <span>操作</span>
                  </div>
                  {pendingF.length === 0 && (
                    <div className="dt-row empty">
                      <span style={{ gridColumn: "1/-1", color: "var(--color-ink-4)", textAlign: "center", padding: "12px" }}>
                        该市场暂无挂单
                      </span>
                    </div>
                  )}
                  {pendingF.map((r, i) => (
                    <div
                      key={i}
                      className="dt-row clickable"
                      style={{ gridTemplateColumns: "48px 1fr 40px 60px 80px 40px 70px 96px" }}
                    >
                      {mkLabel(r.market)}
                      <span className="sym">{r.sym}</span>
                      <span className={r.cls}>{r.side}</span>
                      <span className="num">{r.qty}</span>
                      <span className="num">{r.price}</span>
                      <span style={{ color: "var(--color-ink-4)" }}>{r.tif}</span>
                      <span style={{ color: "var(--color-gold)" }}>{r.status}</span>
                      <span>
                        <button
                          className="row-btn"
                          title="改单功能开发中"
                          disabled
                        >
                          改单
                        </button>
                        <button
                          className="row-btn danger"
                          onClick={() => r.id && cancelOrder(r.id)}
                        >
                          撤单
                        </button>
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {postab === "open" && (
                <div className="dtable">
                  <div
                    className="dt-row head"
                    style={{ gridTemplateColumns: "48px 1fr 40px 60px 80px 80px 70px" }}
                  >
                    <span>市场</span>
                    <span>品种</span>
                    <span>方向</span>
                    <span className="num">数量</span>
                    <span className="num">开仓价</span>
                    <span className="num">PnL</span>
                    <span>操作</span>
                  </div>
                  {openF.length === 0 && (
                    <div className="dt-row empty">
                      <span style={{ gridColumn: "1/-1", color: "var(--color-ink-4)", textAlign: "center", padding: "12px" }}>
                        该市场暂无持仓
                      </span>
                    </div>
                  )}
                  {openF.map((r, i) => (
                    <div
                      key={i}
                      className="dt-row clickable"
                      style={{ gridTemplateColumns: "48px 1fr 40px 60px 80px 80px 70px" }}
                      onClick={() => selectSymbol(r.sym)}
                      onContextMenu={(e) => onContextMenu(e, r.sym)}
                    >
                      {mkLabel(r.market)}
                      <span className="sym">{r.sym}</span>
                      <span className={r.cls}>{r.side}</span>
                      <span className="num">{r.qty}</span>
                      <span className="num">{r.price}</span>
                      <span className={`num ${r.cls}`}>{r.pnl}</span>
                      <span onClick={(e) => e.stopPropagation()}>
                        <button
                          className="row-btn"
                          title="持仓改单功能开发中"
                          disabled
                        >
                          改单
                        </button>
                        <button
                          className="row-btn danger"
                          onClick={() => closePosition("all")}
                        >
                          平仓
                        </button>
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {postab === "closed" && (
                <div className="dtable">
                  <div
                    className="dt-row head"
                    style={{ gridTemplateColumns: "48px 1fr 40px 60px 80px 80px" }}
                  >
                    <span>市场</span>
                    <span>品种</span>
                    <span>方向</span>
                    <span className="num">数量</span>
                    <span className="num">平仓价</span>
                    <span className="num">PnL</span>
                  </div>
                  {closedF.length === 0 && (
                    <div className="dt-row empty">
                      <span style={{ gridColumn: "1/-1", color: "var(--color-ink-4)", textAlign: "center", padding: "12px" }}>
                        该市场暂无历史
                      </span>
                    </div>
                  )}
                  {closedF.map((r, i) => (
                    <div
                      key={i}
                      className="dt-row"
                      style={{ gridTemplateColumns: "48px 1fr 40px 60px 80px 80px" }}
                    >
                      {mkLabel(r.market)}
                      <span className="sym">{r.sym}</span>
                      <span className={r.cls}>{r.side}</span>
                      <span className="num">{r.qty}</span>
                      <span className="num">{r.price}</span>
                      <span className={`num ${r.cls}`}>{r.pnl}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
        {active === "events" && (
          <div className="events-panel">
            <div className="flex items-center gap-2 px-2 py-1 border-b border-ink-2">
              {regime === null ? (
                <span className="text-ink-4 text-[11px]">regime 状态加载中…</span>
              ) : regime.some((r) => r.active) ? (
                regime.filter((r) => r.active).map((r) => (
                  <span
                    key={r.symbol}
                    className="text-[11px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800"
                    title={`${r.full_symbol} · 白名单 ${r.whitelisted_types.join("/")} · 窗口 ${r.active_windows.map((w) => w.event_type).join(",")}`}
                  >
                    {r.symbol} {r.window_after_hours}h减仓 {r.regime_mult}
                  </span>
                ))
              ) : (
                <span className="text-ink-4 text-[11px]">无 regime 窗口（TG 波动持续性 48h×0.85 未覆盖）</span>
              )}
            </div>
            {(anomalyWired || anomalyLoadError) && (
              <div className="anomaly-proposals">
                <div className="ap-head">
                  <span className="ap-title">
                    A股异动待批 <span className="ap-count">{anomalyProposals.length}</span>
                  </span>
                  <span className="ap-disclaimer">
                    批准仅记录人工确认,不下单
                  </span>
                </div>
                {/* 读挂 ≠ 无提案。后端挂掉时面板必须说话,否则和「档位关掉」长得一样。 */}
                {anomalyLoadError && (
                  <div className="ap-error">提案列表不可达:{anomalyLoadError}</div>
                )}
                {anomalyDecideError && (
                  <div className="ap-error">裁定失败:{anomalyDecideError}</div>
                )}
                {!anomalyLoadError && anomalyProposals.length === 0 && (
                  <div className="ap-empty">
                    {anomalyMode === "suggest" ? "暂无待批异动提案" : "异动决策档为 observe,不产生提案"}
                  </div>
                )}
                {anomalyProposals.map((p) => {
                  const busy = anomalyDeciding.has(p.dedup_id);
                  return (
                    <div key={p.dedup_id} className="ap-row" title={`dedup_id ${p.dedup_id}`}>
                      <span className="ap-sym">{p.symbol}</span>
                      <span className="ap-side">{p.side === "sell" ? "卖" : "买"}</span>
                      <span className="ap-qty">{p.quantity}</span>
                      <span className="ap-price">@{p.price}</span>
                      <span className="ap-kind" title={p.kind}>{p.kind}</span>
                      <span className="ap-actions">
                        <button
                          className="ap-btn approve"
                          disabled={busy}
                          onClick={() => decideAnomaly(p.dedup_id, true)}
                        >
                          {busy ? "…" : "批准"}
                        </button>
                        <button
                          className="ap-btn reject"
                          disabled={busy}
                          onClick={() => decideAnomaly(p.dedup_id, false)}
                        >
                          拒绝
                        </button>
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="event-subtabs">
              {EVENT_SUBTABS.map((s) => {
                const count = s.id === "all"
                  ? events.length
                  : events.filter((e) => {
                      if (s.id === "signal") return e.kind === "信号";
                      if (s.id === "alert") return e.kind === "告警";
                      if (s.id === "liq") return e.kind === "爆仓";
                      if (s.id === "news") return e.kind === "新闻";
                      return false;
                    }).length;
                return (
                  <div
                    key={s.id}
                    className={`esub-tab ${eventSub === s.id ? "active" : ""}`}
                    onClick={() => setEventSub(s.id)}
                  >
                    {s.label} <span className="esub-badge">{count}</span>
                  </div>
                );
              })}
            </div>
            <div className="event-list">
              {filteredEvents.length === 0 ? (
                <div className="text-center text-ink-4 text-[11px] py-3">
                  {wsLive
                    ? "暂无实时 TG 事件 · 需启动 tg_listen --macro-db 并配置 TG 频道"
                    : wsText}
                </div>
              ) : (
                filteredEvents.map((e, i) => (
                  <div
                    key={i}
                    className={`event-row ${e.level} ${e.flash ? "flash" : ""} ${followed.has(i) ? "followed" : ""}`}
                  >
                    <span className="ev-time">{e.time}</span>
                    <span className={`ev-kind kind-${e.kind}`}>{e.kind}</span>
                    <span className="ev-text">
                      <span className="ev-main" title={e.text}>
                        {e.text}
                      </span>
                      {e.meta && <span className="ev-meta">{e.meta}</span>}
                    </span>
                    <button
                      className={`ev-follow ${followed.has(i) ? "active" : ""}`}
                      onClick={() => toggleFollow(i)}
                      title={followed.has(i) ? "取消跟单" : "跟单"}
                    >
                      {followed.has(i) ? "★" : "☆"}
                    </button>
                  </div>
                ))
              )}
            </div>
            <div className="event-conn">
              <span className={`ev-conn-dot ${wsLive ? "live" : "down"}`} />
              <span className="ev-conn-text">{wsText}</span>
            </div>
          </div>
        )}
        {active === "log" && (
          <div className="log-panel">
            <div className="text-center text-ink-4 text-[11px] py-3">日志功能未规划</div>
          </div>
        )}
        {active === "terminal" && <TerminalPanel />}
      </div>
      <CmdkPanel />
      {ctxMenu && (
        <div
          className="ctx-menu"
          style={{
            position: "fixed",
            top: ctxMenu.y,
            left: ctxMenu.x,
            background: "var(--color-surface-1)",
            border: "1px solid var(--color-border-soft)",
            borderRadius: 4,
            boxShadow: "0 4px 16px rgba(0,0,0,0.5)",
            zIndex: 150,
            minWidth: 160,
            padding: 4,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {[
            { label: "设为主图", action: () => setMainChart(ctxMenu.sym) },
            { label: "添加到多图", action: () => { toastInfo(`已添加 ${ctxMenu.sym} 到多图`); setCtxMenu(null); } },
            { label: "移除多图", action: () => { toastInfo(`已移除 ${ctxMenu.sym}`); setCtxMenu(null); } },
            { label: "跳转盯盘", action: () => { setMainChart(ctxMenu.sym); setCtxMenu(null); } },
          ].map((item) => (
            <div
              key={item.label}
              onClick={item.action}
              style={{
                padding: "5px 10px",
                fontSize: 11,
                cursor: "pointer",
                borderRadius: 2,
                color: "var(--color-ink-2)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--color-surface-2)";
                e.currentTarget.style.color = "var(--color-gold)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = "var(--color-ink-2)";
              }}
            >
              {item.label}
            </div>
          ))}
        </div>
      )}
      <div className="fixed right-4 z-50 flex flex-col gap-1" style={{ bottom: "calc(28px + 16px)" }}>
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`rounded px-3 py-1.5 text-xs shadow-lg ${
              t.level === "success"
                ? "bg-pos/20 text-pos border border-pos/40"
                : t.level === "error"
                ? "bg-neg/20 text-neg border border-neg/40"
                : t.level === "warn"
                ? "bg-gold/20 text-gold border border-gold/40"
                : "bg-surface-2 text-ink border border-border-soft"
            }`}
          >
            {t.msg}
          </div>
        ))}
      </div>
      <Dialog
        open={hedgeReverseOpen !== null}
        onOpenChange={(v) => {
          if (!v) setHedgeReverseOpen(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {hedgeReverseOpen === "reverse" ? "确认反手" : "确认对冲"}
            </DialogTitle>
            <DialogDescription>
              {hedgeReverseOpen === "reverse"
                ? "反手将先平掉当前持仓，再以原数量的反向开仓。确认继续？"
                : "对冲将平掉当前持仓。确认继续？"}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setHedgeReverseOpen(null)}>
              取消
            </Button>
            <Button
              className="bg-amber-600 hover:bg-amber-700"
              onClick={() => {
                const mode = hedgeReverseOpen;
                setHedgeReverseOpen(null);
                if (mode === "hedge" || mode === "reverse") {
                  void hedgeOrReverse(mode);
                }
              }}
            >
              确认
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
