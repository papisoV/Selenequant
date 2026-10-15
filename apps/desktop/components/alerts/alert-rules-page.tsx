"use client";

import { useEffect, useState } from "react";
import { AlertRuleCard } from "./alert-rule-card";
import { AlertRuleEditorDialog } from "./alert-rule-editor-dialog";
import { useAlertsStore } from "@/lib/store/alerts";
import type { AlertRule, AlertRuleDraft } from "@/lib/api/intelligence";

export function AlertRulesPage() {
  const rules = useAlertsStore((s) => s.rules);
  const syncError = useAlertsStore((s) => s.syncError);
  const hydrate = useAlertsStore((s) => s.hydrate);
  const syncFromBackend = useAlertsStore((s) => s.syncFromBackend);
  const addRule = useAlertsStore((s) => s.addRule);
  const updateRule = useAlertsStore((s) => s.updateRule);
  const deleteRule = useAlertsStore((s) => s.deleteRule);
  const toggleRule = useAlertsStore((s) => s.toggleRule);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<AlertRule | undefined>(undefined);

  useEffect(() => {
    hydrate();
    void syncFromBackend();
  }, [hydrate, syncFromBackend]);

  const openCreate = () => {
    setEditing(undefined);
    setDialogOpen(true);
  };
  const openEdit = (rule: AlertRule) => {
    setEditing(rule);
    setDialogOpen(true);
  };
  const handleDelete = (rule: AlertRule) => {
    if (window.confirm(`删除规则「${rule.name}」?`)) {
      deleteRule(rule.id);
    }
  };
  const handleSubmit = (draft: AlertRuleDraft) => {
    if (editing) updateRule(editing.id, draft);
    else addRule(draft);
  };

  return (
    <div className="alert-rules-page flex h-full flex-col gap-2 overflow-auto p-3">
      <div className="flex items-center justify-between border-b border-border-soft pb-1">
        <h2 className="text-[11px] font-medium text-gold">情报预警规则</h2>
        <button
          type="button"
          onClick={openCreate}
          className="rounded border border-border-soft px-2 py-0.5 text-[9px] text-ink-2 hover:bg-surface-2"
        >
          + 新建规则
        </button>
      </div>
      {syncError && <div className="text-[9px] text-gold">{syncError}</div>}
      <div className="flex flex-col gap-1.5">
        {rules.length === 0 && (
          <div className="py-6 text-center text-[11px] text-ink-4">
            暂无规则,点击「+ 新建规则」创建
          </div>
        )}
        {rules.map((r) => (
          <AlertRuleCard
            key={r.id}
            rule={r}
            onEdit={openEdit}
            onDelete={handleDelete}
            onToggle={(rule) => toggleRule(rule.id)}
          />
        ))}
      </div>
      <AlertRuleEditorDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        rule={editing}
        onSubmit={handleSubmit}
      />
    </div>
  );
}
