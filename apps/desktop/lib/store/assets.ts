"use client";

import { create } from "zustand";
import type { AssetMode } from "@/lib/assets/types";

// AssetOverview 视图状态:模式(空态/demo/live)+ 当前交易所 tab
// 数据(rows/priceMap)由 asset-overview.tsx 派生,不存 store — store 只存交互态

interface AssetsState {
  mode: AssetMode;
  activeExchange: string;
  setMode: (m: AssetMode) => void;
  setActiveExchange: (ex: string) => void;
}

export const useAssetsStore = create<AssetsState>((set) => ({
  mode: "empty",
  activeExchange: "binance",
  setMode: (m) => set({ mode: m }),
  setActiveExchange: (ex) => set({ activeExchange: ex }),
}));
