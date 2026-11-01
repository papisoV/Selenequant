"use client";

import { useState } from "react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useUiText } from "@/components/i18n/use-ui-text";
import {
  detectLocalProxies,
  fetchProxyStatus,
  putNetworkSettings,
  testProxyConnectivity,
  type ProxyDetection,
  type ProxyStatus,
  type ProxyTestResult,
} from "@/lib/api/settings";

// GET /api/v1/settings → data.network 段（settings_service 注入）。
// 后端把 proxy_url 同时写进 keyring 与 settings.json。
// 保存后：AI 对话立即生效（PUT 会清掉 AgentRuntime 缓存，下一轮重建）；
// 行情/K 线等长连接是启动时建的，需重启后端。
interface SettingsData {
  network?: { proxy_url?: string };
}

interface AsyncState<T> {
  busy: boolean;
  data: T | null;
  error: string | null;
}

const IDLE: AsyncState<never> = { busy: false, data: null, error: null };

export function NetworkSettingsPanel() {
  const tr = useUiText();

  const { useApi } = useApiPage();
  const { data, mutate } = useApi<SettingsData | null>("/api/v1/settings");
  const saved = data?.network?.proxy_url ?? "";
  const ready = data !== null;

  const [proxyUrl, setProxyUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  // 后端数据到达/更新时同步表单 —— render-adjust 模式（同 LlmSettingsPanel）
  const [prevSaved, setPrevSaved] = useState<string | undefined>(undefined);
  if (saved !== prevSaved) {
    setPrevSaved(saved);
    setProxyUrl(saved);
  }

  // 运行期状态另走一条请求:它不在 settings 文档里,而是 lifespan 发布到
  // app.state 的「本进程实际用了什么」。
  const { data: status, mutate: refreshStatus } = useApi<ProxyStatus | null>(
    "/api/v1/settings/network/status",
  );

  const [detection, setDetection] = useState<AsyncState<ProxyDetection>>(IDLE);
  const [testing, setTesting] = useState<AsyncState<ProxyTestResult>>(IDLE);

  const handleSave = async () => {
    if (!ready) {
      setSavedMsg(tr("数据加载中"));
      return;
    }
    setSaving(true);
    setSavedMsg(null);
    try {
      await putNetworkSettings({ proxy_url: proxyUrl.trim() });
      // Same honesty rule as the footnote: with an env var in charge, this save
      // is inert until the variable goes away, so do not promise a restart.
      if (status?.source.startsWith("env:")) {
        setSavedMsg(tr("已保存（但环境变量仍优先生效）"));
      } else {
        setSavedMsg(proxyUrl.trim() ? tr("已保存，重启后生效") : tr("已清除"));
      }
      mutate();
      refreshStatus();
    } catch {
      setSavedMsg(tr("保存失败"));
    } finally {
      setSaving(false);
    }
  };

  const handleDetect = async () => {
    setDetection({ busy: true, data: null, error: null });
    try {
      const found = await detectLocalProxies();
      setDetection({ busy: false, data: found, error: null });
      if (found.recommended) setProxyUrl(found.recommended);
    } catch {
      setDetection({ busy: false, data: null, error: tr("探测失败") });
    }
  };

  const handleTest = async () => {
    const target = proxyUrl.trim();
    setTesting({ busy: true, data: null, error: null });
    try {
      const result = await testProxyConnectivity(target);
      setTesting({ busy: false, data: result, error: null });
    } catch {
      setTesting({ busy: false, data: null, error: tr("测试失败") });
    }
  };

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <div className="flex items-baseline justify-between gap-2 mb-1.5">
        <h2 className="set-group-title text-[12px] font-medium text-gold">
          {tr("网络代理")}
        </h2>
        <ProxyStatusBadge tr={tr} status={status} />
      </div>

      {status?.pending_restart === true && (
        <div
          role="status"
          className="mb-2 rounded border border-gold/40 bg-surface-3 px-2 py-1.5 text-[11px] leading-snug text-gold"
        >
          <span className="font-medium">{tr("已保存待重启")}</span>
          <span className="text-ink-3">
            {" — "}
            {tr(
              "运行中的后端仍在用旧设置。关闭并重新启动 Selene 后，新代理才会用于拉行情、连交易所和访问 GitHub/DeFi Llama。",
            )}
          </span>
        </div>
      )}

      <div className="space-y-1.5">
        <div className="set-row flex items-center gap-2">
          <label
            htmlFor="net-proxy-url"
            className="set-key text-ink-3 text-[11px] shrink-0"
          >
            {tr("代理地址")}
          </label>
          <input
            id="net-proxy-url"
            value={proxyUrl}
            onChange={(e) => setProxyUrl(e.target.value)}
            placeholder={tr("留空 = 直连")}
            className="set-input flex-1 min-w-0"
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <div className="text-ink-4 text-[11px] leading-snug">
          {tr(
            "留空 = 直连。国内访问交易所/GitHub/DeFi Llama 时常需要代理；AI 对话保存后立即生效，行情/K 线连接需重启后端。本机地址（如 127.0.0.1 上的 LLM 网关）永远直连，不走代理。环境变量",
          )}{" "}
          <span className="font-mono">SELENE_PROXY_URL</span> {tr("优先级更高。")}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 mt-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !ready}
          className="set-btn rounded border border-gold bg-surface-3 px-2.5 text-[11px] text-gold disabled:opacity-50"
        >
          {saving ? tr("保存中...") : tr("保存")}
        </button>
        <button
          type="button"
          onClick={handleDetect}
          disabled={detection.busy}
          className="set-btn rounded border border-border-soft bg-surface-3 px-2.5 text-[11px] text-ink-2 disabled:opacity-50"
        >
          {detection.busy ? tr("探测中…") : tr("探测本机")}
        </button>
        <button
          type="button"
          onClick={handleTest}
          disabled={testing.busy}
          className="set-btn rounded border border-border-soft bg-surface-3 px-2.5 text-[11px] text-ink-2 disabled:opacity-50"
        >
          {testing.busy ? tr("测试中…") : tr("测试连接")}
        </button>
        {savedMsg && <span className="text-[11px] text-ink-3">{savedMsg}</span>}
      </div>

      <DetectionResult tr={tr} state={detection} />
      <TestResult tr={tr} state={testing} />

      <PriorityGuide tr={tr} status={status} />

      <FootNote tr={tr} status={status} />
    </div>
  );
}

