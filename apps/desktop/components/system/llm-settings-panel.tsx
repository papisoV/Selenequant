"use client";

import { useState } from "react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { putLlmSettings } from "@/lib/api/settings";
import { useUiText } from "@/components/i18n/use-ui-text"

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
  const tr = useUiText()

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
      <h2 className="set-group-title text-[12px] font-medium text-gold mb-1.5">
        {tr("LLM / Agent 配置")}
      </h2>
      <div className="space-y-1.5">
        <div className="set-row flex items-center gap-2">
          <label htmlFor="llm-provider" className="set-key text-ink-3 text-[11px] shrink-0">Provider</label>
          <input
            id="llm-provider"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            placeholder="openai"
            className="set-input flex-1 min-w-0"
          />
        </div>
        <div className="set-row flex items-center gap-2">
          <label htmlFor="llm-base-url" className="set-key text-ink-3 text-[11px] shrink-0">Base URL</label>
          <input
            id="llm-base-url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.openai.com/v1"
            className="set-input flex-1 min-w-0"
          />
        </div>
        <div className="set-row flex items-center gap-2">
          <label htmlFor="llm-model" className="set-key text-ink-3 text-[11px] shrink-0">Model</label>
          <input
            id="llm-model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={provider || "gpt-4o"}
            className="set-input flex-1 min-w-0"
          />
        </div>
        <div className="set-row flex items-center gap-2">
          <label htmlFor="llm-api-key" className="set-key text-ink-3 text-[11px] shrink-0">API Key</label>
          <span className="flex flex-1 min-w-0 items-center gap-1">
            <input
              id="llm-api-key"
              type="password"
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value);
                if (e.target.value) setClearKey(false);
              }}
              placeholder={
                llm?.api_key_configured ? tr("•••••••• 留空保持不变") : tr("未配置")
              }
              className="set-input flex-1 min-w-0"
            />
            {llm?.api_key_configured && (
              <button
                type="button"
                onClick={() => {
                  setClearKey(true);
                  setApiKey("");
                }}
                className="set-btn rounded border border-border-soft bg-surface-3 px-1.5 text-[11px] text-red-400"
                title={tr("从系统钥匙串清除已存 key")}
              >
                {tr("清除")}
              </button>
            )}
          </span>
        </div>
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[11px]">{tr("智能模型切换")}</span>
          <button
            type="button"
            onClick={() => setSmartMode(!smartMode)}
            className={`set-btn set-val font-mono px-2 rounded border ${
              smartMode
                ? "border-gold text-gold bg-surface-3"
                : "border-border-soft text-ink-4 bg-surface-2"
            }`}
            title={tr("按问题类型切换模型（strategy/analysis/quick）")}
          >
            {smartMode ? "ON" : "OFF"}
          </button>
        </div>
        {smartMode && (
          <div className="space-y-1.5 pt-1">
            {SMART_MODEL_FIELDS.map((f) => (
              <div
                key={f.key}
                className="set-row flex items-center gap-2"
              >
                <label
                  htmlFor={`llm-smart-${f.key}`}
                  className="text-ink-3 text-[11px] shrink-0"
                >
                  {tr(f.label)}
                  <span className="text-ink-4">（{tr(f.hint)}）</span>
                </label>
                <input
                  id={`llm-smart-${f.key}`}
                  value={smartModels[f.key]}
                  onChange={(e) =>
                    setSmartModels((prev) => ({ ...prev, [f.key]: e.target.value }))
                  }
                  placeholder={tr("模型名")}
                  className="set-input flex-1 min-w-0"
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
          className="set-btn flex-1 rounded border border-gold bg-surface-3 px-2 text-[11px] text-gold disabled:opacity-50"
        >
          {saving ? tr("保存中...") : tr("保存")}
        </button>
        {savedMsg && <span className="text-[11px] text-ink-3">{tr(savedMsg)}</span>}
      </div>
    </div>
  );
}
