"use client";

import React, { useRef, useEffect, useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useUiText } from "@/components/i18n/use-ui-text"

// 旧代码 kingfisher/liqmap-canvas.tsx 搬迁
// 纯展示组件: 输入 data, 输出 <canvas>, 无外部依赖/状态/副作用
// ResizeObserver + devicePixelRatio 已处理响应式

interface LiqMapCluster {
  price_level: number;
  price_min: number;
  price_max: number;
  long_liquidated_usd: number;
  short_liquidated_usd: number;
  total_liquidated_usd: number;
  long_count: number;
  short_count: number;
  intensity: number;
  cascading_risk: number;
  dominant_side: "long" | "short" | "neutral";
}

interface LiqMapZone {
  zone_type: "support" | "resistance";
  price_low: number;
  price_high: number;
  total_liquidated_usd: number;
  cluster_count: number;
  cascading_risk: number;
  estimated_magnitude_usd: number;
}

export interface LiquidationHeatmapData {
  symbol: string;
  current_price: number;
  clusters: LiqMapCluster[];
  zones: LiqMapZone[];
  total_long_liquidated_usd: number;
  total_short_liquidated_usd: number;
  window: string;
  timestamp: number;
}

export type { LiqMapCluster, LiqMapZone };

interface LiqMapCanvasProps {
  data: LiquidationHeatmapData | null;
  className?: string;
}

interface TooltipData {
  x: number;
  y: number;
  type: "cluster" | "zone";
  cluster?: LiqMapCluster;
  zone?: LiqMapZone;
}

const PADDING_LEFT = 8;
const PADDING_RIGHT = 60;
const PADDING_TOP = 8;
const PADDING_BOTTOM = 8;
const LONG_COLOR = { r: 239, g: 68, b: 68 };
const SHORT_COLOR = { r: 59, g: 130, b: 246 };
const SUPPORT_COLOR = "rgba(34, 197, 94, 0.08)";
const RESISTANCE_COLOR = "rgba(239, 68, 68, 0.08)";
const SUPPORT_BORDER = "rgba(34, 197, 94, 0.25)";
const RESISTANCE_BORDER = "rgba(239, 68, 68, 0.25)";

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

