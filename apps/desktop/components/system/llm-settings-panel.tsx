"use client";

import { useState } from "react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { putLlmSettings } from "@/lib/api/settings";

// GET /api/v1/settings → data.llm 结构（settings_service.get_settings 注入）
interface LlmBackendShape {
  provider?: string;
  base_url?: string;
  model?: string;
  api_key_configured?: boolean;
  api_key_masked?: string;
  smart_mode?: boolean;
  smart_models?: { strategy?: string; analysis?: string; quick?: string };
}

interface SettingsData {
  llm?: LlmBackendShape;
}

const SMART_MODEL_FIELDS: {
  key: "strategy" | "analysis" | "quick";
  label: string;
  hint: string;
}[] = [
  { key: "strategy", label: "策略", hint: "策略/回测/起草类问题" },
  { key: "analysis", label: "分析", hint: "行情/趋势/分析类问题" },
  { key: "quick", label: "快速", hint: "其他常规问题" },
];

export function LlmSettingsPanel() {
  const { useApi } = useApiPage();
  const { data, mutate } = useApi<SettingsData | null>("/api/v1/settings");
  const llm = data?.llm;
  const ready = data !== null;

  const [provider, setProvider] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [smartMode, setSmartMode] = useState(false);
  const [smartModels, setSmartModels] = useState({
    strategy: "",
    analysis: "",
    quick: "",
  });
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  // 后端数据到达/更新时同步表单 —— render-adjust 模式（React 官方替代 set-state-in-effect）
  const [prevLlm, setPrevLlm] = useState<LlmBackendShape | undefined>(undefined);
  if (llm !== prevLlm) {
    setPrevLlm(llm);
    setProvider(llm?.provider ?? "");
    setBaseUrl(llm?.base_url ?? "");
    setModel(llm?.model ?? "");
    setApiKey("");
    setClearKey(false);
    setSmartMode(!!llm?.smart_mode);
    setSmartModels({
      strategy: llm?.smart_models?.strategy ?? "",
      analysis: llm?.smart_models?.analysis ?? "",
      quick: llm?.smart_models?.quick ?? "",
    });
  }

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
      if (baseUrl.trim()) cfg.base_url = baseUrl.trim();
      if (model.trim()) cfg.model = model.trim();
      // api_key 留空不传（不覆盖存量 key）；只有填新值或点清除才动 keyring
      if (apiKey.trim()) cfg.api_key = apiKey.trim();
      else if (clearKey) cfg.clear_api_key = true;
      cfg.smart_mode = smartMode;
      cfg.smart_models = smartModels;
      await putLlmSettings(cfg);
      setApiKey("");
      setClearKey(false);
      setSavedMsg("已保存");
      mutate();
    } catch {
      setSavedMsg("保存失败");
    } finally {
      setSaving(false);
      setTimeout(() => setSavedMsg(null), 2000);
    }
  };

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <div className="set-group-title text-[10px] font-medium text-gold mb-1.5">
        LLM / Agent 配置
      </div>
      <div className="space-y-1.5">
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">Provider</span>
          <input
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            placeholder="openai"
            className="h-5 w-36 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
          />
        </div>
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">Base URL</span>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.openai.com/v1"
            className="h-5 w-36 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
          />
        </div>
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">Model</span>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={provider || "gpt-4o"}
            className="h-5 w-36 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
          />
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
              placeholder={
                llm?.api_key_configured ? "•••••••• 留空保持不变" : "未配置"
              }
              className="h-5 w-28 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
            />
            {llm?.api_key_configured && (
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
          <span className="set-key text-ink-3 text-[9px]">智能模型切换</span>
          <button
            type="button"
            onClick={() => setSmartMode(!smartMode)}
            className={`set-val font-mono px-2 py-0.5 rounded border ${
              smartMode
                ? "border-gold text-gold bg-surface-3"
                : "border-border-soft text-ink-4 bg-surface-2"
            }`}
            title="按问题类型切换模型（strategy/analysis/quick）"
          >
            {smartMode ? "ON" : "OFF"}
          </button>
        </div>
        {smartMode && (
          <div className="space-y-1.5 pt-1">
            {SMART_MODEL_FIELDS.map((f) => (
              <div
                key={f.key}
                className="set-row flex items-center justify-between gap-2"
              >
                <span className="text-ink-3 text-[9px]">
                  {f.label}
                  <span className="text-ink-4">（{f.hint}）</span>
                </span>
                <input
                  value={smartModels[f.key]}
                  onChange={(e) =>
                    setSmartModels((prev) => ({ ...prev, [f.key]: e.target.value }))
                  }
                  placeholder="模型名"
                  className="h-5 w-32 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
                />
              </div>
            ))}
          </div>
        )}
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
        {savedMsg && <span className="text-[9px] text-ink-3">{savedMsg}</span>}
      </div>
    </div>
  );
}
