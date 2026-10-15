// 5 市场数据源真值映射（后端 fetcher 是代码写死的架构事实，前端静态镜像零漂移）。
// 证据：crypto=markets.py _resolve_source(binance/okx, futures 默认)；us=us_radar.py:46
// YahooFinanceClient；ashare=ashare/ths_data.py:101 同花顺(日线主源，data_fetcher
// 优先级 THS→AKShare→sina；东财只用于资金流/行业/市值)；commodities=commodities-fx/
// data_fetcher.py:78+ sina；taiwan=chart.py:320 FinMind。
export interface SourceInfo {
  venue: string;
  kind: string;
}

const REGISTRY: Record<string, SourceInfo> = {
  crypto: { venue: "Binance", kind: "永续" },
  us: { venue: "Yahoo", kind: "股票" },
  ashare: { venue: "同花顺", kind: "股票" },
  commodities: { venue: "新浪", kind: "期货" },
  taiwan: { venue: "FinMind", kind: "股票" },
};

export function sourceInfo(market: string): SourceInfo {
  return (
    REGISTRY[market] ?? { venue: market, kind: "" }
  );
}

export function sourceLabel(market: string): string {
  const { venue, kind } = sourceInfo(market);
  return kind ? `${venue} · ${kind}` : venue;
}
