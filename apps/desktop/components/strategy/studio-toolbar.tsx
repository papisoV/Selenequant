"use client"

import { Button } from "@/components/ui/button"
import {
  CheckCircle2,
  Download,
  Loader2,
  Play,
  Rocket,
  Save,
  Square,
  XCircle,
  Zap,
} from "lucide-react"
import { useUiText } from "@/components/i18n/use-ui-text"

export interface StudioToolbarProps {
  onSave: () => void
  onValidate: () => void
  onRunBacktest: () => void
  onDeploy: () => void
  onStopDeployment?: () => void
  onExport: () => void
  backtestRunning: boolean
  deploying?: boolean
  isDeployed?: boolean
  validationResult: { valid: boolean; errors: string[] } | null
}

export function StudioToolbar({
  onSave,
  onValidate,
  onRunBacktest,
  onDeploy,
  onStopDeployment,
  onExport,
  backtestRunning,
  deploying,
  isDeployed,
  validationResult,
}: StudioToolbarProps) {
  const tr = useUiText()

  return (
    <div className="flex items-center gap-1.5">
      <Button variant="outline" size="sm" className="h-7 text-xs" onClick={onSave}>
        <Save className="h-3.5 w-3.5 mr-1" /> {tr("保存")}
      </Button>
      <Button variant="outline" size="sm" className="h-7 text-xs" onClick={onValidate}>
        {validationResult?.valid === true ? (
          <CheckCircle2 className="h-3.5 w-3.5 mr-1 text-pos" />
        ) : validationResult?.valid === false ? (
          <XCircle className="h-3.5 w-3.5 mr-1 text-destructive" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
        )}
        {tr("校验")}
      </Button>
      <Button size="sm" className="h-7 text-xs" onClick={onRunBacktest} disabled={backtestRunning}>
        {backtestRunning ? (
          <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
        ) : (
          <Play className="h-3.5 w-3.5 mr-1" />
        )}
        {tr("运行回测")}
      </Button>
      <Button
        variant="outline"
        size="sm"
        className="h-7 text-xs opacity-50 cursor-not-allowed"
        disabled
        title="Coming in v0.2"
      >
        <Zap className="h-3.5 w-3.5 mr-1" /> {tr("优化")}
      </Button>
      <Button variant="outline" size="sm" className="h-7 text-xs" onClick={onDeploy} disabled={deploying || isDeployed}>
        {deploying ? (
          <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
        ) : (
          <Rocket className="h-3.5 w-3.5 mr-1" />
        )}
        {deploying ? tr("部署中...") : tr("部署")}
      </Button>
      {isDeployed && onStopDeployment && (
        <Button variant="destructive" size="sm" className="h-7 text-xs" onClick={onStopDeployment}>
          <Square className="h-3 w-3 mr-1" /> {tr("停止部署")}
        </Button>
      )}
      <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={onExport}>
        <Download className="h-3.5 w-3.5 mr-1" /> {tr("导出")}
      </Button>
    </div>
  )
}
