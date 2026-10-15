"use client";

import { useState, useEffect, useRef } from "react";
import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { apiPost, apiPut, apiDelete } from "@/lib/api-client";
import { toastSuccess, toastError, toastInfo } from "@/lib/toast";
import { useContextStore } from "@/lib/store/context";
import { usePaperStore } from "@/lib/store/paper";
import { useLiveTradingStore } from "@/lib/store/live-trading";
import { useTradingModeStore } from "@/lib/store/trading-mode";
import { useAccountId } from "@/lib/hooks/use-account-id";
import { useApi } from "@/lib/hooks/use-api";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useRiskAwareSubmit } from "@/lib/hooks/use-risk-aware-submit";
import { usePositionActions, normalizeSymbol } from "@/lib/hooks/use-position-actions";
import { RiskAlertDialog } from "@/components/trading/risk-alert-dialog";
import type { PaperOrderData, PaperPositionData } from "@/lib/types/paper";
import type { TradingOrder, TradingPosition } from "@/lib/types/live-trading";
import { useShallow } from "zustand/react/shallow";
import { TradeHistory } from "@/components/trading/trade-history";
import { StrategyStatus } from "@/components/trading/strategy-status";
import { WorkflowContextBanner } from "@/components/layout/workflow-context-banner";

// 设计稿 L7041 renderOrderTicket — 实盘下单面板 + 订单管理
type OrderType = "limit" | "market" | "stop" | "tp" | "post";
// TIF (Time-In-Force): 后端 trading.py:143 仅认 GTC/IOC/FOK。
// Post-Only 是独立 order_type(type="post" → L223 转成 limit),不属 TIF 下拉。
type Tif = "GTC" | "IOC" | "FOK";

const ORDER_TYPES: { id: OrderType; label: string; needPrice: boolean }[] = [
  { id: "limit", label: "限价", needPrice: true },
  { id: "market", label: "市价", needPrice: false },
  { id: "stop", label: "止损", needPrice: true },
  { id: "tp", label: "止盈", needPrice: true },
  { id: "post", label: "Post-Only", needPrice: true },
];
const TIF_OPTIONS: Tif[] = ["GTC", "IOC", "FOK"];
const LEVERAGE_STEPS = [1, 2, 3, 5, 10, 20];

interface WorkingOrder {
  id: string;
  sym: string;
  side: "买" | "卖";
  type: string;
  price: string;
  qty: string;
  filled: string;
  status: "working" | "partial" | "filled" | "canceled";
}

type BackendOrder = PaperOrderData | TradingOrder;

// 订单 tab 对应的状态分组(后端 status 值归一)
type OrderTab = "working" | "partial" | "filled" | "canceled";
const ORDER_TABS: { id: OrderTab; label: string }[] = [
  { id: "working", label: "挂单" },
  { id: "partial", label: "部分成交" },
  { id: "filled", label: "已成交" },
  { id: "canceled", label: "已撤" },
];

// 后端订单 status → 前端 tab 分组归一
function classifyOrderStatus(raw: string | undefined): OrderTab {
  const s = (raw ?? "").toLowerCase();
  if (s === "partial" || s === "partially_filled") return "partial";
  if (["filled", "closed", "done"].includes(s)) return "filled";
  if (["canceled", "cancelled", "expired", "rejected"].includes(s)) return "canceled";
  return "working"; // working / pending / new / open
}

// 后端 /api/v1/markets/{symbol}/orderbook 返回 shape
interface BackendObLevel { price?: number; quantity?: number; size?: number; amount?: number; }
interface BackendOrderbook { bids?: BackendObLevel[]; asks?: BackendObLevel[]; }

// 下单返回 shape（与 order-form 一致）
interface TrdOrderResult {
  has_risk_alerts?: boolean
  risk_alerts?: { level: string; rule_id?: string; message: string }[]
  order?: { id: string }
}

interface TrdSubmitParams {
  symbol: string
  side: "buy" | "sell"
  order_type: string
  quantity: number
  price?: number | null
  leverage: number
  reduce_only: boolean
  account_id: string
  time_in_force: Tif  // Bug 4: 透传 TIF 到后端 PlaceOrderRequest.time_in_force
}

