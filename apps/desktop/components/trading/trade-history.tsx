"use client"

import { useEffect, useState } from "react"
import { useApi } from "@/lib/hooks/use-api"
import { useAccountId } from "@/lib/hooks/use-account-id"
import type { Fill } from "@/lib/types/backend"
import { useUiText } from "@/components/i18n/use-ui-text"

interface TradeHistoryProps {
  wsFills?: Fill[]
}

export function TradeHistory({ wsFills }: TradeHistoryProps) {
  const tr = useUiText()

  const accountId = useAccountId()
  const { data: restFills } = useApi<Fill[]>(
    `/api/v1/trading/fills?account_id=${encodeURIComponent(accountId)}&limit=50`
  )

  const [fills, setFills] = useState<Fill[]>([])

  useEffect(() => {
    if (restFills && restFills.length > 0) {
      setFills(restFills)
    } else {
      setFills([])
    }
  }, [restFills])

  const merged = [...(wsFills ?? [])]
  for (const f of fills) {
    if (!merged.find((w) => w.id === f.id)) merged.push(f)
  }
  merged.sort((a, b) => b.timestamp - a.timestamp)

  return (
    <div className="overflow-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted-foreground text-xs">
            <th className="text-left py-2 px-2 font-medium">{tr("订单号")}</th>
            <th className="text-right py-2 px-2 font-medium">{tr("价格")}</th>
            <th className="text-right py-2 px-2 font-medium">{tr("数量")}</th>
            <th className="text-right py-2 px-2 font-medium">{tr("手续费")}</th>
            <th className="text-right py-2 px-2 font-medium">{tr("时间")}</th>
          </tr>
        </thead>
        <tbody>
          {merged.length === 0 && (
            <tr>
              <td colSpan={5} className="py-8 text-center text-muted-foreground text-xs">
                {tr("暂无成交记录")}
              </td>
            </tr>
          )}
          {merged.map((f) => (
            <tr key={f.id} className="border-b border-border/50 hover:bg-muted/30">
              <td className="py-2 px-2 font-mono text-xs text-muted-foreground">{f.order_id.slice(0, 12)}</td>
              <td className="py-2 px-2 text-right font-mono text-xs">{f.price.toFixed(2)}</td>
              <td className="py-2 px-2 text-right font-mono text-xs">{f.quantity}</td>
              <td className="py-2 px-2 text-right font-mono text-xs text-muted-foreground">
                {f.fee.toFixed(4)} {f.fee_currency}
              </td>
              <td className="py-2 px-2 text-right text-xs text-muted-foreground">
                {new Date(f.timestamp).toLocaleTimeString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
