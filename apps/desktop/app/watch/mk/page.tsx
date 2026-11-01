"use client";

import { BlockComposer } from "@/components/shared/block-composer";

// mk 市场结构 — 判定表 r5:9 条收 6(block + 5 tab,demo r5 态 mk 面)
// block=市场总览(markets) · 体制/聚合盘口/合并盘口/成交量分布各 1 tab
// 跨市场 4 视图(ashare-deep/ashare-regime/taiwan/commodities)收 1 tab seg,不占 tab 名额
export default function Page() {
  return (
    <BlockComposer
      panels={[
        { slug: "markets", label: "市场总览" },
      ]}
      tabs={[
        { slug: "regime", label: "市场体制" },
        { slug: "agg", label: "聚合盘口" },
        { slug: "derivatives-merged-book", label: "合并盘口" },
        { slug: "derivatives-vpvr", label: "成交量分布" },
        {
          slug: "xm", label: "跨市场", views: [
            { slug: "ashare-deep", label: "A股深度" },
            { slug: "ashare-regime", label: "A股体制" },
            { slug: "taiwan", label: "台股" },
            { slug: "commodities", label: "大宗外汇" },
          ],
        },
      ]}
      defaultTab="regime"
    />
  );
}