export function LiqMapCanvas({ data, className }: LiqMapCanvasProps) {
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
    if (containerRef.current) {
      observer.observe(containerRef.current);
    }
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

    if (drawWidth <= 0 || drawHeight <= 0) return;

    const allPrices = [
      data.current_price,
      ...data.clusters.map((c) => c.price_min),
      ...data.clusters.map((c) => c.price_max),
      ...data.zones.map((z) => z.price_low),
      ...data.zones.map((z) => z.price_high),
    ];

    const priceMin = Math.min(...allPrices) * 0.998;
    const priceMax = Math.max(...allPrices) * 1.002;
    const priceRange = priceMax - priceMin || 1;

    const priceToY = (price: number): number => {
      return PADDING_TOP + drawHeight * (1 - (price - priceMin) / priceRange);
    };

    const maxLiquidated = data.clusters.length > 0
      ? Math.max(...data.clusters.map((c) => c.total_liquidated_usd))
      : 1;

    for (const zone of data.zones) {
      const yTop = priceToY(zone.price_high);
      const yBottom = priceToY(zone.price_low);
      const height = yBottom - yTop;

      ctx.fillStyle = zone.zone_type === "support" ? SUPPORT_COLOR : RESISTANCE_COLOR;
      ctx.fillRect(PADDING_LEFT, yTop, drawWidth, height);

      ctx.strokeStyle = zone.zone_type === "support" ? SUPPORT_BORDER : RESISTANCE_BORDER;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.strokeRect(PADDING_LEFT, yTop, drawWidth, height);
      ctx.setLineDash([]);

      ctx.fillStyle = zone.zone_type === "support" ? "rgba(34, 197, 94, 0.5)" : "rgba(239, 68, 68, 0.5)";
      ctx.font = "9px monospace";
      ctx.textAlign = "left";
      ctx.fillText(
        `${zone.zone_type === "support" ? "S" : "R"} ${formatUSD(zone.estimated_magnitude_usd)}`,
        PADDING_LEFT + 4,
        yTop + 10,
      );
    }

    for (const cluster of data.clusters) {
      const yCenter = priceToY(cluster.price_level);
      const yTop = priceToY(cluster.price_max);
      const yBottom = priceToY(cluster.price_min);
      const barHeight = Math.max(yBottom - yTop, 3);

      const widthRatio = Math.min(cluster.total_liquidated_usd / maxLiquidated, 1);
      const barWidth = drawWidth * widthRatio;

      const alpha = 0.2 + cluster.intensity * 0.7;

      if (cluster.cascading_risk > 0.5) {
        const glowAlpha = cluster.cascading_risk * 0.15;
        const glowColor = cluster.dominant_side === "long"
          ? `rgba(${LONG_COLOR.r}, ${LONG_COLOR.g}, ${LONG_COLOR.b}, ${glowAlpha})`
          : `rgba(${SHORT_COLOR.r}, ${SHORT_COLOR.g}, ${SHORT_COLOR.b}, ${glowAlpha})`;

        ctx.shadowColor = cluster.dominant_side === "long"
          ? `rgba(${LONG_COLOR.r}, ${LONG_COLOR.g}, ${LONG_COLOR.b}, 0.5)`
          : `rgba(${SHORT_COLOR.r}, ${SHORT_COLOR.g}, ${SHORT_COLOR.b}, 0.5)`;
        ctx.shadowBlur = cluster.cascading_risk * 12;
        ctx.fillStyle = glowColor;
        ctx.fillRect(PADDING_LEFT, yCenter - barHeight / 2 - 2, barWidth, barHeight + 4);
        ctx.shadowBlur = 0;
      }

      if (cluster.long_liquidated_usd > 0) {
        const longWidth = barWidth * (cluster.long_liquidated_usd / cluster.total_liquidated_usd);
        ctx.fillStyle = `rgba(${LONG_COLOR.r}, ${LONG_COLOR.g}, ${LONG_COLOR.b}, ${alpha})`;
        ctx.fillRect(PADDING_LEFT, yCenter - barHeight / 2, longWidth, barHeight);
      }

      if (cluster.short_liquidated_usd > 0) {
        const shortWidth = barWidth * (cluster.short_liquidated_usd / cluster.total_liquidated_usd);
        const longWidth = barWidth * (cluster.long_liquidated_usd / cluster.total_liquidated_usd);
        ctx.fillStyle = `rgba(${SHORT_COLOR.r}, ${SHORT_COLOR.g}, ${SHORT_COLOR.b}, ${alpha})`;
        ctx.fillRect(PADDING_LEFT + longWidth, yCenter - barHeight / 2, shortWidth, barHeight);
      }
    }

    const currentY = priceToY(data.current_price);
    ctx.setLineDash([6, 3]);
    ctx.strokeStyle = "rgba(250, 204, 21, 0.7)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(PADDING_LEFT, currentY);
    ctx.lineTo(PADDING_LEFT + drawWidth, currentY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "rgba(250, 204, 21, 0.9)";
    ctx.font = "bold 10px monospace";
    ctx.textAlign = "left";
    ctx.fillText(formatPrice(data.current_price), PADDING_LEFT + drawWidth + 4, currentY + 3);

    ctx.fillStyle = "rgba(148, 163, 184, 0.6)";
    ctx.font = "9px monospace";
    ctx.textAlign = "left";
    const gridLines = 5;
    for (let i = 0; i <= gridLines; i++) {
      const price = priceMax - (priceRange / gridLines) * i;
      const y = PADDING_TOP + (drawHeight / gridLines) * i;
      ctx.fillText(formatPrice(price), PADDING_LEFT + drawWidth + 4, y + 3);
    }

    ctx.font = "9px monospace";
    ctx.textAlign = "left";
    ctx.fillStyle = `rgba(${LONG_COLOR.r}, ${LONG_COLOR.g}, ${LONG_COLOR.b}, 0.8)`;
    ctx.fillRect(PADDING_LEFT + 4, PADDING_TOP + drawHeight - 24, 8, 8);
    ctx.fillText("Long Liq", PADDING_LEFT + 16, PADDING_TOP + drawHeight - 16);

    ctx.fillStyle = `rgba(${SHORT_COLOR.r}, ${SHORT_COLOR.g}, ${SHORT_COLOR.b}, 0.8)`;
    ctx.fillRect(PADDING_LEFT + 80, PADDING_TOP + drawHeight - 24, 8, 8);
    ctx.fillText("Short Liq", PADDING_LEFT + 92, PADDING_TOP + drawHeight - 16);
  }, [data, canvasSize]);

  useEffect(() => {
    animFrameRef.current = requestAnimationFrame(draw);
    return () => {
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
      }
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

    const allPrices = [
      data.current_price,
      ...data.clusters.map((c) => c.price_min),
      ...data.clusters.map((c) => c.price_max),
      ...data.zones.map((z) => z.price_low),
      ...data.zones.map((z) => z.price_high),
    ];
    const priceMin = Math.min(...allPrices) * 0.998;
    const priceMax = Math.max(...allPrices) * 1.002;
    const priceRange = priceMax - priceMin || 1;

    const price = priceMax - ((y - PADDING_TOP) / drawHeight) * priceRange;

    for (const zone of data.zones) {
      if (price >= zone.price_low && price <= zone.price_high) {
        setTooltip({ x, y, type: "zone", zone });
        return;
      }
    }

    let closestCluster: LiqMapCluster | null = null;
    let closestDist = Infinity;

    for (const cluster of data.clusters) {
      const dist = Math.abs(cluster.price_level - price);
      if (dist < closestDist) {
        closestDist = dist;
        closestCluster = cluster;
      }
    }

    if (closestCluster && closestDist < priceRange * 0.03) {
      setTooltip({ x, y, type: "cluster", cluster: closestCluster });
    } else {
      setTooltip(null);
    }
  }, [data, canvasSize]);

  const handleMouseLeave = useCallback(() => {
    setTooltip(null);
  }, []);

  if (!data) {
    return (
      <div className={cn("flex items-center justify-center h-48 text-[10px] text-ink-4", className)}>
        {tr("无爆仓热力数据")}
      </div>
    );
  }

  return (
    <div className={cn("relative w-full h-full", className)} ref={containerRef}>
      <canvas
        ref={canvasRef}
        className="w-full h-full cursor-crosshair"
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
      />

      {tooltip && (
        <div
          className="absolute pointer-events-none z-10 bg-surface-2/95 border border-border-soft rounded-md shadow-lg px-2 py-1.5 text-[10px] max-w-[220px]"
          style={{
            left: Math.min(tooltip.x + 12, canvasSize.width - 230),
            top: Math.max(tooltip.y - 80, 4),
          }}
        >
          {tooltip.type === "cluster" && tooltip.cluster && (
            <>
              <div className="font-mono font-bold mb-1">
                <span className={tooltip.cluster.dominant_side === "long" ? "text-neg" : "text-pos"}>
                  {tooltip.cluster.dominant_side.toUpperCase()}
                </span>
                {" "}{formatPrice(tooltip.cluster.price_level)}
              </div>
              <div className="text-ink-4">
                Range: {formatPrice(tooltip.cluster.price_min)} - {formatPrice(tooltip.cluster.price_max)}
              </div>
              <div className="mt-1 space-y-0.5">
                <div className="flex justify-between gap-4">
                  <span className="text-neg">Long Liq</span>
                  <span className="font-mono">{formatUSD(tooltip.cluster.long_liquidated_usd)}</span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-pos">Short Liq</span>
                  <span className="font-mono">{formatUSD(tooltip.cluster.short_liquidated_usd)}</span>
                </div>
              </div>
              {tooltip.cluster.cascading_risk > 0.3 && (
                <div className="mt-1 pt-1 border-t border-border-soft">
                  <span className="text-gold">Cascading Risk: </span>
                  <span className="font-mono">{(tooltip.cluster.cascading_risk * 100).toFixed(0)}%</span>
                </div>
              )}
            </>
          )}

          {tooltip.type === "zone" && tooltip.zone && (
            <>
              <div className="font-mono font-bold mb-1">
                <span className={tooltip.zone.zone_type === "support" ? "text-pos" : "text-neg"}>
                  {tooltip.zone.zone_type === "support" ? "SUPPORT" : "RESISTANCE"}
                </span>
              </div>
              <div className="text-ink-4">
                {formatPrice(tooltip.zone.price_low)} - {formatPrice(tooltip.zone.price_high)}
              </div>
              <div className="mt-1 space-y-0.5">
                <div className="flex justify-between gap-4">
                  <span className="text-ink-4">Total Liq</span>
                  <span className="font-mono">{formatUSD(tooltip.zone.total_liquidated_usd)}</span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-ink-4">Clusters</span>
                  <span className="font-mono">{tooltip.zone.cluster_count}</span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-ink-4">Magnitude</span>
                  <span className="font-mono">{formatUSD(tooltip.zone.estimated_magnitude_usd)}</span>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default LiqMapCanvas;
