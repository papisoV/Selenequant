"use client"

import { useEffect, useState } from "react"
import { useApi } from "@/lib/hooks/use-api"
import { cn } from "@/lib/utils"
import { useAccountId } from "@/lib/hooks/use-account-id"
import type { Position } from "@/lib/types/backend"
import { useUiText } from "@/components/i18n/use-ui-text"

interface PositionPanelProps {
  wsPositions?: Position[]
}

export function PositionPanel({ wsPositions }: PositionPanelProps) {
  const tr = useUiText()

  const accountId = useAccountId()
  const { data: restPositions } = useApi<Position[]>(
    `/api/v1/trading/positions?account_id=${encodeURIComponent(accountId)}`
  )

  const [positions, setPositions] = useState<Position[]>([])

  useEffect(() => {
    if (restPositions && restPositions.length > 0) {
      setPositions(restPositions)
    } else {
      setPositions([])
    }
  }, [restPositions])

  const merged = [...(wsPositions ?? [])]
  for (const p of positions) {
    if (!merged.find((w) => w.symbol === p.symbol)) merged.push(p)
  }

  return (
    <div className="space-y-2">
      {merged.length === 0 && (
        <p className="text-center text-sm text-muted-foreground py-6">{tr("暂无持仓")}</p>
      )}
      {merged.map((p) => (
        <div key={p.symbol} className="rounded-lg border border-border p-3 space-y-1">
          <div className="flex items-center justify-between">
            <span className="font-mono text-sm font-medium">{p.symbol}</span>
            <span className={cn(
              "text-xs font-medium uppercase",
              p.side === "buy" ? "text-green-500" : "text-red-500"
            )}>
              {p.side === "buy" ? tr("多") : tr("空")}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <div>
              <span>{tr("数量:")} </span>
              <span className="font-mono text-foreground">{p.quantity_base || p.quantity}</span>
            </div>
            <div>
              <span>{tr("均价:")} </span>
              <span className="font-mono text-foreground">{p.avg_entry_price.toFixed(2)}</span>
            </div>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">{tr("未实现盈亏")}</span>
            <span className={cn(
              "font-mono font-medium",
              p.unrealized_pnl >= 0 ? "text-green-500" : "text-red-500"
            )}>
              {p.unrealized_pnl >= 0 ? "+" : ""}{p.unrealized_pnl.toFixed(2)}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}
