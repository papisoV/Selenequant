"use client";

import { BlockComposer } from "@/components/shared/block-composer";

// 交易运维 hub — 判定表 r5:tof 正典 + tof-all/tof-current(K39/K40)作 tab
export default function Page() {
  return (
    <BlockComposer
      panels={[]}
      tabs={[
        { slug: "tof-current", label: "当前信号" },
        { slug: "tof-all", label: "全量" },
      ]}
      defaultTab="tof-current"
    />
  );
}
