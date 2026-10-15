"use client";

import { SerenityPanel } from "@/components/research/serenity-panel";
import { useStrategyStore } from "@/lib/store/strategy";
import { useShallow } from "zustand/react/shallow";

// serenity-page：策略风险评分面板
// P1：接 useStrategyStore 顶层（serenity-panel 自身只做 REST 扫描）
// 策略信号 WS 顶层挂载在 SceneWsMount 已完成，这里只读 store 数据
export function SerenityPage() {
  const signals = useStrategyStore(useShallow((s) => s.signals));
  // signals 已订阅到 store，serenity-panel 可通过 props 接入（暂保留 panel 原结构）
  void signals;
  return <SerenityPanel />;
}
