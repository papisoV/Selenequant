"use client";

import { tierGrants, type FeatureKey } from "@/lib/tiers";
import { useTierStore } from "@/lib/store/tier";
import { FeatureLock } from "./feature-lock";

// 功能墙容器:tier 满足 → 渲染 children;不满足 → 锁层(默认 replace,可 blur 原内容)
export function FeatureGate({
  feature,
  lockMode = "replace",
  children,
  fallback,
}: {
  feature: FeatureKey;
  lockMode?: "blur" | "replace";
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const tier = useTierStore((s) => s.tier);
  if (tierGrants(feature, tier)) return <>{children}</>;
  if (fallback !== undefined) return <>{fallback}</>;
  return <FeatureLock feature={feature} lockMode={lockMode}>{children}</FeatureLock>;
}
