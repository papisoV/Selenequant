// lib/assets/types.ts — 资产聚合壳类型与交易所 tab 定义

export type AssetMode = "empty" | "demo" | "live";

// 对齐 balance-card TradingBalance{asset,exchange,free,locked},live 管道落地可直写
export interface AssetBalance {
  exchange: string;
  asset: string;
  free: number;
  locked: number;
}

export interface ExchangeTab {
  key: string;
  label: string;
}

// 对齐现存 exchange 词汇(binance/okx/...),key 直接透传给 AssetBalance.exchange
export const EXCHANGE_TABS: ExchangeTab[] = [
  { key: "binance", label: "币安" },
  { key: "okx", label: "OKX" },
  { key: "web3", label: "Web3钱包" },
];
