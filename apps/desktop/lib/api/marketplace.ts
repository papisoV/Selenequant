"use client";

import { api, apiPost } from "@/lib/api-client";

const BASE = "/api/v1/marketplace";

export interface MarketplaceListing {
  id: string;
  seller_user_id?: string;
  strategy_config?: Record<string, unknown>;
  price?: number;
  tags?: string[];
  description?: string;
  status?: "draft" | "published" | string;
  purchase_count?: number;
  rating_avg?: number;
  created_at?: number;
  [k: string]: unknown;
}

export async function listListings(opts?: { tag?: string; search?: string; offset?: number; limit?: number }): Promise<MarketplaceListing[]> {
  const params = new URLSearchParams();
  if (opts?.tag) params.set("tag", opts.tag);
  if (opts?.search) params.set("search", opts.search);
  if (opts?.offset != null) params.set("offset", String(opts.offset));
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return await api<MarketplaceListing[]>(`${BASE}/listings${qs ? `?${qs}` : ""}`);
}

export async function buyListing(listingId: string): Promise<void> {
  await apiPost(`${BASE}/listings/${listingId}/buy`, {});
}

export async function publishListing(listingId: string): Promise<void> {
  await apiPost(`${BASE}/listings/${listingId}/publish`, {});
}
