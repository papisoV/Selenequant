// DEPRECATED: 死壳页 — 无路由入口（page-manifest 未注册 slug，无 app/research/<slug> 路由，无外部 import）。
// 内部 AgentStudioPanel 含真实 API 交互但不可达。保留代码防重建，勿视为 live 页。
"use client";

import { AgentStudioPanel } from "@/components/research/agent-studio-panel";

export function AgentStudioPage() {
  return <AgentStudioPanel />;
}
