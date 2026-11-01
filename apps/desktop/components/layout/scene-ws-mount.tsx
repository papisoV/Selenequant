"use client";

import { useSceneWs } from "@/lib/hooks/use-scene-ws";
import { usePaperAccountTick } from "@/lib/hooks/use-paper-account-tick";
import { usePaperWs } from "@/lib/ws/use-paper-ws";
import { useTradingWs } from "@/lib/ws/use-trading-ws";
import { useStrategyWs } from "@/lib/ws/use-strategy-ws";
import { useTradingModeStore } from "@/lib/store/trading-mode";

// DashboardLayout 是 server component，不能直接调 client hook
// 用这个 client wrapper 挂载：
//   - useSceneWs：行情数据
//   - usePaperAccountTick：1.5s 净值/盈亏跳动
//   - usePaperWs：paper 账户实时数据（mode === "paper" 时数据流）
//   - useStrategyWs：策略信号实时流（一直挂载）
export function SceneWsMount() {
  useSceneWs();
  usePaperAccountTick(true);
  usePaperWs("paper-default");
  useStrategyWs();
  return null;
}

// 按 mode 决定是否挂载实盘 trading ws
// 单独组件：hook 不能在条件分支里调用，所以用"组件是否渲染"来控制 hook 是否 mount
function LiveTradingWsMount() {
  useTradingWs();
  return null;
}

export function ConditionalTradingWsMount() {
  const mode = useTradingModeStore((s) => s.mode);
  if (mode !== "live") return null;
  return <LiveTradingWsMount />;
}
