"use client";

import { create } from "zustand";

export interface RiskOverviewState {
  symbol: string;
  timeRange: "7d" | "30d";
  positionTab: "pending" | "open" | "closed";
  riskTab: "live" | "portfolio";
  setSymbol: (s: string) => void;
  setTimeRange: (t: "7d" | "30d") => void;
  setPositionTab: (t: "pending" | "open" | "closed") => void;
  setRiskTab: (t: "live" | "portfolio") => void;
}

export const useRiskOverviewStore = create<RiskOverviewState>((set) => ({
  symbol: "BTC",
  timeRange: "7d",
  positionTab: "open",
  riskTab: "live",
  setSymbol: (s) => set({ symbol: s }),
  setTimeRange: (t) => set({ timeRange: t }),
  setPositionTab: (t) => set({ positionTab: t }),
  setRiskTab: (t) => set({ riskTab: t }),
}));
