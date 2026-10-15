"use client";

import { BlockComposer } from "@/components/shared/block-composer";

// 链上工作台 — 判定表 r5:3 条收 2+block(demo r5 态 onchain-hub 面)
// ov=链上总览(block 盯,src=onchain) · met=链上指标(seg 4 视图,src=onchain-metrics-hub 翻②)
// whale=鲸鱼钱流(seg 2 视图:巨鲸转账/KOL 信号,src=onchain-whales 孤儿收编)
export default function Page() {
  return (
    <BlockComposer
      panels={[
        { slug: "onchain", label: "链上总览", span: 2 },
      ]}
      tabs={[
        {
          slug: "met", label: "链上指标", views: [
            { slug: "onchain-metrics", label: "总览" },
            { slug: "onchain-metrics-active-addresses", label: "活跃地址" },
            { slug: "onchain-metrics-nvt-ratio", label: "NVT" },
            { slug: "onchain-metrics-sopr", label: "SOPR" },
          ],
        },
        {
          slug: "whale", label: "鲸鱼钱流", views: [
            { slug: "onchain-whales", label: "巨鲸转账" },
            { slug: "onchain-whales-kol", label: "KOL 信号" },
          ],
        },
      ]}
      defaultTab="met"
    />
  );
}
