"use client";

import { BlockComposer } from "@/components/shared/block-composer";

// 期权 hub — 判定表 r5:4 条收 1 block + 2 tab(vol_surface 嫌疑犯默认并入方案)
// block=期权总览 · 合约列表/行情/波动率曲面 各 1 tab;合约详情走 drawer(options-tickers-instrument-name 保留对象类)
export default function Page() {
  return (
    <BlockComposer
      panels={[
        { slug: "options-summary", label: "期权总览" },
      ]}
      tabs={[
        { slug: "options-instruments", label: "合约列表" },
        { slug: "options-tickers", label: "行情" },
        { slug: "vol_surface", label: "波动率曲面" },
      ]}
      defaultTab="options-instruments"
    />
  );
}
