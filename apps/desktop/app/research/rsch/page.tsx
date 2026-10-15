"use client";

import { BlockComposer } from "@/components/shared/block-composer";

// 研究分析域 hub — 判定表 r5:4 条收 1 block + 3 tab
// block=研究中心(rsch 本体) · analysis/analytics/research_conclusions 各 1 tab(US-209)
export default function Page() {
  return (
    <BlockComposer
      panels={[{ slug: "rsch", label: "策略工厂" }]}
      tabs={[
        { slug: "analysis", label: "行情分析" },
        { slug: "analytics", label: "分析统计" },
        { slug: "research_conclusions", label: "研究结论" },
      ]}
      defaultTab="analysis"
    />
  );
}
