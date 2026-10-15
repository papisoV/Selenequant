"use client";

import { BlockComposer } from "@/components/shared/block-composer";
import { UsRadarAgentsNode } from "@/components/watch/us-radar-agents-node";

// us-radar — 判定表 r5:5 条收 block + 3 tab + node
// block=主面板(快照+全域,us-radar 自身) · 宇宙/事件信号/市场总览 各 1 tab
// 供应商状态 tab = K23 capabilities + K24 readiness 合并 seg(数据可信度是判断侧面,不适用管线正则)
// node=K25 研究代理就绪 → 面板头右侧状态区
export default function Page() {
  return (
    <BlockComposer
      panels={[
        { slug: "us-radar", label: "主面板" },
      ]}
      tabs={[
        { slug: "us-radar-universe", label: "代币宇宙" },
        { slug: "us-radar-event-signals", label: "事件信号" },
        { slug: "us-radar-market-overview", label: "市场总览" },
        {
          slug: "providers", label: "供应商状态", views: [
            { slug: "us-radar-providers-readiness", label: "就绪" },
            { slug: "us-radar-providers-capabilities", label: "能力" },
          ],
        },
      ]}
      defaultTab="us-radar-universe"
      node={<UsRadarAgentsNode />}
    />
  );
}