// 按 mode 切 paper/live store 的订单数据源
// paper 模式：usePaperStore.orders（P0a 的 paper store 数据源保留不破坏）
// live 模式：useLiveTradingStore.orders（P1 新增实盘 store）
function useExecutionOrders(): TradingOrder[] {
  const mode = useTradingModeStore((s) => s.mode);
  const paperOrders = usePaperStore(useShallow((s) => s.orders));
  const liveOrders = useLiveTradingStore(useShallow((s) => s.orders));
  if (mode === "live") return liveOrders;
  return paperOrders;
}

function useExecutionStreamState(): "live" | "degraded" | "recovering" {
  const mode = useTradingModeStore((s) => s.mode);
  const paperState = usePaperStore((s) => s.streamState);
  const liveState = useLiveTradingStore((s) => s.streamState);
  return mode === "live" ? liveState : paperState;
}

// P5-1 按 mode 切 paper/live positions(和 useExecutionOrders 同模式)
function useExecutionPositions(): TradingPosition[] | PaperPositionData[] {
  const mode = useTradingModeStore((s) => s.mode);
  const paperPositions = usePaperStore(useShallow((s) => s.positions));
  const livePositions = useLiveTradingStore(useShallow((s) => s.positions));
  return mode === "live" ? livePositions : paperPositions;
}

// 订单簿子组件 — 独立轮询 + 限流 toast,重渲染范围收敛到本卡片。
// 不抽出来时 orderbook 每 2s 刷新会让整个 TrdPage 重渲染一次(下单面板/风险计算器/订单管理全跟着抖)。
// key 用 l.price ?? i:价格是同一水平位的稳定标识,避免列表重排时 React 复用错位。
interface OrderBookProps {
  symbol: string;  // 交易对基符号,如 "BTC" → /markets/BTC/USDT/orderbook
  onPickPrice: (price: string) => void;
}

