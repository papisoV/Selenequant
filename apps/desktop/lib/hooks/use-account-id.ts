"use client";

import { useTradingModeStore } from "@/lib/store/trading-mode";

const LIVE_ACCOUNT_STORAGE_KEY = "selenespace-live-account-id";

// account_id 派生 hook
// - paper 模式: 固定 "paper-default"
// - live 模式: 读 localStorage 里用户绑定的 live 账户 id,无则 fallback "paper-default"
// community edition 短期只支持 paper,live 路径未来补
export function useAccountId(): string {
  const mode = useTradingModeStore((s) => s.mode);
  if (mode !== "live") return "paper-default";
  if (typeof window === "undefined") return "paper-default";
  try {
    const v = window.localStorage.getItem(LIVE_ACCOUNT_STORAGE_KEY);
    return v || "paper-default";
  } catch {
    return "paper-default";
  }
}
