"use client";

import React, { useRef, useEffect, useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useUiText } from "@/components/i18n/use-ui-text"

// 旧代码 kingfisher/vpvr-canvas.tsx 搬迁
// 纯展示组件: 输入 VPVRData, 输出 <canvas> (成交量分布 + POC/VA)

export interface VPVRLevel {
  price: number;
  total_volume: number;
  buy_volume: number;
  sell_volume: number;
}

export interface VPVRData {
  symbol: string;
  exchange: string;
  levels: VPVRLevel[];
  poc_price: number;
  va_high: number;
  va_low: number;
  total_volume: number;
  total_buy_volume: number;
  total_sell_volume: number;
  bucket_size: number;
  window: string;
  timestamp: number;
}

interface VPVRCanvasProps {
  data: VPVRData | null;
  className?: string;
}

interface TooltipData {
  x: number;
  y: number;
  level: VPVRLevel;
}

const PADDING_LEFT = 8;
const PADDING_RIGHT = 60;
const PADDING_TOP = 8;
const PADDING_BOTTOM = 8;
const BUY_COLOR = { r: 34, g: 197, b: 94 };
const SELL_COLOR = { r: 239, g: 68, b: 68 };
const POC_COLOR = "rgba(250, 204, 21, 0.8)";
const VA_COLOR = "rgba(99, 102, 241, 0.06)";
const VA_BORDER = "rgba(99, 102, 241, 0.2)";

function formatVol(val: number): string {
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `${(val / 1_000).toFixed(1)}K`;
  return val.toFixed(2);
}

function formatPrice(val: number): string {
  if (val >= 1000) return val.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (val >= 1) return val.toFixed(2);
  return val.toFixed(4);
}

