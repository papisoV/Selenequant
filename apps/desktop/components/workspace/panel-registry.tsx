"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import type { PanelId, PanelSlot } from "@/lib/workspace/workspace-layouts";
import { useContextStore } from "@/lib/store/context";
import { toSymbolPair } from "@/lib/symbol-utils";
import { getOrderBook } from "@/lib/api/markets";
import type { OrderBook } from "@/lib/types";

/**
 * Panel 注册表 — panelId → 组件 + defaultProps
 *
 * 动态 import 避免 SSR 时 panel 内部 hooks(useState/useEffect)报错,
 * 同时让 workspace 首屏只加载当前可见的 panel。
 */

function PanelLoading() {
  return (
    <div className="flex h-full items-center justify-center text-ink-4 text-sm">
      加载中…
    </div>
  );
}

const AlphaMiningPanel = dynamic(
  () => import("@/components/research/alpha-mining-panel").then((m) => m.AlphaMiningPanel),
  { loading: () => <PanelLoading />, ssr: false },
);
const FactorResearchPanel = dynamic(
  () => import("@/components/research/factor-research-panel").then((m) => m.FactorResearchPanel),
  { loading: () => <PanelLoading />, ssr: false },
);
const VchartPage = dynamic(
  () => import("@/components/watch/vchart-page").then((m) => m.VchartPage),
  { loading: () => <PanelLoading />, ssr: false },
);
const GexPage = dynamic(
  () => import("@/components/watch/gex-page").then((m) => m.GexPage),
  { loading: () => <PanelLoading />, ssr: false },
);
const LiqmapPage = dynamic(
  () => import("@/components/watch/liqmap-page").then((m) => m.LiqmapPage),
  { loading: () => <PanelLoading />, ssr: false },
);
const SigPage = dynamic(
  () => import("@/components/watch/sig-page").then((m) => m.SigPage),
  { loading: () => <PanelLoading />, ssr: false },
);
const KfPage = dynamic(
  () => import("@/components/watch/kf-page").then((m) => m.KfPage),
  { loading: () => <PanelLoading />, ssr: false },
);
const OrderForm = dynamic(
  () => import("@/components/trading/order-form").then((m) => m.OrderForm),
  { loading: () => <PanelLoading />, ssr: false },
);
const OrderbookPanel = dynamic(
  () => import("@/components/trading/orderbook-panel").then((m) => m.OrderbookPanel),
  { loading: () => <PanelLoading />, ssr: false },
);
const WhaleTransferFeed = dynamic(
  () => import("@/components/tactical/whale-transfer-feed").then((m) => m.WhaleTransferFeed),
  { loading: () => <PanelLoading />, ssr: false },
);
const SmartMoneyPage = dynamic(
  () => import("@/components/watch/smart_money-page").then((m) => m.SmartMoneyPage),
  { loading: () => <PanelLoading />, ssr: false },
);
const AlertRulesPage = dynamic(
  () => import("@/components/alerts/alert-rules-page").then((m) => m.AlertRulesPage),
  { loading: () => <PanelLoading />, ssr: false },
);

/** order_form wrapper: 读 pageFilter.symbol 归一化作 OrderForm 受控 symbol prop
 *  slot.props.symbol 若显式传入则优先(布局持久化场景);否则读 store 归一 */
function OrderFormWrapper(props: Record<string, unknown>) {
  const { pageFilter } = useContextStore();
  const symbol = (props.symbol as string | undefined) ?? toSymbolPair(pageFilter.symbol);
  return <OrderForm {...props} symbol={symbol} />;
}

/** watch_ob wrapper: 读 pageFilter.symbol → getOrderBook 轮询(1500ms)喂 OrderbookPanel
 *  symbol 为空/pair 归一由 toSymbolPair 兜底(BTC/USDT);fetch 失败优雅降级 data=null → 骨架屏
 *  slot.props.symbol 若显式传入则优先(布局持久化场景) */
const ORDERBOOK_POLL_MS = 1500;

