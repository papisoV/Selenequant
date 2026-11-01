"use client";

import { api } from "@/lib/api-client";

const BASE = "/api/v1/polymarket";

export interface PolyOutcome {
  name?: string;
  price?: number;
  winner?: boolean;
}

export interface PolyMarketDetail {
  id?: string;
  question?: string;
  question_zh?: string;
  condition_id?: string;
  slug?: string;
  active?: boolean;
  closed?: boolean;
  volume?: number;
  liquidity?: number;
  outcomes?: PolyOutcome[];
  description?: string;
  description_zh?: string;
  end_date?: string;
  start_date?: string;
  best_bid?: number;
  best_ask?: number;
  spread?: number;
  last_trade_price?: number;
  volume_24h?: number;
  neg_risk?: boolean;
}

export interface PolyEventDetail {
  id?: string;
  title?: string;
  title_zh?: string;
  slug?: string;
  description?: string;
  category?: string;
  category_zh?: string;
  image?: string;
  icon?: string;
  active?: boolean;
  closed?: boolean;
  volume?: number;
  volume_24h?: number;
  liquidity?: number;
  start_date?: string;
  end_date?: string;
  markets?: PolyMarketDetail[];
  market_count?: number;
  description_zh?: string;
  comment_count?: number;
  created_at?: string;
  updated_at?: string;
  competitive?: number;
  enableOrderBook?: boolean;
  neg_risk?: boolean;
}

export async function getPolyEvent(eventId: string): Promise<PolyEventDetail> {
  return await api<PolyEventDetail>(`${BASE}/events/${encodeURIComponent(eventId)}`);
}
