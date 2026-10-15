"use client"

import { useEffect, useState } from "react"
import { useApi } from "@/lib/hooks/use-api"
import { useAccountId } from "@/lib/hooks/use-account-id"

interface TradingBalance {
  asset: string
  exchange: string
  free: number
  locked: number
}

interface BalanceCardProps {
  wsBalances?: TradingBalance[]
}

export function BalanceCard({ wsBalances }: BalanceCardProps) {
  const [balances, setBalances] = useState<TradingBalance[]>([])
  const accountId = useAccountId()

  const { data: restBalances } = useApi<TradingBalance[]>(
    `/api/v1/trading/balances?account_id=${encodeURIComponent(accountId)}`
  )

  useEffect(() => {
    if (restBalances && restBalances.length > 0) {
      setBalances(restBalances)
    } else {
      setBalances([])
    }
  }, [restBalances])

  const display = wsBalances && wsBalances.length > 0 ? wsBalances : balances

  return (
    <div className="space-y-2">
      {display.length === 0 && (
        <p className="text-center text-sm text-muted-foreground py-4">暂无余额</p>
      )}
      {display.map((b) => (
        <div key={b.asset} className="flex items-center justify-between py-1.5">
          <div>
            <span className="font-mono text-sm font-medium">{b.asset}</span>
            <span className="text-xs text-muted-foreground ml-2">{b.exchange}</span>
          </div>
          <div className="text-right">
            <div className="font-mono text-sm">{b.free?.toFixed(2) ?? "—"}</div>
            {b.locked > 0 && (
              <div className="text-xs text-muted-foreground">锁定: {b.locked?.toFixed(2) ?? "—"}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
