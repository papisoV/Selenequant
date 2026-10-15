// 每市场支持的周期档位 + TF 展示文案。
// 后端 /chart/capabilities 是权威来源；此处为 offline 兜底（useApi 失败返回 null 时用）。
// crypto 档位仅用于前端渲染，实际数据走 WS 实时分支（Decision A）。

export type ChartCapabilities = Record<string, string[]>;

export const DEFAULT_CHART_CAPABILITIES: ChartCapabilities = {
  us: ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"],
  commodities: ["1m", "5m", "15m", "30m", "1h", "1d"],
  ashare: ["1d"],
  taiwan: ["1d"],
  crypto: ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"],
};

export const TF_SET = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];

export const TF_LABELS: Record<string, string> = {
  "1m": "1m",
  "5m": "5m",
  "15m": "15m",
  "30m": "30m",
  "1h": "1H",
  "4h": "4H",
  "1d": "1D",
  "1w": "1W",
};

// null-safe：caps 未加载（useApi 失败）时回退默认档位
export function supportedIntervals(market: string, caps?: ChartCapabilities | null): string[] {
  return caps?.[market] ?? DEFAULT_CHART_CAPABILITIES[market] ?? [];
}