function OrderBookWrapper(props: Record<string, unknown>) {
  const { pageFilter } = useContextStore();
  const symbol = (props.symbol as string | undefined) ?? toSymbolPair(pageFilter.symbol);
  const [data, setData] = useState<OrderBook | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    const poll = async () => {
      try {
        const ob = await getOrderBook(symbol);
        if (!cancelled) setData(ob);
      } catch {
        // 数据管道不可用 → 优雅降级(OrderbookPanel 收到 null 显示骨架屏)
        if (!cancelled) setData(null);
      }
    };

    void poll();
    timer = setInterval(poll, ORDERBOOK_POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [symbol]);

  const baseCurrency = symbol.split("/")[0] || undefined;
  return <OrderbookPanel data={data} title={symbol} baseCurrency={baseCurrency} />;
}
export { OrderBookWrapper };

/** onchain_whale wrapper: 读 pageFilter.symbol 归一化喂 WhaleTransferFeed 过滤
 *  slot.props.symbol 若显式传入则优先(布局持久化场景) */
function WhaleTransferWrapper(props: Record<string, unknown>) {
  const { pageFilter } = useContextStore();
  const symbol = (props.symbol as string | undefined) ?? toSymbolPair(pageFilter.symbol);
  return <WhaleTransferFeed symbolFilter={symbol} />;
}

/** onchain_sm wrapper: SmartMoneyPage 无 symbol 依赖,直接透传 */
function SmartMoneyWrapper(props: Record<string, unknown>) {
  return <SmartMoneyPage {...props} />;
}

/** alert_rules wrapper: 预警规则页读全局 store,无 symbol 依赖,直接透传 */
function AlertRulesWrapper(props: Record<string, unknown>) {
  return <AlertRulesPage {...props} />;
}

export interface PanelRegistryEntry {
  Component: React.ComponentType<Record<string, unknown>>;
  defaultProps?: Record<string, unknown>;
}

/** panel 显示标签(类型切换 Select + header 标题用) */
export const PANEL_LABELS: Record<PanelId, string> = {
  alpha_mine: "Alpha 挖矿",
  factors: "因子研究",
  watch_vchart: "专业图表",
  watch_gex: "GEX",
  watch_liqmap: "爆仓热力",
  watch_sig: "信号中心",
  watch_kf: "盯盘主图",
  watch_ob: "盘口",
  order_form: "下单",
  onchain_whale: "巨鲸转账",
  onchain_sm: "聪明钱",
  alert_rules: "预警规则",
};

export const PANEL_REGISTRY: Record<PanelId, PanelRegistryEntry> = {
  alpha_mine: { Component: AlphaMiningPanel as React.ComponentType<Record<string, unknown>> },
  factors: { Component: FactorResearchPanel as React.ComponentType<Record<string, unknown>> },
  watch_vchart: { Component: VchartPage as React.ComponentType<Record<string, unknown>> },
  watch_gex: { Component: GexPage as React.ComponentType<Record<string, unknown>> },
  watch_liqmap: { Component: LiqmapPage as React.ComponentType<Record<string, unknown>> },
  watch_sig: { Component: SigPage as React.ComponentType<Record<string, unknown>> },
  watch_kf: { Component: KfPage as React.ComponentType<Record<string, unknown>> },
  watch_ob: { Component: OrderBookWrapper as React.ComponentType<Record<string, unknown>> },
  order_form: { Component: OrderFormWrapper as React.ComponentType<Record<string, unknown>> },
  onchain_whale: { Component: WhaleTransferWrapper as React.ComponentType<Record<string, unknown>> },
  onchain_sm: { Component: SmartMoneyWrapper as React.ComponentType<Record<string, unknown>> },
  alert_rules: { Component: AlertRulesWrapper as React.ComponentType<Record<string, unknown>> },
};

export function renderPanel(slot: PanelSlot): React.ReactNode {
  const entry = PANEL_REGISTRY[slot.id];
  if (!entry) {
    return (
      <div className="p-6 text-ink-4 text-sm">
        未知 panel: {slot.id}
      </div>
    );
  }
  const props = { ...entry.defaultProps, ...slot.props };
  return <entry.Component {...props} />;
}
