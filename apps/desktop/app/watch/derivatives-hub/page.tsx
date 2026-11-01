"use client";

import { BlockComposer } from "@/components/shared/block-composer";

// 衍生品面 — 判定表 r5 新拼 hub:12 条 ② 收 7 tab,爆仓族内 segmented control 不占 tab 名额
// 行情链收敛单 slug(kf→kfi→mk→衍生品面);circuit: cvd-hub/gex-hub/liquidation-hub 三个旧 hub 翻②入此面
export default function Page() {
  return (
    <BlockComposer
      panels={[]}
      tabs={[
        { slug: "derivatives-funding-rates", label: "资金费率" },
        { slug: "derivatives-open-interest", label: "未平仓量" },
        {
          slug: "liquidations",
          label: "爆仓",
          views: [
            { slug: "liquidations", label: "明细" },
            { slug: "derivatives-liquidations-recent", label: "近期" },
            { slug: "derivatives-liquidation-stats", label: "统计", renderMode: "list" },
            { slug: "liqmap", label: "热区" },
            { slug: "liquidations-heatmap", label: "热力", renderMode: "list" },
            { slug: "heatmap", label: "通用热力" },
            { slug: "liqratio", label: "多空" },
          ],
        },
        {
          slug: "derivatives-large-trades-recent",
          label: "大单",
          views: [
            { slug: "derivatives-large-trades-recent", label: "Recent" },
            { slug: "derivatives-large-trades-stats", label: "Stats", renderMode: "list" },
          ],
        },
        {
          slug: "derivatives-footprint",
          label: "足迹",
          views: [
            { slug: "derivatives-footprint", label: "当前" },
            { slug: "derivatives-footprint-history", label: "历史" },
          ],
        },
        { slug: "cvd", label: "CVD" },
        { slug: "gex", label: "GEX" },
      ]}
      defaultTab="liquidations"
    />
  );
}
