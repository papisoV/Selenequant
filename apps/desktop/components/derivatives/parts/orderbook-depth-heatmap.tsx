"use client";

import React, { useRef, useEffect, useState, useCallback } from "react";
import { cn } from "@/lib/utils";

// 旧代码 kingfisher/orderbook-depth-heatmap.tsx 搬迁
// 纯展示组件: 输入 MergedBookSnapshot, 输出 <canvas> (盘口深度热力)

export interface MergedPriceLevel {
  price: number;
  total_amount: number;
  exchange_breakdown: Record<string, number>;
}

export interface MergedBookSnapshot {
  symbol: string;
  exchanges: string[];
  best_bid: number;
  best_ask: number;
  spread: number;
  mid_price: number;
  bids: MergedPriceLevel[];
  asks: MergedPriceLevel[];
  bid_depth_usd: number;
  ask_depth_usd: number;
  bid_ask_ratio: number;
  timestamp: number;
}

interface OrderbookDepthHeatmapProps {
  data: MergedBookSnapshot | null;
  className?: string;
}

interface TooltipData {
  x: number;
  y: number;
  price: number;
  totalAmount: number;
  side: "bid" | "ask";
  breakdown: Record<string, number>;
}

const PADDING_LEFT = 60;
const PADDING_RIGHT = 16;
const PADDING_TOP = 8;
const PADDING_BOTTOM = 24;
const ROW_HEIGHT = 3;
const BID_COLOR = { r: 34, g: 197, b: 94 };
const ASK_COLOR = { r: 239, g: 68, b: 68 };

