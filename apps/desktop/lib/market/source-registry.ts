// 5 市场数据源真值映射（后端 fetcher 是代码写死的架构事实，前端静态镜像零漂移）。
// 证据：crypto=markets.py _resolve_source(binance/okx, futures 默认)；us=us_radar.py:46
// YahooFinanceClient；ashare=ashare/ths_data.py:101 同花顺(日线主源，data_fetcher
// 优先级 THS→AKShare→sina；东财只用于资金流/行业/市值)；commodities=commodities-fx/
// data_fetcher.py:78+ sina；taiwan=chart.py:320 FinMind。
import { identityTranslate, type Translate } from "@/lib/i18n/types";

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

/**
 * 「数据源 · 品种」徽章文案。
 *
 * 拼好的整串不是词表键,所以逐段过 `tr`:`kind`(永续/股票/期货)是展示用
 * 品类词,`venue` 里同花顺/新浪也需随语言变(Binance/Yahoo/FinMind 恒等)。
 * 不传 `tr` 时恒等返回,既有单参调用(app 外脚本/测试)行为不变。
 */
export function sourceLabel(market: string, tr: Translate = identityTranslate): string {
  const { venue, kind } = sourceInfo(market);
  return kind ? `${tr(venue)} · ${tr(kind)}` : tr(venue);
}
