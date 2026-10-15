"use client";

import { useState } from "react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import {
  putExchangeSettings,
  testExchangeCredentials,
  type ExchangeProbeResult,
  type ExchangeSettings,
} from "@/lib/api/settings";

// GET /api/v1/settings → data.exchange 段(settings_service.get_settings 注入)。
// 此前该段只走 set-page 的通用只读分组,api_key_hint 渲染成裸英文 key,
// 用户看得见但读不懂;且 POST /settings/exchange/test 凭证探针前端零消费。
interface SettingsData {
  exchange?: ExchangeSettings;
}

const PROVIDERS = ["binance", "okx"] as const;
const MARKET_TYPES = ["spot", "futures"] as const;

const MARKET_TYPE_LABELS: Record<string, string> = {
  spot: "现货",
  futures: "合约",
};

export function ExchangeSettingsPanel() {
  const { useApi } = useApiPage();
  const { data, mutate } = useApi<SettingsData | null>("/api/v1/settings");
  const exchange = data?.exchange;
  const ready = data !== null;

  const [provider, setProvider] = useState("");
  const [marketType, setMarketType] = useState("");
  const [testnet, setTestnet] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  const [probing, setProbing] = useState(false);
  const [probe, setProbe] = useState<ExchangeProbeResult | null>(null);
  const [probeErr, setProbeErr] = useState<string | null>(null);

  // 后端数据到达/更新时同步表单 —— render-adjust 模式(React 官方替代 set-state-in-effect)
  const [prevExchange, setPrevExchange] = useState<ExchangeSettings | undefined>(
    undefined,
  );
  if (exchange !== prevExchange) {
    setPrevExchange(exchange);
    setProvider(exchange?.provider ?? "");
    setMarketType(exchange?.market_type ?? "");
    setTestnet(!!exchange?.testnet);
    setApiKey("");
    setApiSecret("");
    setClearKey(false);
  }

  const configured = !!exchange?.api_key_configured;
  const secretConfigured = !!exchange?.api_secret_configured;
  const hint = exchange?.api_key_hint?.trim() ?? "";

  const handleSave = async () => {
    if (!ready) {
      setSavedMsg("数据加载中");
      return;
    }
    setSaving(true);
    setSavedMsg(null);
    try {
      const cfg: Record<string, unknown> = {};
      if (provider.trim()) cfg.provider = provider.trim();
      if (marketType.trim()) cfg.market_type = marketType.trim();
      cfg.testnet = testnet;
      // key/secret 留空不传(不覆盖存量);填新值才写,点清除才从钥匙串删
      if (apiKey.trim()) cfg.api_key = apiKey.trim();
      else if (clearKey) cfg.clear_api_key = true;
      if (apiSecret.trim()) cfg.api_secret = apiSecret.trim();
      await putExchangeSettings(cfg);
      setApiKey("");
      setApiSecret("");
      setClearKey(false);
      setProbe(null);
      setProbeErr(null);
      setSavedMsg("已保存");
      mutate();
    } catch {
      setSavedMsg("保存失败");
    } finally {
      setSaving(false);
      setTimeout(() => setSavedMsg(null), 2000);
    }
  };

  const handleProbe = async () => {
    setProbing(true);
    setProbe(null);
    setProbeErr(null);
    try {
      setProbe(await testExchangeCredentials());
    } catch (e) {
      // 后端把"凭证被拒"和"网络不可达"都塞进 HTTP error payload,这里取最可读的一句
      const msg = e instanceof Error ? e.message : String(e);
      setProbeErr(msg || "测试失败");
    } finally {
      setProbing(false);
    }
  };

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <div className="set-group-title text-[10px] font-medium text-gold mb-1.5">
        交易所凭证
      </div>

      {/* 当前生效的 key:后端 api_key_hint 首尾明文。此前零消费——
          用户无法确认系统里存的是哪一个 key,换 key 后只能盲信。 */}
      <div className="mb-1.5 flex items-center justify-between rounded border border-border-soft bg-surface-3 px-1.5 py-1">
        <span className="text-[9px] text-ink-3">当前生效 Key</span>
        <span className="font-mono text-[9px] text-ink">
          {configured ? (
            <>
              {hint || exchange?.api_key_masked || "••••••••"}
              <span className="ml-1 text-ink-4">
                {secretConfigured ? "(密钥已配对)" : "(缺 Secret)"}
              </span>
            </>
          ) : (
            <span className="text-amber-500">未配置</span>
          )}
        </span>
      </div>

      <div className="space-y-1.5">
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">交易所</span>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="h-5 w-36 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
          >
            <option value="">—</option>
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">市场类型</span>
          <div className="flex gap-1">
            {MARKET_TYPES.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMarketType(m)}
                className={`rounded border px-1.5 py-0.5 text-[9px] ${
                  marketType === m
                    ? "border-gold text-gold bg-surface-3"
                    : "border-border-soft text-ink-4 bg-surface-2"
                }`}
              >
                {MARKET_TYPE_LABELS[m] ?? m}
              </button>
            ))}
          </div>
        </div>
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">
            测试网
            <span className="text-ink-4">（不会真下单）</span>
          </span>
          <button
            type="button"
            onClick={() => setTestnet(!testnet)}
            className={`rounded border px-2 py-0.5 text-[9px] font-mono ${
              testnet
                ? "border-gold text-gold bg-surface-3"
                : "border-border-soft text-ink-4 bg-surface-2"
            }`}
          >
            {testnet ? "ON" : "OFF"}
          </button>
        </div>
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">API Key</span>
          <span className="flex items-center gap-1">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value);
                if (e.target.value) setClearKey(false);
              }}
              placeholder={configured ? "留空保持不变" : "未配置"}
              className="h-5 w-28 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
            />
            {configured && (
              <button
                type="button"
                onClick={() => {
                  setClearKey(true);
                  setApiKey("");
                }}
                className="rounded border border-border-soft bg-surface-3 px-1.5 py-0.5 text-[9px] text-red-400"
                title="从系统钥匙串清除已存 key"
              >
                清除
              </button>
            )}
          </span>
        </div>
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">API Secret</span>
          <input
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            placeholder={secretConfigured ? "留空保持不变" : "未配置"}
            className="h-5 w-36 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
          />
        </div>
      </div>

      <div className="flex items-center gap-1.5 mt-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !ready}
          className="flex-1 rounded border border-gold bg-surface-3 px-2 py-1 text-[9px] text-gold disabled:opacity-50"
        >
          {saving ? "保存中..." : "保存"}
        </button>
        <button
          type="button"
          onClick={handleProbe}
          disabled={probing || !configured || !secretConfigured}
          className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[9px] text-ink-3 disabled:opacity-50"
          title={
            configured && secretConfigured
              ? "用已存凭证打一次只读探针(后端真调交易所 account 接口)"
              : "先保存 API Key 与 Secret"
          }
        >
          {probing ? "测试中..." : "测试连接"}
        </button>
        {savedMsg && <span className="text-[9px] text-ink-3">{savedMsg}</span>}
      </div>

      {probe && (
        <div className="mt-1.5 rounded border border-green-500/40 bg-green-500/10 px-1.5 py-1 text-[9px] text-green-500">
          凭证可用 · {probe.provider} / {probe.market_type}
          {probe.testnet ? " · 测试网" : ""}
          {probe.account_summary
            ? ` · ${Object.entries(probe.account_summary)
                .map(([k, v]) => `${k}=${String(v)}`)
                .join(" ")}`
            : ""}
        </div>
      )}
      {probeErr && (
        <div className="mt-1.5 rounded border border-red-500/40 bg-red-500/10 px-1.5 py-1 text-[9px] text-red-400 break-all">
          {probeErr}
        </div>
      )}
    </div>
  );
}
