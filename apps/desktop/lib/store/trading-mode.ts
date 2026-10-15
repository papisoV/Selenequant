"use client";

import { create } from "zustand";

// TradingModeState — paper / live 切换
// 消费者：trd-page（按 mode 切换 paper/live store）、scene-ws-mount（按 mode 决定是否连 trading ws）
// 持久化：localStorage key "selenespace-trading-mode"
// 注意：community edition 默认 paper，live 仅暴露切换能力（后端可能不支持，由 useTradingWs 的 WS 连接失败时 fallback mock）

const STORAGE_KEY = "selenespace-trading-mode";

type TradingMode = "paper" | "live";

interface TradingModeState {
  mode: TradingMode;
  setMode: (m: TradingMode) => void;
}

function readInitialMode(): TradingMode {
  if (typeof window === "undefined") return "paper";
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "live" ? "live" : "paper";
  } catch {
    return "paper";
  }
}

export const useTradingModeStore = create<TradingModeState>((set) => ({
  mode: readInitialMode(),
  setMode: (m) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(STORAGE_KEY, m);
      } catch {
        // ignore storage error
      }
    }
    set({ mode: m });
  },
}));