function formatUSD(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

function formatPrice(val: number): string {
  if (val >= 1000) return val.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (val >= 1) return val.toFixed(2);
  return val.toFixed(4);
}

export function OrderbookDepthHeatmap({ data, className }: OrderbookDepthHeatmapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const animFrameRef = useRef<number>(0);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });

  const handleResize = useCallback(() => {
    if (!containerRef.current) return;
    const { clientWidth, clientHeight } = containerRef.current;
    setCanvasSize({ width: clientWidth, height: clientHeight });
  }, []);

  useEffect(() => {
    handleResize();
    const observer = new ResizeObserver(handleResize);
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [handleResize]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const displayWidth = canvasSize.width;
    const displayHeight = canvasSize.height;

    if (displayWidth === 0 || displayHeight === 0) return;

    canvas.width = displayWidth * dpr;
    canvas.height = displayHeight * dpr;
    canvas.style.width = `${displayWidth}px`;
    canvas.style.height = `${displayHeight}px`;
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, displayWidth, displayHeight);

    const drawWidth = displayWidth - PADDING_LEFT - PADDING_RIGHT;
    const drawHeight = displayHeight - PADDING_TOP - PADDING_BOTTOM;

    const allLevels = [...data.bids, ...data.asks];
    const maxAmount = allLevels.length > 0
      ? Math.max(...allLevels.map((l) => l.total_amount))
      : 1;

    const priceMin = data.bids.length > 0 ? data.bids[data.bids.length - 1].price : data.mid_price * 0.99;
    const priceMax = data.asks.length > 0 ? data.asks[data.asks.length - 1].price : data.mid_price * 1.01;
    const priceRange = priceMax - priceMin || 1;

    const priceToY = (price: number): number => {
      return PADDING_TOP + drawHeight * (1 - (price - priceMin) / priceRange);
    };

    ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
    ctx.lineWidth = 1;
    const gridLines = 6;
    for (let i = 0; i <= gridLines; i++) {
      const y = PADDING_TOP + (drawHeight / gridLines) * i;
      ctx.beginPath();
      ctx.moveTo(PADDING_LEFT, y);
      ctx.lineTo(PADDING_LEFT + drawWidth, y);
      ctx.stroke();
    }

    ctx.fillStyle = "rgba(148, 163, 184, 0.7)";
    ctx.font = "10px monospace";
    ctx.textAlign = "right";
    for (let i = 0; i <= gridLines; i++) {
      const price = priceMax - (priceRange / gridLines) * i;
      const y = PADDING_TOP + (drawHeight / gridLines) * i;
      ctx.fillText(formatPrice(price), PADDING_LEFT - 6, y + 3);
    }

    for (const level of data.bids) {
      const y = priceToY(level.price);
      if (y < PADDING_TOP || y > PADDING_TOP + drawHeight) continue;

      const intensity = Math.min(level.total_amount / maxAmount, 1);
      const barWidth = drawWidth * intensity;

      const alpha = 0.15 + intensity * 0.75;
      ctx.fillStyle = `rgba(${BID_COLOR.r}, ${BID_COLOR.g}, ${BID_COLOR.b}, ${alpha})`;
      ctx.fillRect(PADDING_LEFT, y - ROW_HEIGHT / 2, barWidth, ROW_HEIGHT);

      if (intensity > 0.5) {
        ctx.fillStyle = `rgba(${BID_COLOR.r}, ${BID_COLOR.g}, ${BID_COLOR.b}, ${alpha * 0.3})`;
        ctx.fillRect(PADDING_LEFT, y - ROW_HEIGHT, barWidth, ROW_HEIGHT * 2);
      }
    }

    for (const level of data.asks) {
      const y = priceToY(level.price);
      if (y < PADDING_TOP || y > PADDING_TOP + drawHeight) continue;

      const intensity = Math.min(level.total_amount / maxAmount, 1);
      const barWidth = drawWidth * intensity;

      const alpha = 0.15 + intensity * 0.75;
      ctx.fillStyle = `rgba(${ASK_COLOR.r}, ${ASK_COLOR.g}, ${ASK_COLOR.b}, ${alpha})`;
      ctx.fillRect(PADDING_LEFT, y - ROW_HEIGHT / 2, barWidth, ROW_HEIGHT);

      if (intensity > 0.5) {
        ctx.fillStyle = `rgba(${ASK_COLOR.r}, ${ASK_COLOR.g}, ${ASK_COLOR.b}, ${alpha * 0.3})`;
        ctx.fillRect(PADDING_LEFT, y - ROW_HEIGHT, barWidth, ROW_HEIGHT * 2);
      }
    }

    const midY = priceToY(data.mid_price);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "rgba(250, 204, 21, 0.6)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(PADDING_LEFT, midY);
    ctx.lineTo(PADDING_LEFT + drawWidth, midY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "rgba(250, 204, 21, 0.9)";
    ctx.font = "bold 10px monospace";
    ctx.textAlign = "left";
    ctx.fillText(formatPrice(data.mid_price), PADDING_LEFT + drawWidth + 2, midY + 3);

    ctx.fillStyle = "rgba(34, 197, 94, 0.8)";
    ctx.font = "bold 11px monospace";
    ctx.textAlign = "left";
    ctx.fillText(`Bid: ${formatUSD(data.bid_depth_usd)}`, PADDING_LEFT + 4, PADDING_TOP + 14);

    ctx.fillStyle = "rgba(239, 68, 68, 0.8)";
    ctx.fillText(`Ask: ${formatUSD(data.ask_depth_usd)}`, PADDING_LEFT + 4, PADDING_TOP + 28);

    ctx.fillStyle = "rgba(148, 163, 184, 0.6)";
    ctx.font = "10px monospace";
    ctx.textAlign = "right";
    ctx.fillText(`Spread: ${formatPrice(data.spread)}`, PADDING_LEFT + drawWidth, PADDING_TOP + 14);
  }, [data, canvasSize]);

  useEffect(() => {
    animFrameRef.current = requestAnimationFrame(draw);
    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    };
  }, [draw]);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!data || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const drawWidth = canvasSize.width - PADDING_LEFT - PADDING_RIGHT;
    const drawHeight = canvasSize.height - PADDING_TOP - PADDING_BOTTOM;

    if (x < PADDING_LEFT || x > PADDING_LEFT + drawWidth || y < PADDING_TOP || y > PADDING_TOP + drawHeight) {
      setTooltip(null);
      return;
    }

    const priceMin = data.bids.length > 0 ? data.bids[data.bids.length - 1].price : data.mid_price * 0.99;
    const priceMax = data.asks.length > 0 ? data.asks[data.asks.length - 1].price : data.mid_price * 1.01;
    const priceRange = priceMax - priceMin || 1;

    const price = priceMax - ((y - PADDING_TOP) / drawHeight) * priceRange;
    const side: "bid" | "ask" = price >= data.mid_price ? "ask" : "bid";

    const levels = side === "bid" ? data.bids : data.asks;
    let closest = levels[0];
    let closestDist = Infinity;

    for (const level of levels) {
      const dist = Math.abs(level.price - price);
      if (dist < closestDist) {
        closestDist = dist;
        closest = level;
      }
    }

    if (closestDist < priceRange * 0.02) {
      setTooltip({
        x, y,
        price: closest.price,
        totalAmount: closest.total_amount,
        side,
        breakdown: closest.exchange_breakdown,
      });
    } else {
      setTooltip(null);
    }
  }, [data, canvasSize]);

  if (!data) {
    return (
      <div className={cn("flex items-center justify-center h-48 text-[10px] text-ink-4", className)}>
        无盘口数据
      </div>
    );
  }

  return (
    <div className={cn("relative w-full h-full", className)} ref={containerRef}>
      <canvas
        ref={canvasRef}
        className="w-full h-full cursor-crosshair"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setTooltip(null)}
      />

      {tooltip && (
        <div
          className="absolute pointer-events-none z-10 bg-surface-2/95 border border-border-soft rounded-md shadow-lg px-2 py-1.5 text-[10px]"
          style={{
            left: Math.min(tooltip.x + 12, canvasSize.width - 200),
            top: Math.max(tooltip.y - 60, 4),
          }}
        >
          <div className="font-mono font-bold mb-1">
            <span className={tooltip.side === "bid" ? "text-pos" : "text-neg"}>
              {tooltip.side.toUpperCase()}
            </span>
            {" "}{formatPrice(tooltip.price)}
          </div>
          <div className="text-ink-4">
            Total: {formatUSD(tooltip.totalAmount)}
          </div>
          {Object.entries(tooltip.breakdown).length > 0 && (
            <div className="mt-1 space-y-0.5 border-t border-border-soft pt-1">
              {Object.entries(tooltip.breakdown)
                .sort(([, a], [, b]) => b - a)
                .map(([exchange, amount]) => (
                  <div key={exchange} className="flex justify-between gap-4">
                    <span className="text-ink-4">{exchange}</span>
                    <span className="font-mono">{formatUSD(amount)}</span>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default OrderbookDepthHeatmap;
