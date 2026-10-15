"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AlertRuleForm } from "./alert-rule-form";
import type { AlertRule, AlertRuleDraft } from "@/lib/api/intelligence";

export function AlertRuleEditorDialog({
  open,
  onOpenChange,
  rule,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rule?: AlertRule;
  onSubmit: (draft: AlertRuleDraft) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{rule ? "编辑规则" : "新建规则"}</DialogTitle>
          <DialogDescription>配置预警触发的条件与通知渠道</DialogDescription>
        </DialogHeader>
        <AlertRuleForm
          key={rule?.id ?? "create"}
          initial={rule}
          onSubmit={(draft) => {
            onSubmit(draft);
            onOpenChange(false);
          }}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
