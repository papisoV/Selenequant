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

// PA 心跳(settings_service.get_settings 注入的 data.pa_heartbeat 段)。
// 后端调度器常驻、每 tick 重读本段 —— 保存后无需重启即生效。
// daily_max = 0 表示不设每日上限(本机默认),不是"零次"。
export interface PaHeartbeatSettings {
  enabled: boolean;
  daily_max: number;
  interval: string;
  hour_utc: number;
}

export async function putPaHeartbeatSettings(
  cfg: Partial<PaHeartbeatSettings>,
): Promise<void> {
  await apiPut("/api/v1/settings", { pa_heartbeat: cfg });
}

// Daily LLM token ceiling. 0 = no cap (the default on this machine).
// Metered in tokens, not dollars: the provider reports real usage, while the
// repo has no authoritative price table — a dollar figure would be invented.
export interface LLMBudgetSettings {
  daily_tokens: number;
}

export async function putLLMBudgetSettings(
  cfg: Partial<LLMBudgetSettings>,
): Promise<void> {
  await apiPut("/api/v1/settings", { llm_budget: cfg });
}

export interface LLMUsageState {
  day: string | null;
  tokens_today: number;
  prompt_tokens: number;
  completion_tokens: number;
  calls_today: number;
  daily_tokens: number;
  exceeded: boolean;
  remaining: number | null;
  by_source: Record<string, { calls: number; total_tokens: number }>;
}

export async function getLLMUsage(): Promise<LLMUsageState> {
  return api<LLMUsageState>("/api/v1/settings/llm-usage");
}

// 调查 worker(investigation 相位的无人值守消费者)。默认**关** —— 它是会真
// 花钱的 LLM 消费者,默认开等于装好就开始烧。关掉后新开的调查停在
// investigation 相位,等人推进;已推进过的不会被回退。
export interface MissionWorkerSettings {
  enabled: boolean;
  interval_minutes: number;
  max_rounds_per_mission: number;
}

export async function putMissionWorkerSettings(
  cfg: Partial<MissionWorkerSettings>,
): Promise<void> {
  await apiPut("/api/v1/settings", { mission_worker: cfg });
}

// 研究自动续研(五步环 ⑤→① 的心跳)。默认**关**:这一节跨了"开调查是用户
// 的按钮"那条红线,开关本身就是授权。只对**结案派生**的问题(有
// derived_from 边)生效,且每个问题只自动续研一次。
export interface ResearchAutorenewSettings {
  enabled: boolean;
  interval_minutes: number;
  max_per_round: number;
  auto_promote: boolean;
}

export async function putResearchAutorenewSettings(
  cfg: Partial<ResearchAutorenewSettings>,
): Promise<void> {
  await apiPut("/api/v1/settings", { research_autorenew: cfg });
}

// ① 传感器(行情异动观察,只读扫描)。默认**关**:它不花 LLM 钱,但会往
// 图里写证据行;用户没打开时,图保持只有人亲手产生的内容。打开后它绝不
// 开立 mission —— 只给已有 open 问题挂一条市场观察证据。
export interface ResearchSensorSettings {
  enabled: boolean;
  interval_minutes: number;
  question_limit: number;
}

export async function putResearchSensorSettings(
  cfg: Partial<ResearchSensorSettings>,
): Promise<void> {
  await apiPut("/api/v1/settings", { research_sensor: cfg });
}

// ── 代理状态 / 本机探测 / 连通性测试 ───────────────────────────────────────
// 三件事前端自己做不了:
//   1. 哪些层在生效 —— 只有后端知道启动时把什么交给了各数据源;
//   2. 本机哪个端口是活的代理 —— 只有后端能直连 127.0.0.1;
//   3. 代理通不通 —— 浏览器的 fetch 与后端的 httpx 是两套网络栈,
//      浏览器通不代表后端通,所以必须由后端代理探测。

export interface ProxyStatus {
  env: { selene_proxy_url: boolean; https_proxy: boolean };
  saved: string;
  effective: string;
  saved_masked: string;
  effective_masked: string;
  /** env:selene | env:https | saved | saved-stale | detected | direct | unknown */
  source: string;
  /** null = 后端未发布运行期值,不能断言是否需重启 */
  pending_restart: boolean | null;
  effective_known: boolean;
}

export interface ProxyCandidate {
  url: string;
  port: number;
  listening: boolean;
  reachable: boolean;
}

export interface ProxyDetection {
  candidates: ProxyCandidate[];
  recommended: string;
}

export interface ProxyTargetResult {
  name: string;
  ok: boolean;
  status: number | null;
  ms: number;
  error: string | null;
}

export interface ProxyTestResult {
  proxy: string;
  direct: boolean;
  results: ProxyTargetResult[];
  reachable: number;
  total: number;
}

export async function fetchProxyStatus(): Promise<ProxyStatus> {
  return await api<ProxyStatus>("/api/v1/settings/network/status");
}

export async function detectLocalProxies(): Promise<ProxyDetection> {
  return await apiPost<ProxyDetection>("/api/v1/settings/network/detect");
}

/** proxyUrl 留空 = 测直连(对照组,用于区分"代理坏了"和"本来就不通")。 */
export async function testProxyConnectivity(
  proxyUrl: string,
): Promise<ProxyTestResult> {
  return await apiPost<ProxyTestResult>("/api/v1/settings/network/test", {
    proxy_url: proxyUrl,
  });
}
