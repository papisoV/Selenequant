"use client";

import { create } from "zustand";
import { TIER_FEATURES, type FeatureKey, type UserTier } from "@/lib/tiers";

// TierStore — 本地演示层级(无真 auth)。持久化:localStorage "selenespace:tier:v1"
// tier 变更 → useTierStore 订阅的 FeatureGate 自动重渲染

const STORAGE_KEY = "selenespace:tier:v1";

interface TierState {
  tier: UserTier;
  setTier: (t: UserTier) => void;
  // 一键切到解锁该 feature 所需的层级(FeatureLock "查看演示" 用)
  switchDemo: (feature: FeatureKey) => void;
}

function readInitial(): UserTier {
  if (typeof window === "undefined") return "free";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return "free";
    const parsed = JSON.parse(raw) as { tier?: UserTier };
    return parsed.tier === "pro" || parsed.tier === "vip" ? parsed.tier : "free";
  } catch {
    return "free";
  }
}

function persist(tier: UserTier) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ tier }));
  } catch {
    // ignore storage error
  }
}

const initial = readInitial();

export const useTierStore = create<TierState>((set) => ({
  tier: initial,
  setTier: (t) => {
    persist(t);
    set({ tier: t });
  },
  switchDemo: (feature) => {
    const t = TIER_FEATURES[feature];
    persist(t);
    set({ tier: t });
  },
}));
