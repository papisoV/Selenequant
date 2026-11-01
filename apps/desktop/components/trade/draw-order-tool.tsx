"use client";

import { useState } from "react";
import { useUiText } from "@/components/i18n/use-ui-text"

export interface DemoCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** 确定性演示 K 线(无随机,便于测试断言) */
export function demoCandles(base = 65000, count = 24, step = 250): DemoCandle[] {
  const bars: DemoCandle[] = [];
  for (let i = 0; i < count; i++) {
    const open = base - (count / 2 - i) * step;
    const close = open + ((i % 3) - 1) * (step / 2);
    bars.push({
      time: `T-${count - i}`,
      open: Math.round(open),
      close: Math.round(close),
      high: Math.round(Math.max(open, close) + step * 0.4),
      low: Math.round(Math.min(open, close) - step * 0.4),
    });
  }
  return bars;
}

export const DEMO_CANDLES = demoCandles();

export function DrawOrderTool({ candles = DEMO_CANDLES }: { candles?: DemoCandle[] }) {
  const tr = useUiText()

  const [drawnPrice, setDrawnPrice] = useState<number | null>(null);
  const [qty, setQty] = useState("0.001");
  const [orders, setOrders] = useState<{ price: number; qty: number }[]>([]);

  const sorted = [...candles].sort((a, b) => b.close - a.close);

  const pick = (price: number) => {
    setDrawnPrice(price);
  };

  const placeOrder = () => {
    if (drawnPrice === null) return;
    const qtyN = Math.max(0, Number(qty) || 0);
    if (qtyN <= 0) return;
    setOrders((prev) => [{ price: drawnPrice, qty: qtyN }, ...prev]);
  };

  return (
    <div className="draw-order-tool flex flex-col gap-2">
      <div className="text-[9px] text-ink-4">{tr("画线下单:点击 K 线行画水平线,价格回填落单表单(纯前端演示)")}</div>

      <div className="draw-chart flex flex-col gap-0.5 overflow-auto rounded border border-border-soft p-1 max-h-64">
        {sorted.map((c, i) => {
          const active = drawnPrice === c.close;
          return (
            <button
              type="button"
              key={`${c.time}-${i}`}
              data-price={c.close}
              onClick={() => pick(c.close)}
              className={`draw-bar flex items-center gap-2 rounded px-1 py-0.5 text-[9px] transition-colors ${active ? "bg-gold/20" : "hover:bg-surface-2"}`}
            >
              <span className="w-10 shrink-0 text-ink-4">{c.time}</span>
              <span className="relative h-2 flex-1">
                <span
                  className="absolute inset-y-0 w-px"
                  style={{
                    left: `${(c.low - sorted[sorted.length - 1].low) / (sorted[0].high - sorted[sorted.length - 1].low) * 100}%`,
                    background: c.close >= c.open ? "var(--color-pos)" : "var(--color-neg)",
                  }}
                />
              </span>
              <span className="w-16 shrink-0 text-right font-mono text-ink">{c.close.toLocaleString()}</span>
              {active && <span className="shrink-0 text-[9px] text-gold">{tr("◂ 已画线")}</span>}
            </button>
          );
        })}
      </div>

      {drawnPrice !== null && (
        <div className="drawn-line flex items-center gap-2 rounded border border-gold/40 bg-gold/10 px-2 py-1 text-[10px]">
          <span className="text-ink-3">{tr("水平线价格")}</span>
          <span className="font-mono font-medium text-gold">{drawnPrice.toLocaleString()}</span>
          <button type="button" onClick={() => setDrawnPrice(null)} className="ml-auto text-[9px] text-ink-3 hover:text-ink">
            {tr("清除画线")}
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("买入价(画线预填)")}
          <input aria-label={tr("买入价")} type="number" readOnly value={drawnPrice ?? ""} placeholder={tr("先点击 K 线")}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("数量")}
          <input aria-label={tr("数量")} type="number" value={qty} onChange={(e) => setQty(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
      </div>
      <button type="button" onClick={placeOrder} disabled={drawnPrice === null}
        className="w-fit rounded bg-gold px-2 py-1 text-[10px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50">
        {tr("落单(模拟)")}
      </button>

      {orders.length > 0 && (
        <div className="flex flex-col gap-1">
          <div className="text-[9px] text-ink-4">{tr("已画线挂单")}</div>
          {orders.map((o, i) => (
            <div key={i} className="order-row flex items-center gap-2 rounded border border-border-soft px-2 py-1 text-[11px]">
              <span className="font-mono text-ink">{o.price.toLocaleString()}</span>
              <span className="font-mono text-ink-3">× {o.qty}</span>
              <span className="ml-auto text-[9px] text-gold">{tr("模拟挂单")}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
