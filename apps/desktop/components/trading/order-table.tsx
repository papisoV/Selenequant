"use client"

import { useEffect, useState } from "react"
import { useApi } from "@/lib/hooks/use-api"
import { apiDelete } from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { X } from "lucide-react"
import { useAccountId } from "@/lib/hooks/use-account-id"
import type { Order } from "@/lib/types/backend"

const statusColor: Record<string, string> = {
  created: "text-yellow-500",
  accepted: "text-blue-500",
  partial: "text-orange-500",
  filled: "text-green-500",
  cancelled: "text-muted-foreground",
  rejected: "text-destructive",
}

interface OrderTableProps {
  wsOrders?: Order[]
}

export function OrderTable({ wsOrders }: OrderTableProps) {
  const accountId = useAccountId()
  const { data: restOrders } = useApi<Order[]>(
    `/api/v1/trading/orders?account_id=${encodeURIComponent(accountId)}&status=active`
  )

  const [orders, setOrders] = useState<Order[]>([])

  useEffect(() => {
    if (restOrders && restOrders.length > 0) {
      setOrders(restOrders)
    } else {
      setOrders([])
    }
  }, [restOrders])

  const merged = [...(wsOrders ?? [])]
  for (const o of orders) {
    if (!merged.find((w) => w.id === o.id)) merged.push(o)
  }
  merged.sort((a, b) => b.created_at - a.created_at)

  async function handleCancel(orderId: string) {
    try {
      await apiDelete(`/api/v1/trading/orders/${orderId}?account_id=${encodeURIComponent(accountId)}`)
    } catch { /* ignore */ }
  }

  const openStatuses = new Set(["created", "accepted", "partial"])

  return (
    <div className="overflow-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted-foreground text-xs">
            <th className="text-left py-2 px-2 font-medium">品种</th>
            <th className="text-left py-2 px-2 font-medium">方向</th>
            <th className="text-left py-2 px-2 font-medium">类型</th>
            <th className="text-right py-2 px-2 font-medium">数量</th>
            <th className="text-right py-2 px-2 font-medium">价格</th>
            <th className="text-right py-2 px-2 font-medium">已成交</th>
            <th className="text-left py-2 px-2 font-medium">状态</th>
            <th className="py-2 px-2"></th>
          </tr>
        </thead>
        <tbody>
          {merged.length === 0 && (
            <tr>
              <td colSpan={8} className="py-8 text-center text-muted-foreground text-xs">
                暂无挂单
              </td>
            </tr>
          )}
          {merged.map((o) => (
            <tr key={o.id} className="border-b border-border/50 hover:bg-muted/30">
              <td className="py-2 px-2 font-mono text-xs">{o.symbol}</td>
              <td className={cn("py-2 px-2 uppercase text-xs font-medium", o.side === "buy" ? "text-green-500" : "text-red-500")}>
                {o.side}
              </td>
              <td className="py-2 px-2 text-xs">{o.type}</td>
              <td className="py-2 px-2 text-right font-mono text-xs">{o.quantity}</td>
              <td className="py-2 px-2 text-right font-mono text-xs">{o.price ?? "-"}</td>
              <td className="py-2 px-2 text-right font-mono text-xs">{o.filled_quantity}</td>
              <td className={cn("py-2 px-2 text-xs capitalize", statusColor[o.status] || "")}>
                {o.status}
              </td>
              <td className="py-2 px-2">
                {openStatuses.has(o.status) && (
                  <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => handleCancel(o.id)}>
                    <X className="h-3 w-3" />
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
