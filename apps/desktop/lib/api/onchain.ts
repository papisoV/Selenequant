import { apiDirect } from "@/lib/api-client";

// 链上情报 API — P3 新增,全部经 apiDirect 直连(禁 api()/apiPost() 代理路径)
// 数据层: /api/v1/onchain/{whales,metrics,smart-money} + /api/v1/intelligence/{news,social}

export interface WhaleTransfer {
  hash?: string;
  token?: string;
  symbol?: string;
  from?: string;
  to?: string;
  value_usd?: number;
  amount?: number;
  chain?: string;
  direction?: "in" | "out" | "internal";
  confirmations?: number;
  ts?: number;
  [key: string]: unknown;
}

export interface WhaleTransfersResult {
  transfers?: WhaleTransfer[];
  count?: number;
  message?: string;
  [key: string]: unknown;
}

export interface OnchainMetric {
  value?: number;
  change_24h?: number;
  [key: string]: unknown;
}

export interface OnchainMetricsData {
  symbol?: string;
  active_addresses?: OnchainMetric;
  nvt_ratio?: OnchainMetric;
  sopr?: OnchainMetric;
  [key: string]: unknown;
}

export interface SmartMoneyMovement {
  ts?: string | number;
  symbol?: string;
  direction?: string;
  value_usd?: number;
  label?: string;
  confidence?: number;
  hash?: string;
  [key: string]: unknown;
}

export interface SmartMoneyData {
  symbol?: string;
  movements?: SmartMoneyMovement[];
  count?: number;
  timestamp?: number;
  [key: string]: unknown;
}

export interface KolSignal {
  id?: string;
  author?: string;
  handle?: string;
  content?: string;
  summary?: string;
  symbol?: string;
  sentiment?: "pos" | "neg" | "neutral";
  ts?: number;
  source?: string;
  [key: string]: unknown;
}

/** 兼容两种响应形态:裸数组或 {list:[...]}(envelope 已在 apiDirect 内解开) */
function asList<T>(d: unknown): T[] {
  if (Array.isArray(d)) return d as T[];
  if (d && typeof d === "object" && Array.isArray((d as { list?: unknown }).list)) {
    return (d as { list: T[] }).list;
  }
  return [];
}

/** 巨鲸转账列表(直连解 envelope) */
export async function getWhaleTransfers(): Promise<WhaleTransfersResult> {
  const data = await apiDirect<unknown>("/api/v1/onchain/whales");
  if (Array.isArray(data)) return { transfers: data as WhaleTransfer[] };
  // 后端现为占位实现,返回 {symbol, movements: [], count: 0, message}。
  // 归一化 movements → transfers,避免消费侧读空键。
  const obj = (data ?? {}) as WhaleTransfersResult & { movements?: WhaleTransfer[] };
  const list = obj.transfers ?? obj.movements ?? [];
  return { ...obj, transfers: list };
}

/** 链上指标(直连,按 symbol) */
export async function getOnchainMetrics(symbol: string): Promise<OnchainMetricsData | null> {
  return apiDirect<OnchainMetricsData | null>(
    `/api/v1/onchain/metrics?symbol=${encodeURIComponent(symbol)}`,
  );
}

/** 聪明钱动向(直连) */
export async function getSmartMoney(): Promise<SmartMoneyData | null> {
  return apiDirect<SmartMoneyData | null>("/api/v1/onchain/smart-money");
}

/** KOL/推文信号(直连;news + social 两路合并,单路失败不拖垮整体) */
export async function getKolSignals(): Promise<KolSignal[]> {
  const results = await Promise.allSettled([
    apiDirect<unknown>("/api/v1/intelligence/news/token-signals"),
    apiDirect<unknown>("/api/v1/intelligence/social/trending"),
  ]);
  const signals: KolSignal[] = [];
  for (const r of results) {
    if (r.status === "fulfilled") {
      signals.push(...asList<KolSignal>(r.value));
    }
  }
  return signals;
}
