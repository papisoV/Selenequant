"use client";

import { useRef } from "react";
import { useContextStore } from "@/lib/store/context";
import { useSceneStore } from "@/lib/store/scene";
import { useMarketsWs, type MarketMessage } from "@/lib/ws/use-markets-ws";
import { useIntelligenceWs, type IntelligenceMessage } from "@/lib/ws/use-intelligence-ws";

// 设计稿 L8372-8385 场景切换 + symbol 切换的 WS 订阅生命周期
// 场景变化 → intelligence channels 重订阅
// symbol 变化 → markets channel 重订阅

const SCENE_INTEL_CHANNELS: Record<string, Array<"news" | "events" | "pulse" | "alerts">> = {
  watch: ["news", "events", "pulse", "alerts"],
  research: ["news", "events"],
  trade: ["events", "alerts"],
  system: ["alerts"],
};

export interface SceneWsStats {
  marketMessages: number;
  intelMessages: number;
  lastMarketType?: MarketMessage["type"];
  lastIntelType?: IntelligenceMessage["type"];
  lastMarketTs?: string;
  lastIntelTs?: string;
}

export function useSceneWs(): SceneWsStats {
  const { pageFilter } = useContextStore();
  const { current: scene } = useSceneStore();

  const statsRef = useRef<SceneWsStats>({
    marketMessages: 0,
    intelMessages: 0,
  });

  // 后端要 unified 格式 "BTC/USDT"，但 pageFilter.symbol 可能是 bare "BTC"
  // （来自 sidebar selectWatchlist 的 sym.replace(/[-/].*$/, "")）
  // 规范化：bare symbol → BASE/USDT
  const rawSymbol = pageFilter.symbol || "BTC";
  const normalizedSymbol = rawSymbol.includes("/") ? rawSymbol : `${rawSymbol}/USDT`;
  const symbols = [normalizedSymbol];
  const exchange = pageFilter.exchange === "all" ? "binance" : pageFilter.exchange;

  const pushMarket = (msg: MarketMessage) => {
    statsRef.current = {
      ...statsRef.current,
      marketMessages: statsRef.current.marketMessages + 1,
      lastMarketType: msg.type,
      lastMarketTs: msg.timestamp ?? new Date().toISOString(),
    };
  };

  useMarketsWs({
    symbols,
    exchange,
    marketType: "futures",
    interval: pageFilter.tf,
    onMessage: pushMarket,
    enabled: true,
  });

  const channels = SCENE_INTEL_CHANNELS[scene] ?? SCENE_INTEL_CHANNELS.watch;

  const pushIntel = (msg: IntelligenceMessage) => {
    statsRef.current = {
      ...statsRef.current,
      intelMessages: statsRef.current.intelMessages + 1,
      lastIntelType: msg.type,
      lastIntelTs: msg.timestamp ?? new Date().toISOString(),
    };
  };

  useIntelligenceWs({
    channels,
    onMessage: pushIntel,
    enabled: true,
  });

  return statsRef.current;
}
