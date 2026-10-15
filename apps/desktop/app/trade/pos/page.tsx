"use client";

import { ModeToggle } from "@/components/trading/mode-toggle";
import { BlockComposer } from "@/components/shared/block-composer";

// 实盘 hub — 判定表 r5:pos 正典链头 + portfolio tab(US-209)
// node=trading-mode(K41:模式切换是操作,ModeToggle 挂 hub 头部);portfolio→pos tab
export default function Page() {
  return (
    <BlockComposer
      panels={[{ slug: "pos", label: "持仓总览" }]}
      tabs={[{ slug: "portfolio", label: "组合构建" }]}
      defaultTab="pos"
      node={<ModeToggle />}
    />
  );
}
