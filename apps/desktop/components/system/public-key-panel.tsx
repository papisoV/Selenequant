"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card } from "@/components/shared/page-filter-bar";
import { getPublicKey, type PublicKeyJWK } from "@/lib/api/settings";
import { useUiText } from "@/components/i18n/use-ui-text"

export function PublicKeyPanel() {
  const tr = useUiText()

  const { data, isLoading, error, mutate } = useSWR<PublicKeyJWK>(
    "/api/v1/settings/public-key",
    getPublicKey,
  );
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);

  const jwk = data ?? null;
  const jwkStr = jwk ? JSON.stringify(jwk, null, 2) : "";

  async function copyKey() {
    if (!jwkStr) return;
    try {
      if (!navigator.clipboard) {
        throw new Error("剪贴板 API 不可用(需 HTTPS)");
      }
      await navigator.clipboard.writeText(jwkStr);
      setCopied("ok");
    } catch (e) {
      setCopied("fail");
      console.warn(tr("复制公钥失败:"), e instanceof Error ? e.message : e);
    } finally {
      setTimeout(() => setCopied(null), 1500);
    }
  }

  return (
    <V17Card title={tr("加密公钥 (JWK)")}>
      <div className="text-[10px] text-ink-4 mb-1.5">
        {tr("前端用此 RSA 公钥加密敏感数据(如 API key)后再传后端,后端用对应私钥解密。")}
      </div>
      {isLoading && !jwk && (
        <div className="text-[11px] text-ink-3 py-2 animate-pulse">{tr("加载中…")}</div>
      )}
      {error && (
        <div className="text-[10px] text-neg py-1">
          {tr("加载失败:")} {error instanceof Error ? error.message : String(error)}
        </div>
      )}
      {jwk && (
        <>
          <pre className="text-[9px] font-mono text-ink-3 whitespace-pre-wrap break-all max-h-32 overflow-auto rounded border border-border-soft bg-surface-2 p-1.5">
            {jwkStr}
          </pre>
          <div className="flex items-center gap-2 mt-1.5">
            <button
              onClick={copyKey}
              className="rounded border border-border-soft bg-surface-2 px-2 py-0.5 text-[10px] text-ink hover:bg-surface-3"
            >
            {copied === "ok" ? tr("✓ 已复制") : copied === "fail" ? tr("✗ 复制失败") : tr("复制 JWK")}
            </button>
            <button
              onClick={() => mutate()}
              className="rounded border border-border-soft bg-surface-2 px-2 py-0.5 text-[10px] text-ink hover:bg-surface-3"
            >
              {tr("刷新")}
            </button>
            {jwk.kid && (
              <span className="text-[9px] text-ink-4 font-mono ml-auto">
                kid: {String(jwk.kid).length > 16 ? `${String(jwk.kid).slice(0, 16)}…` : String(jwk.kid)}
              </span>
            )}
          </div>
        </>
      )}
    </V17Card>
  );
}
