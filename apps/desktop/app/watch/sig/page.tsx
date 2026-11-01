"use client";

import { BlockComposer } from "@/components/shared/block-composer";

// sig 信号面 — 判定表 r5:9 条收 1 block + 4 tab + 1 node(demo r5 态 sig 面)
// block=新闻摘要 · 代币信号/社媒(seg 3)/脉动历史/钱流(鲸鱼流+聪明钱 seg 2) 各 1 tab
// node=新闻因子管线状态(设计正典 L104 顶部状态区) — BlockComposer node 尚未实现,本期以 tab 承载,升格待配方扩展
export default function Page() {
  return (
    <BlockComposer
      panels={[
        { slug: "intelligence-news-summary", label: "新闻摘要" },
      ]}
      tabs={[
        { slug: "intelligence-news-token-signals", label: "代币信号" },
        {
          slug: "social", label: "社媒", views: [
            { slug: "intelligence-social-sentiment", label: "情绪" },
            { slug: "intelligence-social-confirmation", label: "确认" },
            { slug: "intelligence-social-trending", label: "热趋势" },
          ],
        },
        { slug: "intelligence-market-pulse-history", label: "脉动历史" },
        {
          slug: "moneyflow", label: "钱流", views: [
            { slug: "intelligence-whale-flow", label: "鲸鱼流" },
            { slug: "smart_money", label: "聪明钱" },
          ],
        },
        { slug: "intelligence-news-factor-pipeline", label: "因子管线" },
      ]}
      defaultTab="intelligence-news-token-signals"
    />
  );
}