/**
 * The footnote has to agree with the badge. Under an env var a restart changes
 * nothing — the fetchers re-read the same variable — so telling the user to
 * restart would send them to a dead end. Say which variable to clear instead.
 */
function FootNote({ tr, status }: { tr: Tr; status: ProxyStatus | null }) {
  if (status?.source.startsWith("env:")) {
    const name =
      status.source === "env:selene" ? "SELENE_PROXY_URL" : "https_proxy";
    return (
      <div className="text-ink-4 text-[11px] leading-snug mt-2">
        {tr("环境变量 {name} 优先于此处的设置，重启不会改变生效的代理。", { name })}
      </div>
    );
  }
  return (
    <div className="text-ink-4 text-[11px] leading-snug mt-2">
      {tr("设置代理后请重启 Selene，数据源才会重新连接。")}
    </div>
  );
}

type Tr = (source: string, params?: Record<string, string | number>) => string;

function ProxyStatusBadge({ tr, status }: { tr: Tr; status: ProxyStatus | null }) {
  if (!status) {
    return <span className="text-[11px] text-ink-4">{tr("数据加载中")}</span>;
  }
  if (!status.effective_known) {
    return (
      <span className="text-[11px] text-ink-3">
        {tr("未知（后端未报告运行中的设置）")}
      </span>
    );
  }
  const pending = status.pending_restart === true;
  return (
    <span className="text-[11px] text-ink-3">
      <span className="text-ink-4">{tr("当前生效")}：</span>
      <span className={pending ? "text-gold" : "text-ink-2"}>{layerLabel(tr, status)}</span>
      {pending && <span className="text-gold"> · {tr("待重启")}</span>}
    </span>
  );
}

/** 每一层都说清"是谁在生效",而不是抛一个裸状态码。 */
function layerLabel(tr: Tr, status: ProxyStatus): string {
  switch (status.source) {
    case "env:selene":
      return `SELENE_PROXY_URL (${status.effective_masked})`;
    case "env:https":
      return `https_proxy (${status.effective_masked})`;
    case "saved":
      return status.saved_masked;
    case "saved-stale":
      // 保存值和运行值不同 —— 两个都亮出来,别只给一个让人猜哪个是真的。
      return `${status.saved_masked}（${tr("待重启")}）`;
    case "detected":
      return `${tr("自动探测（Windows 系统代理）")} (${status.effective_masked})`;
    case "direct":
      return tr("直连（无代理）");
    default:
      return tr("未知（后端未报告运行中的设置）");
  }
}

