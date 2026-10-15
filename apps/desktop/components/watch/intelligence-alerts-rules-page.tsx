"use client";

import { AlertRulesPage } from "@/components/alerts/alert-rules-page";

// Phase 1 注册 stub → Phase 4 全量编辑器(新建/编辑/开关/删除,本地持久)
export function IntelligenceAlertsRulesPage() {
  return (
    <div className="intelligence-alerts-rules-page h-full">
      <AlertRulesPage />
    </div>
  );
}
