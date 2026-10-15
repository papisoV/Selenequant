"use client"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { AlertTriangle, Info, ShieldAlert, ShieldX, XCircle } from "lucide-react"
import { cn } from "@/lib/utils"

export type RiskLevel = "info" | "warning" | "restricted" | "dangerous" | "critical"

export interface RiskAlert {
  level: RiskLevel
  rule_id: string
  message: string
}

const LEVEL_CONFIG: Record<RiskLevel, {
  icon: React.ElementType
  label: string
  border: string
  bg: string
  text: string
  iconColor: string
}> = {
  info: {
    icon: Info,
    label: "提示",
    border: "border-blue-500/30",
    bg: "bg-blue-500/10",
    text: "text-blue-300",
    iconColor: "text-blue-400",
  },
  warning: {
    icon: AlertTriangle,
    label: "警告",
    border: "border-yellow-500/30",
    bg: "bg-yellow-500/10",
    text: "text-yellow-300",
    iconColor: "text-yellow-400",
  },
  restricted: {
    icon: ShieldAlert,
    label: "限制",
    border: "border-orange-500/30",
    bg: "bg-orange-500/10",
    text: "text-orange-300",
    iconColor: "text-orange-400",
  },
  dangerous: {
    icon: ShieldX,
    label: "危险",
    border: "border-red-500/30",
    bg: "bg-red-500/10",
    text: "text-red-300",
    iconColor: "text-red-400",
  },
  critical: {
    icon: XCircle,
    label: "禁止",
    border: "border-red-700/50",
    bg: "bg-red-700/20",
    text: "text-red-400",
    iconColor: "text-red-500",
  },
}

interface RiskAlertDialogProps {
  open: boolean
  alerts: RiskAlert[]
  onConfirm: () => void
  onCancel: () => void
}

export function RiskAlertDialog({ open, alerts, onConfirm, onCancel }: RiskAlertDialogProps) {
  const hasCritical = alerts.some((a) => a.level === "critical")
  const worstLevel = alerts.reduce<RiskLevel>((worst, a) => {
    const order: RiskLevel[] = ["info", "warning", "restricted", "dangerous", "critical"]
    return order.indexOf(a.level) > order.indexOf(worst) ? a.level : worst
  }, "info")

  const worstConfig = LEVEL_CONFIG[worstLevel]

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className={cn("flex items-center gap-2", worstConfig.text)}>
            <worstConfig.icon className={cn("h-5 w-5", worstConfig.iconColor)} />
            风控提醒
          </DialogTitle>
          <DialogDescription>
            {hasCritical
              ? "交易被阻止，存在严重风险"
              : "以下风险提醒供您参考，确认后可继续下单"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 max-h-60 overflow-y-auto">
          {alerts.map((alert, i) => {
            const cfg = LEVEL_CONFIG[alert.level]
            const Icon = cfg.icon
            return (
              <div
                key={i}
                className={cn(
                  "flex items-start gap-2 rounded-lg border p-3",
                  cfg.border,
                  cfg.bg
                )}
              >
                <Icon className={cn("h-4 w-4 mt-0.5 shrink-0", cfg.iconColor)} />
                <div className="min-w-0">
                  <div className={cn("text-xs font-medium", cfg.text)}>
                    [{cfg.label}] {alert.rule_id}
                  </div>
                  <div className="text-sm text-foreground/80 mt-0.5">
                    {alert.message}
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          {!hasCritical && (
            <Button onClick={onConfirm} className="bg-amber-600 hover:bg-amber-700">
              确认继续
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
