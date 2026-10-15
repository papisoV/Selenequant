export interface FundingRateRow {
  symbol: string;
  exchange?: string;
  /** 8h 资金费率(小数:0.0001 = 0.01%) */
  rate: number;
}

export interface ArbOpportunity {
  symbol: string;
  exchange?: string;
  rate: number;
  /** 年化 %(正=做空吃费,负=做多吃费) */
  annualizedPct: number;
  /** 吃 funding 的方向 */
  side: "long" | "short";
  /** 是否达开仓阈值 */
  openable: boolean;
  label: string;
}

/** 开仓阈值:年化 ≥ 10% 才值得开 */
export const FUNDING_THRESHOLD_APR = 10;

/** 8h 费率 → 年化 %(3 期/天 × 365) */
export function annualizeFunding(rate: number): number {
  return rate * 3 * 365 * 100;
}

/** 正费率 → 做空吃费(多头付给你);负费率 → 做多吃费 */
export function fundingSide(rate: number): "long" | "short" {
  return rate >= 0 ? "short" : "long";
}

export function isArbOpenable(
  annualizedPct: number,
  thresholdApr = FUNDING_THRESHOLD_APR,
): boolean {
  return Math.abs(annualizedPct) >= thresholdApr;
}

/** 资金费率套利扫描:年化从高到低排序,标记可开仓机会 */
export function fundingRateSpread(
  rates: FundingRateRow[],
  thresholdApr = FUNDING_THRESHOLD_APR,
): ArbOpportunity[] {
  return rates
    .map((r) => {
      const annualizedPct = annualizeFunding(r.rate);
      const side = fundingSide(r.rate);
      return {
        symbol: r.symbol,
        exchange: r.exchange,
        rate: r.rate,
        annualizedPct: round2(annualizedPct),
        side,
        openable: isArbOpenable(annualizedPct, thresholdApr),
        label: `${side === "short" ? "做空" : "做多"}吃费 ${round2(annualizedPct)}%`,
      };
    })
    .sort((a, b) => Math.abs(b.annualizedPct) - Math.abs(a.annualizedPct));
}

/** 期现基差 %:(futures - spot) / spot */
export function basisSpread(spot: number, futures: number): number {
  if (!spot || spot <= 0) return 0;
  return ((futures - spot) / spot) * 100;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export const DEMO_FUNDING_RATES: FundingRateRow[] = [
  { symbol: "SOL-USDT-SWAP", exchange: "okx", rate: 0.000155 },
  { symbol: "BTC-USDT-SWAP", exchange: "binance", rate: 0.000101 },
  { symbol: "ETH-USDT-SWAP", exchange: "binance", rate: 0.000062 },
  { symbol: "XRP-USDT-SWAP", exchange: "binance", rate: -0.000031 },
  { symbol: "DOGE-USDT-SWAP", exchange: "okx", rate: 0.000008 },
  { symbol: "BNB-USDT-SWAP", exchange: "binance", rate: -0.000012 },
];
