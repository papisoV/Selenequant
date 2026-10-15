"use client";

import { AccountGate } from "@/components/shared/account-gate";
import { useAssetsStore } from "@/lib/store/assets";

// 薄封装:资产壳场景委托通用 AccountGate。
// mode 受控于 useAssetsStore(AssetOverview 既有 demo/empty 流),contextId 隔离本上下文
export function AuthGate({ onDemo }: { onDemo: () => void }) {
  const mode = useAssetsStore((s) => s.mode);
  return (
    <AccountGate
      contextId="assets-overview"
      title="暂无资产数据"
      description="连接交易所后此处汇总持仓与余额"
      mode={mode}
      onEnterDemo={onDemo}
    />
  );
}