export function VPVRCanvas({ data, className }: VPVRCanvasProps) {
  const tr = useUiText()

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
    if (!canvas || !data || data.levels.length === 0) return;

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
    if (drawWidth <= 0 || drawHeight <= 0) return;

    const levels = data.levels;
    const priceMin = levels[0].price - data.bucket_size / 2;
    const priceMax = levels[levels.length - 1].price + data.bucket_size / 2;
    const priceRange = priceMax - priceMin || 1;

    const priceToY = (price: number): number => {
      return PADDING_TOP + drawHeight * (1 - (price - priceMin) / priceRange);
    };

    const maxVol = Math.max(...levels.map((l) => l.total_volume), 1);

    if (data.va_high > 0 && data.va_low > 0) {
      const vaTop = priceToY(data.va_high);
      const vaBottom = priceToY(data.va_low);
      ctx.fillStyle = VA_COLOR;
      ctx.fillRect(PADDING_LEFT, vaTop, drawWidth, vaBottom - vaTop);
      ctx.strokeStyle = VA_BORDER;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(PADDING_LEFT, vaTop, drawWidth, vaBottom - vaTop);
      ctx.setLineDash([]);
    }

    const rowHeight = drawHeight / levels.length;

    for (let i = 0; i < levels.length; i++) {
      const level = levels[i];
      const yCenter = priceToY(level.price);
      const halfRow = rowHeight / 2 - 0.5;

      const totalRatio = level.total_volume / maxVol;
      const barWidth = drawWidth * totalRatio;

      const buyRatio = level.buy_volume / level.total_volume;
      const buyWidth = barWidth * buyRatio;

      if (level.buy_volume > 0) {
        const alpha = 0.3 + 0.5 * totalRatio;
        ctx.fillStyle = `rgba(${BUY_COLOR.r}, ${BUY_COLOR.g}, ${BUY_COLOR.b}, ${alpha})`;
        ctx.fillRect(PADDING_LEFT, yCenter - halfRow, buyWidth, halfRow * 2);
      }

      if (level.sell_volume > 0) {
        const sellWidth = barWidth * (1 - buyRatio);
        const alpha = 0.3 + 0.5 * totalRatio;
        ctx.fillStyle = `rgba(${SELL_COLOR.r}, ${SELL_COLOR.g}, ${SELL_COLOR.b}, ${alpha})`;
        ctx.fillRect(PADDING_LEFT + buyWidth, yCenter - halfRow, sellWidth, halfRow * 2);
      }
    }

    const pocY = priceToY(data.poc_price);
    ctx.strokeStyle = POC_COLOR;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(PADDING_LEFT, pocY);
    ctx.lineTo(PADDING_LEFT + drawWidth, pocY);
    ctx.stroke();

    ctx.fillStyle = POC_COLOR;
    ctx.font = "bold 9px monospace";
    ctx.textAlign = "left";
    ctx.fillText(`POC ${formatPrice(data.poc_price)}`, PADDING_LEFT + 4, pocY - 4);

    ctx.fillStyle = "rgba(148, 163, 184, 0.6)";
    ctx.font = "9px monospace";
    ctx.textAlign = "left";
    const step = Math.max(1, Math.floor(levels.length / 8));
    for (let i = 0; i < levels.length; i += step) {
      const y = priceToY(levels[i].price);
      ctx.fillText(formatPrice(levels[i].price), PADDING_LEFT + drawWidth + 4, y + 3);
    }

    if (data.va_high > 0) {
      ctx.fillStyle = "rgba(99, 102, 241, 0.6)";
      ctx.font = "8px monospace";
      ctx.fillText("VAH", PADDING_LEFT + drawWidth + 4, priceToY(data.va_high) + 3);
      ctx.fillText("VAL", PADDING_LEFT + drawWidth + 4, priceToY(data.va_low) + 3);
    }

    ctx.font = "9px monospace";
    ctx.textAlign = "left";
    ctx.fillStyle = `rgba(${BUY_COLOR.r}, ${BUY_COLOR.g}, ${BUY_COLOR.b}, 0.8)`;
    ctx.fillRect(PADDING_LEFT + 4, PADDING_TOP + drawHeight - 24, 8, 8);
    ctx.fillText("Buy", PADDING_LEFT + 16, PADDING_TOP + drawHeight - 16);

    ctx.fillStyle = `rgba(${SELL_COLOR.r}, ${SELL_COLOR.g}, ${SELL_COLOR.b}, 0.8)`;
    ctx.fillRect(PADDING_LEFT + 50, PADDING_TOP + drawHeight - 24, 8, 8);
    ctx.fillText("Sell", PADDING_LEFT + 62, PADDING_TOP + drawHeight - 16);
  }, [data, canvasSize]);

  useEffect(() => {
    animFrameRef.current = requestAnimationFrame(draw);
    return () => { if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current); };
  }, [draw]);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!data || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const y = e.clientY - rect.top;

    const drawHeight = canvasSize.height - PADDING_TOP - PADDING_BOTTOM;
    const levels = data.levels;
    if (levels.length === 0) return;

    const priceMin = levels[0].price - data.bucket_size / 2;
    const priceMax = levels[levels.length - 1].price + data.bucket_size / 2;
    const priceRange = priceMax - priceMin || 1;
    const price = priceMax - ((y - PADDING_TOP) / drawHeight) * priceRange;

    let closest = levels[0];
    let closestDist = Infinity;
    for (const l of levels) {
      const dist = Math.abs(l.price - price);
      if (dist < closestDist) { closestDist = dist; closest = l; }
    }

    if (closestDist < priceRange * 0.05) {
      setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, level: closest });
    } else {
      setTooltip(null);
    }
  }, [data, canvasSize]);

  if (!data || data.levels.length === 0) {
    return (
      <div className={cn("flex items-center justify-center h-48 text-[10px] text-ink-4", className)}>
        {tr("无成交量分布数据")}
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
          className="absolute pointer-events-none z-10 bg-surface-2/95 border border-border-soft rounded-md shadow-lg px-2 py-1.5 text-[10px] max-w-[200px]"
          style={{
            left: Math.min(tooltip.x + 12, canvasSize.width - 210),
            top: Math.max(tooltip.y - 60, 4),
          }}
        >
          <div className="font-mono font-bold mb-1">{formatPrice(tooltip.level.price)}</div>
          <div className="space-y-0.5">
            <div className="flex justify-between gap-4">
              <span className="text-ink-4">Total</span>
              <span className="font-mono">{formatVol(tooltip.level.total_volume)}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-pos">Buy</span>
              <span className="font-mono">{formatVol(tooltip.level.buy_volume)}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-neg">Sell</span>
              <span className="font-mono">{formatVol(tooltip.level.sell_volume)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default VPVRCanvas;
