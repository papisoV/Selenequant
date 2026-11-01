// lib/assets/demo-data.ts — 三交易所演示数据(前端先行兜底)
import type { AssetBalance } from "./types";

export const DEMO_PRICE_MAP: Record<string, number> = {
  USDT: 1,
  BTC: 61230,
  ETH: 3412,
  SOL: 152,
  BNB: 585,
  XRP: 0.62,
};

export const DEMO_BALANCES: AssetBalance[] = [
  // binance
  { exchange: "binance", asset: "USDT", free: 12500, locked: 500 },
  { exchange: "binance", asset: "BTC", free: 0.42, locked: 0.05 },
  { exchange: "binance", asset: "ETH", free: 3.1, locked: 0.4 },
  // okx
  { exchange: "okx", asset: "USDT", free: 8200, locked: 0 },
  { exchange: "okx", asset: "SOL", free: 180, locked: 30 },
  { exchange: "okx", asset: "BNB", free: 2.4, locked: 0 },
  // web3
  { exchange: "web3", asset: "ETH", free: 1.2, locked: 0 },
  { exchange: "web3", asset: "XRP", free: 3200, locked: 0 },
];
