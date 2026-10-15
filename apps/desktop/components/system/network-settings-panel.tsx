"use client";

import { useState } from "react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { putNetworkSettings } from "@/lib/api/settings";

// GET /api/v1/settings → data.network 段（settings_service 注入）。
// 后端把 proxy_url 同时写进 keyring 与 settings.json；启动时 main.py 读
// settings.json 那一份来构造各数据源，所以这里保存的代理在**下次启动**生效。
interface SettingsData {
  network?: { proxy_url?: string };
}

export function NetworkSettingsPanel() {
  const { useApi } = useApiPage();
  const { data, mutate } = useApi<SettingsData | null>("/api/v1/settings");
  const saved = data?.network?.proxy_url ?? "";
  const ready = data !== null;

  const [proxyUrl, setProxyUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // 后端数据到达/更新时同步表单 —— render-adjust 模式（同 LlmSettingsPanel）
  const [prevSaved, setPrevSaved] = useState<string | undefined>(undefined);
  if (saved !== prevSaved) {
    setPrevSaved(saved);
    setProxyUrl(saved);
  }

  const handleSave = async () => {
    if (!ready) {
      setMsg("数据加载中");
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await putNetworkSettings({ proxy_url: proxyUrl.trim() });
      setMsg(proxyUrl.trim() ? "已保存，重启后生效" : "已清除");
      mutate();
    } catch {
      setMsg("保存失败");
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(null), 3000);
    }
  };

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <div className="set-group-title text-[10px] font-medium text-gold mb-1.5">
        网络代理
      </div>
      <div className="space-y-1.5">
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[9px]">代理地址</span>
          <input
            value={proxyUrl}
            onChange={(e) => setProxyUrl(e.target.value)}
            placeholder="http://127.0.0.1:7890 或 socks5://127.0.0.1:1080"
            className="h-5 w-52 rounded border border-border-soft bg-surface-3 px-1 text-[9px] font-mono text-ink"
          />
        </div>
        <div className="text-ink-4 text-[9px] leading-snug">
          留空 = 直连。国内访问交易所/GitHub/DeFi Llama 时常需要代理；
          保存后下次启动生效。环境变量 <span className="font-mono">SELENE_PROXY_URL</span> 优先级更高。
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
        {msg && <span className="text-[9px] text-ink-3">{msg}</span>}
      </div>
    </div>
  );
}