function OrderBookCard({ symbol, onPickPrice }: OrderBookProps) {
  const { useApi: useApiStrict } = useApiPage({ distinguish: true });
  const obToastFired = useRef(false);
  // D-REST 决策:订单簿 2s 轮询;1008/429 限流时 onErrorRetry 关闭,UI 显示"—"
  const { data: obReal, error: obError } = useApiStrict<BackendOrderbook | null>(
    `/api/v1/markets/${encodeURIComponent(symbol)}/USDT/orderbook?depth=5`,
    { refreshInterval: 2000, onErrorRetry: false },
  );
  // 限流降级提示:429/1008 触发一次 toast,恢复时重置 ref
  useEffect(() => {
    if (obError && (obError.status === 429 || obError.status === 1008)) {
      if (!obToastFired.current) {
        toastError("行情降级(限流)");
        obToastFired.current = true;
      }
    } else {
      obToastFired.current = false;
    }
  }, [obError]);
  const obBids = obReal?.bids?.slice(0, 5) ?? [];
  const obAsks = obReal?.asks?.slice(0, 5) ?? [];
  const fmtObPx = (n: number | undefined) => (Number.isFinite(n as number) ? (n as number).toFixed(2) : "—");
  const fmtObSz = (n: number | undefined) =>
    Number.isFinite(n as number) ? (n as number).toFixed(4) : "—";

  return (
    <V17Card title="订单簿 (click 填价)">
      {obError && (obError.status === 429 || obError.status === 1008) && (
        <div className="bg-orange-600/90 text-white text-[10px] px-2 py-1 mb-1 rounded">
          行情降级(限流)
        </div>
      )}
      <div className="grid gap-1 text-[10px] font-mono" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div>
          <div className="text-[9px] text-ink-4 mb-1">BIDS</div>
          {obBids.length === 0 ? (
            <div className="text-[9px] text-ink-4 py-1 text-center">—</div>
          ) : (
            obBids.map((l, i) => {
              const p = Number(l.price ?? 0);
              return (
                <button
                  key={l.price ?? i}
                  onClick={() => p > 0 && onPickPrice(p.toFixed(2))}
                  className="flex w-full justify-between py-0.5 border-b border-border-soft text-pos hover:bg-surface-2"
                >
                  <span>{fmtObPx(l.price)}</span>
                  <span className="text-ink-4">{fmtObSz(l.quantity ?? l.size ?? l.amount)}</span>
                </button>
              );
            })
          )}
        </div>
        <div>
          <div className="text-[9px] text-ink-4 mb-1">ASKS</div>
          {obAsks.length === 0 ? (
            <div className="text-[9px] text-ink-4 py-1 text-center">—</div>
          ) : (
            obAsks.map((l, i) => {
              const p = Number(l.price ?? 0);
              return (
                <button
                  key={l.price ?? i}
                  onClick={() => p > 0 && onPickPrice(p.toFixed(2))}
                  className="flex w-full justify-between py-0.5 border-b border-border-soft text-neg hover:bg-surface-2"
                >
                  <span>{fmtObPx(l.price)}</span>
                  <span className="text-ink-4">{fmtObSz(l.quantity ?? l.size ?? l.amount)}</span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </V17Card>
  );
}

export function TrdPage() {
  const [type, setType] = useState<OrderType>("limit");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [price, setPrice] = useState("");
  const [amount, setAmount] = useState("0.05");
  const [leverage, setLeverage] = useState(3);
  const [tif, setTif] = useState<Tif>("GTC");
  const [reduceOnly, setReduceOnly] = useState(false);
  // P1-5 risk-at-1R 计算器（设计稿 L7100-7167 + L7237-7246）
  const [rMultiple, setRMultiple] = useState(1);      // R 倍数 1/2/3/5
  const [slPct, setSlPct] = useState(2);              // SL 距离 %
  const [riskPct, setRiskPct] = useState(1);          // 账户风险 %
  // P0b inline 改单表单
  const [amendTarget, setAmendTarget] = useState<{ id: string; price: string } | null>(null);
  // P1-2 订单簿 click 填价
  // Bug 1: 卸载时清理两步确认 timer,防 setState on unmounted
  useEffect(() => {
    return () => {
      if (posConfirmTimer.current) clearTimeout(posConfirmTimer.current);
    };
  }, []);
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<number>;
      if (typeof ce.detail !== "number") return;
      const formatted = ce.detail.toLocaleString("en-US", { maximumFractionDigits: 2 });
      setPrice(formatted);
      if (type !== "limit") setType("limit");
    };
    window.addEventListener("trade-fill-price", handler as EventListener);
    return () => window.removeEventListener("trade-fill-price", handler as EventListener);
  }, [type]);
  const { pageFilter, tick } = useContextStore();
  const accountId = useAccountId();
  const symbol = pageFilter.symbol ? `${pageFilter.symbol}-USDT-SWAP` : "BTC-USDT-SWAP";
  const ordersKey = `/api/v1/trading/orders?account_id=${encodeURIComponent(accountId)}`;
  const { mutate } = useSWR(ordersKey);

  const { submit: submitRisk, riskAlerts, showRiskDialog, confirm, cancel, submitting } =
    useRiskAwareSubmit<TrdSubmitParams, TrdOrderResult>({
      url: "/api/v1/trading/orders",
      onSuccess: async () => {
        toastSuccess(`${side === "buy" ? "买入" : "卖出"} ${amount} ${symbol} 已提交`);
        await mutate();
        setAmount("");
      },
      onError: (err) => {
        toastError(`下单失败: ${err.message}`);
      },
    });

  const priceNum = parseFloat(price.replace(/,/g, "")) || 0;
  const amountNum = parseFloat(amount) || 0;
  const notional = priceNum * amountNum;
  const estFee = notional * 0.0004;
  const marginReq = notional / leverage;

  // P1-3 输入实时校验：amount > 0；非市价单 price > 0
  const amountInvalid = amount.trim() !== "" && (Number.isNaN(parseFloat(amount.replace(/,/g, ""))) || amountNum <= 0);
  const priceInvalid = type !== "market" && price.trim() !== "" && (Number.isNaN(parseFloat(price.replace(/,/g, ""))) || priceNum <= 0);
  const submitDisabled = submitting || amountInvalid || priceInvalid;

  // P1-5 risk-at-1R 计算（设计稿 _tsCalcRisk）
  const accountEquity = tick.equity;
  const riskAmount = accountEquity * (riskPct / 100) * rMultiple;  // R 倍数放大风险
  const slDistance = priceNum * (slPct / 100);
  // 建议下单量 = 风险金额 / SL 距离（每单位亏损）
  const suggestQty = slDistance > 0 ? riskAmount / slDistance : 0;
  const maxLoss = amountNum * slDistance;  // 当前输入数量的最大亏损

  const realOrders = useExecutionOrders();
  const streamState = useExecutionStreamState();
  // P5-1 当前持仓(按 mode 切 paper/live)
  const realPositions = useExecutionPositions();
  const currentPosition = (realPositions as Array<TradingPosition | PaperPositionData>).find(
    (p) => normalizeSymbol(p.symbol) === symbol
  ) ?? null;
  // P5-3 反手/平仓 hook
  const { closePosition, hedgeOrReverse } = usePositionActions({
    accountId,
    leverage,
    mutateOrders: mutate,
    // positions 是 zustand store(非 SWR key),传 undefined:依赖 WS 推送刷新;
    // 两步确认 3s + close 成功 toast 缓解 WS 延迟期误触
    mutatePositions: undefined,
  });
  // P5-3 两步确认状态:第一次点击存时间戳,3s 内再次点击才执行;过期自动重置。
  // Bug 1: 原实现只查 truthy 不校验 3s 窗口,toast 说"3s 内确认"但永不过期。
  const POS_CONFIRM_WINDOW_MS = 3000;
  const [posConfirm, setPosConfirm] = useState<Record<string, number>>({});
  const posConfirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Bug 2: 订单管理 tab 选中态(原实现 i===0 永远高亮第一个,点击无效果)
  const [orderTab, setOrderTab] = useState<OrderTab>("working");
  const runPosAction = (id: "closeAll" | "hedge" | "reverse") => {
    const firstClickAt = posConfirm[id];
    const withinWindow = firstClickAt != null && Date.now() - firstClickAt < POS_CONFIRM_WINDOW_MS;
    if (!withinWindow) {
      // 第一次点击(或窗口已过期):开始 3s 确认窗口
      if (posConfirmTimer.current) clearTimeout(posConfirmTimer.current);
      setPosConfirm({ [id]: Date.now() });
      posConfirmTimer.current = setTimeout(() => {
        setPosConfirm({});
        posConfirmTimer.current = null;
      }, POS_CONFIRM_WINDOW_MS);
      toastInfo(`${id === "closeAll" ? "全平" : id === "hedge" ? "对冲" : "反手"} · 3s 内再次点击确认`);
      return;
    }
    // 第二次点击且在窗口内:确认执行,清理 timer
    if (posConfirmTimer.current) {
      clearTimeout(posConfirmTimer.current);
      posConfirmTimer.current = null;
    }
    setPosConfirm({});
    if (id === "closeAll") return closePosition("all", currentPosition);
    if (id === "hedge") return hedgeOrReverse("hedge", currentPosition);
    if (id === "reverse") return hedgeOrReverse("reverse", currentPosition);
  };
  // 全量订单(含 working/partial/filled/canceled),供 tab 过滤 + 执行统计
  const allOrders: WorkingOrder[] = (realOrders && realOrders.length > 0)
    ? (realOrders as BackendOrder[])
        .map((o) => {
          const qty = Number(o.quantity_base ?? o.quantity ?? 0);
          const filled = Number(o.filled_quantity ?? 0);
          const filledPct = qty > 0 ? (filled / qty) * 100 : 0;
          const status = classifyOrderStatus(o.status);
          return {
            id: String(o.order_id ?? o.id ?? "?"),
            sym: o.symbol || "?",
            side: o.side === "buy" ? "买" : "卖",
            type: o.type === "limit" ? "限价" : o.type === "market" ? "市价" : (o.type ?? "限价"),
            price: String(o.price ?? 0),
            qty: String(qty),
            filled: `${filledPct.toFixed(0)}%`,
            status,
          };
        })
    : [];
  // Bug 2: 按 tab 过滤(原 workingOrders 只显示 working/partial,且 tab 死按钮)
  const visibleOrders = allOrders.filter((o) => o.status === orderTab);

  // Bug 3: 执行统计从 realOrders 派生(原全硬编码 "—")
  const rawOrders = (realOrders ?? []) as BackendOrder[];
  const startOfToday = (() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  })();
  const todayCount = rawOrders.filter((o) => {
    const ts = o.created_at ?? 0;
    const ms = ts > 1e12 ? ts : ts * 1000;  // 秒/毫秒归一
    return ms >= startOfToday;
  }).length;
  const filledCount = rawOrders.filter((o) => classifyOrderStatus(o.status) === "filled").length;
  const canceledCount = rawOrders.filter((o) => classifyOrderStatus(o.status) === "canceled").length;
  const cancelRate = rawOrders.length > 0 ? (canceledCount / rawOrders.length) * 100 : 0;
  // 滑点:有 avg_fill_price 且 limit 价的订单,(fill-price)/price 平均值
  const slippageSamples = rawOrders
    .map((o) => {
      const fp = Number(o.avg_fill_price ?? 0);
      const op = Number(o.price ?? 0);
      return op > 0 && fp > 0 ? ((fp - op) / op) * 100 : null;
    })
    .filter((v): v is number => v != null);
  const avgSlippage = slippageSamples.length > 0
    ? slippageSamples.reduce((s, v) => s + v, 0) / slippageSamples.length
    : null;

  const submitOrder = async () => {
    if (submitting) return;
    if (amountNum <= 0) {
      toastError("数量必须大于 0");
      return;
    }
    if (type !== "market" && priceNum <= 0) {
      toastError("限价单价格必须大于 0");
      return;
    }
    const orderType = type === "post" ? "limit" : type;
    await submitRisk({
      symbol,
      side,
      order_type: orderType,
      quantity: amountNum,
      price: type === "market" ? null : priceNum,
      leverage,
      reduce_only: reduceOnly,
      account_id: accountId,
      time_in_force: tif,  // Bug 4: 透传到后端 PlaceOrderRequest.time_in_force
    });
  };

  const cancelOrder = async (orderId: string) => {
    try {
      await apiDelete(`/api/v1/trading/orders/${orderId}?account_id=${encodeURIComponent(accountId)}`);
      toastSuccess(`订单 ${orderId} 已撤`);
      await mutate();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastError(`撤单失败: ${msg}`);
    }
  };

  const amendPrice = async (orderId: string, newPrice: string) => {
    const priceVal = parseFloat(newPrice);
    if (!Number.isFinite(priceVal) || priceVal <= 0) {
      toastError("价格无效");
      return;
    }
    try {
      await apiPut(`/api/v1/trading/orders/${orderId}`, {
        price: priceVal,
        account_id: accountId,
      });
      toastSuccess(`订单 ${orderId} 改价 ${priceVal}`);
      setAmendTarget(null);
      await mutate();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastError(`改单失败: ${msg}`);
    }
  };

  const symBase = pageFilter.symbol || "BTC";

  return (
    <div className="v17-page-grid grid gap-2">
      <WorkflowContextBanner />
      <div className="grid gap-2" style={{ gridTemplateColumns: "var(--rail-w) 1fr" }}>
        <V17Card title="下单面板">
          <div className="flex flex-col gap-2 text-[10px]">
            <div className="text-gold font-mono">{`${pageFilter.symbol || "BTC"}-PERP`}</div>
            <div className="flex items-center gap-1 text-[9px] text-ink-3">
              <span>当前持仓:</span>
              {currentPosition ? (() => {
                const qty = Number(currentPosition.quantity_base || currentPosition.quantity || 0);
                const pnl = Number(currentPosition.unrealized_pnl || 0);
                const sideLabel = currentPosition.side === "buy" ? "多" : "空";
                const pnlStr = (pnl >= 0 ? "+" : "") + pnl.toFixed(2);
                const pnlCls = pnl >= 0 ? "text-pos" : "text-neg";
                return (
                  <span className="font-mono text-[10px]">
                    <span className={currentPosition.side === "buy" ? "text-pos" : "text-neg"}>{sideLabel}</span>
                    {" "}{qty} @ {currentPosition.avg_entry_price ?? "—"}
                    {" "}
                    <span className={pnlCls}>{pnlStr}</span>
                  </span>
                );
              })() : <span className="text-ink-4">无持仓</span>}
            </div>
            <div className="flex gap-1">
              {ORDER_TYPES.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setType(t.id)}
                  className={`rounded px-1.5 py-0.5 ${type === t.id ? "bg-gold/20 text-gold" : "bg-surface-2 text-ink-3 hover:text-ink-2"}`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="flex gap-1">
              <button
                onClick={() => setSide("buy")}
                className={`flex-1 rounded py-1 ${side === "buy" ? "bg-pos/20 text-pos" : "bg-surface-2 text-ink-3"}`}
              >
                买入
              </button>
              <button
                onClick={() => setSide("sell")}
                className={`flex-1 rounded py-1 ${side === "sell" ? "bg-neg/20 text-neg" : "bg-surface-2 text-ink-3"}`}
              >
                卖出
              </button>
            </div>
            {type !== "market" && (
              <label className="flex flex-col gap-0.5">
                <div className="flex items-center justify-between">
                  <span className="text-ink-3">价格</span>
                  <input
                    type="text"
                    inputMode="decimal"
                    value={price}
                    onChange={(e) => setPrice(e.target.value)}
                    className={`w-32 rounded border bg-surface-2 px-1.5 py-0.5 font-mono text-right text-ink ${priceInvalid ? "border-red-500" : "border-border-soft"}`}
                  />
                </div>
                {priceInvalid && (
                  <span className="text-[9px] text-red-500">价格必须 &gt; 0</span>
                )}
              </label>
            )}
            <label className="flex flex-col gap-0.5">
              <div className="flex items-center justify-between">
                <span className="text-ink-3">数量</span>
                <input
                  type="text"
                  inputMode="decimal"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  className={`w-32 rounded border bg-surface-2 px-1.5 py-0.5 font-mono text-right text-ink ${amountInvalid ? "border-red-500" : "border-border-soft"}`}
                />
              </div>
              {amountInvalid && (
                <span className="text-[9px] text-red-500">数量必须 &gt; 0</span>
              )}
            </label>
            <div className="flex gap-1">
              {[0, 25, 50, 75, 100].map((pct) => (
                <button
                  key={pct}
                  onClick={() => {
                    // P1-5: preset 基于建议下单量比例（设计稿 L7237-7246）
                    const v = (suggestQty * pct) / 100;
                    setAmount(v.toFixed(4));
                  }}
                  className="flex-1 rounded bg-surface-2 py-0.5 text-[9px] text-ink-3 hover:text-ink-2"
                >
                  {pct === 0 ? "0" : `${pct}%`}
                </button>
              ))}
            </div>
            <div className="flex items-center justify-between">
              <span className="text-ink-3">杠杆</span>
              <div className="flex gap-1">
                {LEVERAGE_STEPS.map((lv) => (
                  <button
                    key={lv}
                    onClick={() => setLeverage(lv)}
                    className={`rounded px-1.5 py-0.5 ${leverage === lv ? "bg-gold/20 text-gold" : "bg-surface-2 text-ink-3"}`}
                  >
                    {lv}x
                  </button>
                ))}
              </div>
            </div>
            <label className="flex items-center justify-between">
              <span className="text-ink-3">TIF</span>
              <select
                value={tif}
                onChange={(e) => setTif(e.target.value as Tif)}
                className="rounded border border-border-soft bg-surface-2 px-1.5 py-0.5 text-ink"
              >
                {TIF_OPTIONS.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={reduceOnly} onChange={(e) => setReduceOnly(e.target.checked)} />
              <span className="text-ink-3">Reduce Only</span>
            </label>
            <div className="border-t border-border-soft pt-2 flex flex-col gap-1 text-[9px]">
              <div className="flex justify-between">
                <span className="text-ink-4">订单金额</span>
                <span className="font-mono text-ink-2">${notional.toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-4">预估手续费</span>
                <span className="font-mono text-neg">${estFee.toFixed(4)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-4">所需保证金</span>
                <span className="font-mono text-gold">${marginReq.toFixed(2)}</span>
              </div>
            </div>
            {/* P1-5 Risk-at-1R 计算器（设计稿 L7100-7167） */}
            <div className="risk-1r rounded border border-gold/30 bg-gold/5 p-1.5 flex flex-col gap-1 text-[9px]">
              <div className="flex items-center justify-between text-gold">
                <span className="font-semibold">Risk-at-1R</span>
                <span className="font-mono">{rMultiple}R</span>
              </div>
              <label className="flex items-center justify-between">
                <span className="text-ink-3">R 倍数</span>
                <input
                  type="range"
                  min={1}
                  max={5}
                  step={1}
                  value={rMultiple}
                  onChange={(e) => setRMultiple(Number(e.target.value))}
                  className="w-24"
                />
              </label>
              <label className="flex items-center justify-between">
                <span className="text-ink-3">SL 距离</span>
                <span className="flex items-center gap-1">
                  <input
                    type="number"
                    min={0.1}
                    max={20}
                    step={0.1}
                    value={slPct}
                    onChange={(e) => setSlPct(Number(e.target.value))}
                    className="w-14 rounded border border-border-soft bg-surface-2 px-1 py-0.5 font-mono text-right text-ink"
                  />
                  <span className="text-ink-4">%</span>
                </span>
              </label>
              <label className="flex items-center justify-between">
                <span className="text-ink-3">账户风险</span>
                <span className="flex items-center gap-1">
                  <input
                    type="number"
                    min={0.1}
                    max={10}
                    step={0.1}
                    value={riskPct}
                    onChange={(e) => setRiskPct(Number(e.target.value))}
                    className="w-14 rounded border border-border-soft bg-surface-2 px-1 py-0.5 font-mono text-right text-ink"
                  />
                  <span className="text-ink-4">%</span>
                </span>
              </label>
              <div className="border-t border-border-soft pt-1 flex flex-col gap-0.5 font-mono">
                <div className="flex justify-between">
                  <span className="text-ink-4">账户余额</span>
                  <span className="text-ink-2">${accountEquity.toLocaleString()}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-4">风险金额 ({rMultiple}R)</span>
                  <span className="text-neg">${riskAmount.toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-4">SL 距离</span>
                  <span className="text-ink-3">${slDistance.toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-4">建议下单量</span>
                  <span className="text-gold">{suggestQty.toFixed(4)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-4">当前最大亏损</span>
                  <span className="text-neg">${maxLoss.toFixed(2)}</span>
                </div>
              </div>
              <button
                onClick={() => setAmount(suggestQty.toFixed(4))}
                className="rounded bg-gold/20 py-0.5 text-[9px] text-gold hover:bg-gold/30"
              >
                套用建议量
              </button>
            </div>
            {currentPosition && (
              <div className="flex gap-1 mb-1">
                <button
                  onClick={() => runPosAction("closeAll")}
                  className={`flex-1 rounded py-1 text-[10px] ${posConfirm.closeAll ? "bg-neg text-ink" : "bg-neg/30 text-neg hover:bg-neg/50"}`}
                >
                  {posConfirm.closeAll ? "确认全平" : "全平"}
                </button>
                <button
                  onClick={() => runPosAction("hedge")}
                  className={`flex-1 rounded py-1 text-[10px] ${posConfirm.hedge ? "bg-gold text-ink" : "bg-gold/20 text-gold hover:bg-gold/30"}`}
                >
                  {posConfirm.hedge ? "确认对冲" : "对冲"}
                </button>
                <button
                  onClick={() => runPosAction("reverse")}
                  className={`flex-1 rounded py-1 text-[10px] ${posConfirm.reverse ? "bg-gold text-ink" : "bg-gold/20 text-gold hover:bg-gold/30"}`}
                >
                  {posConfirm.reverse ? "确认反手" : "反手"}
                </button>
              </div>
            )}
            <button
              onClick={submitOrder}
              disabled={submitDisabled}
              className={`rounded py-1.5 font-medium text-bg ${side === "buy" ? "bg-pos" : "bg-neg"} ${submitDisabled ? "opacity-50 cursor-not-allowed" : ""}`}
            >
              {submitting ? "提交中…" : `${side === "buy" ? "买入" : "卖出"} ${pageFilter.symbol || "BTC"}${amountNum > 0 ? ` ${amount}` : ""}`}
            </button>
          </div>
        </V17Card>
        <div className="flex flex-col gap-2">
          <OrderBookCard symbol={symBase} onPickPrice={(p) => setPrice(p)} />
          <V17Card title="订单管理">
            <div className="flex gap-1 mb-2 text-[10px]">
              {ORDER_TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setOrderTab(t.id)}
                  className={`rounded px-2 py-0.5 ${orderTab === t.id ? "bg-gold/20 text-gold" : "bg-surface-2 text-ink-3 hover:text-ink-1"}`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="dtable" role="table" aria-label="订单管理">
              <div className="grid grid-cols-[70px_1fr_40px_40px_70px_50px_60px_50px_80px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono" role="row">
                <span role="columnheader">订单号</span><span role="columnheader">品种</span><span role="columnheader">方向</span><span role="columnheader">类型</span><span role="columnheader" className="text-right">价格</span><span role="columnheader" className="text-right">数量</span><span role="columnheader">成交</span><span role="columnheader">状态</span><span role="columnheader">操作</span>
              </div>
              {visibleOrders.map((r, i) => {
                const dCls = r.side === "买" ? "text-pos" : "text-neg";
                const statusLabel = r.status === "working" ? "挂单中"
                  : r.status === "partial" ? "部分"
                  : r.status === "filled" ? "已成交"
                  : "已撤";
                const statusCls = r.status === "working" ? "text-gold"
                  : r.status === "filled" ? "text-pos"
                  : r.status === "canceled" ? "text-ink-4"
                  : "text-pos";
                return (
                  <div
                    key={i}
                    className="grid grid-cols-[70px_1fr_40px_40px_70px_50px_60px_50px_80px] gap-2 text-[10px] py-1 border-b border-border-soft items-center"
                    role="row"
                  >
                    <span className="text-ink-4 font-mono">{r.id}</span>
                    <span className="text-gold font-mono">{r.sym}</span>
                    <span className={dCls}>{r.side}</span>
                    <span className="text-ink-3">{r.type}</span>
                    <span className="num text-right font-mono">{r.price}</span>
                    <span className="num text-right font-mono">{r.qty}</span>
                    <span className="text-right text-ink-3 font-mono">{r.filled}</span>
                    <span className={`text-right font-mono ${statusCls}`}>
                      {statusLabel}
                    </span>
                    <span className="flex gap-1">
                      <button
                        onClick={() => setAmendTarget({ id: r.id, price: r.price.replace(/,/g, "") })}
                        className="rounded bg-surface-2 px-1.5 py-0.5 text-[9px] text-ink-3 hover:text-gold"
                      >
                        改
                      </button>
                      <button
                        onClick={() => cancelOrder(r.id)}
                        className="rounded bg-surface-2 px-1.5 py-0.5 text-[9px] text-ink-3 hover:text-neg"
                      >
                        撤
                      </button>
                    </span>
                    {amendTarget?.id === r.id && (
                      <div className="amend-inline col-span-full flex gap-1 py-1">
                        <input
                          className="flex-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-mono text-ink-1 border border-border-soft"
                          value={amendTarget.price}
                          onChange={(e) => setAmendTarget({ ...amendTarget, price: e.target.value })}
                          placeholder="新价格"
                          autoFocus
                        />
                        <button
                          onClick={() => amendPrice(amendTarget.id, amendTarget.price)}
                          className="rounded bg-gold/20 px-2 py-0.5 text-[9px] text-gold hover:bg-gold/30"
                        >
                          改
                        </button>
                        <button
                          onClick={() => setAmendTarget(null)}
                          className="rounded bg-surface-2 px-2 py-0.5 text-[9px] text-ink-3 hover:text-ink-1"
                        >
                          取消
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </V17Card>
          <V17Card title="执行统计">
            <div className="grid grid-cols-4 gap-2">
              <StatBlock label="今日下单" value={todayCount} />
              <StatBlock label="已成交" value={filledCount} cls="text-pos" />
              <StatBlock label="撤单率" value={`${cancelRate.toFixed(0)}%`} cls="text-neg" />
              <StatBlock label="滑点" value={avgSlippage != null ? `${avgSlippage >= 0 ? "+" : ""}${avgSlippage.toFixed(2)}%` : "—"} cls="text-gold" />
            </div>
          </V17Card>
          <V17Card title="成交记录">
            <TradeHistory />
          </V17Card>
          <V17Card title="策略信号">
            <StrategyStatus />
          </V17Card>
        </div>
      </div>
      <RiskAlertDialog
        open={showRiskDialog}
        alerts={riskAlerts}
        onConfirm={confirm}
        onCancel={cancel}
      />
    </div>
  );
}
