"use client";

import { useApiPage } from "@/lib/hooks/use-api-page";
import { useUiText } from "@/components/i18n/use-ui-text"

// 研究代理就绪 node 状态区 — r5 判定 K25 降 node(us-radar hub 面板头右侧)
// shape: { agents[], overall_readiness, available_count, missing_providers[], timestamp }
interface AgentsReadinessData {
  overall_readiness?: string;
  available_count?: number;
}

const NODE_STYLE: Record<string, { dot: string; label: string; text: string }> = {
  available: { dot: "bg-pos", label: "就绪", text: "text-pos" },
  limited: { dot: "bg-gold", label: "受限", text: "text-gold" },
  disabled: { dot: "bg-ink-4", label: "停用", text: "text-ink-4" },
};

export function UsRadarAgentsNode() {
  const tr = useUiText()

  const { useApi } = useApiPage();
  const { data } = useApi<AgentsReadinessData | null>("/api/v1/us-radar/research-agents/readiness");
  const style = NODE_STYLE[data?.overall_readiness ?? ""] ?? {
    dot: "bg-ink-4", label: "未知", text: "text-ink-4",
  };
  return (
    <div className="flex shrink-0 items-center gap-1" title={tr("研究代理就绪")}>
      <span className={`size-1.5 rounded-full ${style.dot}`} aria-hidden />
      <span className={`text-[9px] ${style.text}`}>
        {tr("代理")} {tr(style.label)}
        {data?.available_count != null && ` ${data.available_count}`}
      </span>
    </div>
  );
}
