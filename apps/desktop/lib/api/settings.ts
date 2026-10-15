"use client";

import { api, apiPost, apiPut } from "@/lib/api-client";

export interface TieredTrailingConfig {
  tiered_hard_stop_pct: number;
  tiered_low_tier_threshold: number;
  tiered_first_tier_threshold: number;
  tiered_second_tier_threshold: number;
  tiered_low_tier_drop_pct: number;
  tiered_first_tier_drop_pct: number;
  tiered_second_tier_drop_pct: number;
  tiered_min_emit_interval_seconds: number;
}

export const TIERED_TRAILING_DEFAULTS: TieredTrailingConfig = {
  tiered_hard_stop_pct: 0.02,
  tiered_low_tier_threshold: 0.015,
  tiered_first_tier_threshold: 0.03,
  tiered_second_tier_threshold: 0.06,
  tiered_low_tier_drop_pct: 0.5,
  tiered_first_tier_drop_pct: 0.3,
  tiered_second_tier_drop_pct: 0.2,
  tiered_min_emit_interval_seconds: 30.0,
};

export async function putTieredConfig(
  cfg: Partial<TieredTrailingConfig>,
): Promise<void> {
  await apiPut("/api/v1/settings", { risk: cfg });
}

// LLM runtime config (Agent). Backend contract: app/api/v1/settings.py
// LLMSettingsUpdate — non-sensitive fields go to keyring (clear=false);
// api_key supports clear_api_key; PUT containing llm triggers agent runtime refresh.
export interface LLMSettingsConfig {
  provider?: string;
  base_url?: string;
  model?: string;
  api_key?: string;
  clear_api_key?: boolean;
  smart_mode?: boolean;
  smart_models?: { strategy?: string; analysis?: string; quick?: string };
}

export async function putLlmSettings(
  cfg: Partial<LLMSettingsConfig>,
): Promise<void> {
  await apiPut("/api/v1/settings", { llm: cfg });
}

export interface PublicKeyJWK {
  kty?: string;
  n?: string;
  e?: string;
  kid?: string;
  alg?: string;
  use?: string;
  [k: string]: unknown;
}

export async function getPublicKey(): Promise<PublicKeyJWK> {
  return await api<PublicKeyJWK>("/api/v1/settings/public-key");
}

// 交易所凭证(settings_service.get_settings 注入的 data.exchange 段)。
// api_key_hint 是后端 _api_key_hint 产出的首尾明文段(如 "ABCD...9012"),
// 用途是让用户确认"当前生效的是哪一个 key",而非泄露凭证。
export interface ExchangeSettings {
  provider?: string;
  market_type?: string;
  testnet?: boolean;
  api_key_configured?: boolean;
  api_secret_configured?: boolean;
  api_key_masked?: string;
  api_secret_masked?: string;
  api_key_hint?: string;
}

export interface ExchangeSettingsUpdate {
  provider?: string;
  market_type?: string;
  testnet?: boolean;
  api_key?: string;
  api_secret?: string;
  clear_api_key?: boolean;
  clear_api_secret?: boolean;
}

export async function putExchangeSettings(
  cfg: ExchangeSettingsUpdate,
): Promise<void> {
  await apiPut("/api/v1/settings", { exchange: cfg });
}

export interface ExchangeProbeResult {
  tested?: boolean;
  provider?: string;
  market_type?: string;
  testnet?: boolean;
  message?: string;
  /** 交易所 account 摘要(binance: can_trade/balances;okx: details) */
  account_summary?: Record<string, unknown>;
  [k: string]: unknown;
}

/** 用已存凭证打一次只读探针(后端真调交易所 account 接口),验证凭证是否可用。 */
export async function testExchangeCredentials(): Promise<ExchangeProbeResult> {
  return await apiPost<ExchangeProbeResult>("/api/v1/settings/exchange/test");
}

// 网络代理(settings_service.get_settings 注入的 data.network 段)。
// 后端把 proxy_url 落进 settings.json —— 启动时 main.py 先读它再建各数据源,
// 所以这里的填写会在下一次启动生效(本进程内已建立的连接不会重连)。
export interface NetworkSettings {
  proxy_url?: string;
}

export async function putNetworkSettings(
  cfg: NetworkSettings,
): Promise<void> {
  await apiPut("/api/v1/settings", { network: cfg });
}