function DetectionResult({
  tr,
  state,
}: {
  tr: Tr;
  state: AsyncState<ProxyDetection>;
}) {
  if (state.error) {
    return <div className="mt-1.5 text-[11px] text-ink-3">{state.error}</div>;
  }
  if (!state.data) return null;

  const { candidates, recommended } = state.data;

  if (candidates.length === 0) {
    return (
      <div className="mt-1.5 text-[11px] leading-snug text-ink-3">
        {tr("未发现本机代理（常见端口 7890/7899/1080/8080 均无响应）")}
      </div>
    );
  }
  if (!recommended) {
    return (
      <div className="mt-1.5 text-[11px] leading-snug text-ink-3">
        {tr("{n} 个端口在监听，但都无法通过代理访问外网", {
          n: candidates.length,
        })}
        {" — "}
        {tr("未发现可用的本机代理")}
      </div>
    );
  }
  const usable = candidates.filter((c) => c.reachable);
  return (
    <div className="mt-1.5 text-[11px] leading-snug text-ink-2">
      <span className="text-gold">{tr("本机检测到")}：</span>
      {usable.map((c, i) => (
        <span key={c.url}>
          {i > 0 && " · "}
          <code className="font-mono text-ink">{c.url}</code>
        </span>
      ))}
      <span className="text-ink-3">
        {" — "}
        {tr("已填入 {url}，确认无误后点保存", { url: recommended })}
      </span>
    </div>
  );
}

function TestResult({
  tr,
  state,
}: {
  tr: Tr;
  state: AsyncState<ProxyTestResult>;
}) {
  if (state.error) {
    return <div className="mt-1.5 text-[11px] text-ink-3">{state.error}</div>;
  }
  if (!state.data) return null;
  const { results, reachable, total, direct } = state.data;
  return (
    <div className="mt-1.5 rounded border border-border-soft bg-surface-3 px-2 py-1.5">
      <div className="text-[11px] text-ink-2">
        {direct ? tr("直连测试") : tr("代理测试")}
        {" — "}
        {reachable === total
          ? tr("全部目标可达")
          : tr("{n}/{total} 个目标可达", { n: reachable, total })}
      </div>
      <ul className="mt-1 space-y-0.5">
        {results.map((r) => (
          <li key={r.name} className="flex items-center gap-1.5 text-[11px]">
            <span className={r.ok ? "text-gold" : "text-ink-4"}>
              {r.ok ? "✓" : "✕"}
            </span>
            <span className="font-mono text-ink-2 w-[68px] shrink-0">{r.name}</span>
            <span className="text-ink-4">
              {r.ok ? `${r.status} · ${r.ms}ms` : (r.error ?? "—")}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 四层优先级原本只写在源码里（main.py::_init_market_data）。
 * 用户看不到它，就只能每次改完代理靠重启试探 —— 这里把它摊开，
 * 并标出当前命中的是哪一层。
 */
function PriorityGuide({ tr, status }: { tr: Tr; status: ProxyStatus | null }) {
  const [open, setOpen] = useState(false);
  const hit = (layer: string) => status?.source === layer;

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-[11px] text-ink-3 hover:text-ink-2"
      >
        {open ? "▾" : "▸"} {tr("优先级（从高到低）")}
      </button>
      {open && (
        <ol className="mt-1 space-y-0.5 pl-1 text-[11px] leading-snug text-ink-3">
          <li className={hit("env:selene") || hit("env:https") ? "text-gold" : ""}>
            {tr("1. 环境变量")} <code className="font-mono">SELENE_PROXY_URL</code> /{" "}
            <code className="font-mono">https_proxy</code>{" "}
            <span className="text-ink-4">{tr("（优先于此处设置）")}</span>
          </li>
          <li className={hit("saved") || hit("saved-stale") ? "text-gold" : ""}>
            {tr("2. 保存的代理")}
          </li>
          <li className={hit("detected") ? "text-gold" : ""}>
            {tr("3. 系统代理")} <span className="text-ink-4">{tr("自动探测（Windows 系统代理）")}</span>
          </li>
          <li className={hit("direct") ? "text-gold" : ""}>{tr("4. 直连")}</li>
        </ol>
      )}
    </div>
  );
}
