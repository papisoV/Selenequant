// lib/tiers.ts — 商业化功能分级(PRO/VIP 功能墙),纯函数,可单测
// 不做真 auth/支付:tier 由 useTierStore(localStorage)本地维护,FeatureGate 只做演示 gating

export type UserTier = "free" | "pro" | "vip";

export const TIER_LEVELS: Record<UserTier, number> = {
  free: 0,
  pro: 1,
  vip: 2,
};

export const TIER_LABELS: Record<UserTier, string> = {
  free: "免费版",
  pro: "PRO",
  vip: "VIP",
};

// 演示切换下拉的候选层级
export const DEMO_TIER_OPTIONS: UserTier[] = ["free", "pro", "vip"];

// 功能 → 解锁所需最低层级。AICoin 商业化墙的本地镜像:
//   聪明钱完整数据 / 专业套利 → PRO;会员群 → VIP
export type FeatureKey = "smart_money_full" | "arb_pro" | "member_group";

export const TIER_FEATURES: Record<FeatureKey, UserTier> = {
  smart_money_full: "pro",
  arb_pro: "pro",
  member_group: "vip",
};

export function tierGrants(feature: FeatureKey, tier: UserTier): boolean {
  return TIER_LEVELS[tier] >= TIER_LEVELS[TIER_FEATURES[feature]];
}
