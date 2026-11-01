"use client"

import { useRouter } from "next/navigation"
import { ArrowRight } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { BacktestData, StrategyData } from "./types"
import { useUiText } from "@/components/i18n/use-ui-text"
import { directionLabel } from "./strategy-card"

export interface StrategyTableProps {
  strategies: StrategyData[]
  getLatestBacktest: (strategyId: string) => BacktestData | null | undefined
  studioHref?: (id: string) => string
}

export function StrategyTable({ strategies, getLatestBacktest, studioHref }: StrategyTableProps) {
  const tr = useUiText()

  const router = useRouter()

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="text-xs">{tr("名称")}</TableHead>
          <TableHead className="text-xs">{tr("类型")}</TableHead>
          <TableHead className="text-xs">{tr("标的")}</TableHead>
          <TableHead className="text-xs">{tr("周期")}</TableHead>
          <TableHead className="text-xs">{tr("方向")}</TableHead>
          <TableHead className="text-xs">{tr("状态")}</TableHead>
          <TableHead className="text-xs text-right">{tr("夏普")}</TableHead>
          <TableHead className="text-xs text-right">{tr("收益")}</TableHead>
          <TableHead className="text-xs text-right">{tr("最大回撤")}</TableHead>
          <TableHead className="text-xs" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {strategies.map((s) => {
          const bt = getLatestBacktest(s.id)
          const metrics = bt?.result?.metrics
          const sharpe = metrics?.sharpe_ratio ?? bt?.sharpe_ratio ?? null
          const ret = metrics?.total_return ?? bt?.total_return ?? null
          const dd = metrics?.max_drawdown ?? bt?.max_drawdown ?? null
          const href = studioHref ? studioHref(s.id) : `/strategies/${s.id}/studio`
          return (
            <TableRow
              key={s.id}
              className="cursor-pointer hover:bg-muted/50"
              onClick={() => router.push(href)}
            >
              <TableCell className="text-xs font-medium">{s.name}</TableCell>
              <TableCell>
                <Badge variant="outline" className="text-[10px]">
                  {s.type}
                </Badge>
              </TableCell>
              <TableCell className="text-xs">{s.symbols?.join(", ")}</TableCell>
              <TableCell className="text-xs">{s.interval}</TableCell>
              <TableCell className="text-xs">{directionLabel(s.direction, tr)}</TableCell>
              <TableCell>
                <Badge variant="outline" className="text-[10px]">
                  {s.lifecycle_state ?? "draft"}
                </Badge>
              </TableCell>
              <TableCell className="text-right font-mono text-xs">
                {sharpe != null ? sharpe.toFixed(2) : "—"}
              </TableCell>
              <TableCell
                className={`text-right font-mono text-xs ${ret != null && ret >= 0 ? "text-emerald-400" : "text-red-400"}`}
              >
                {ret != null ? `${(ret * 100).toFixed(1)}%` : "—"}
              </TableCell>
              <TableCell className="text-right font-mono text-xs text-red-400">
                {dd != null ? `${(dd * 100).toFixed(1)}%` : "—"}
              </TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  aria-label={tr("打开 Studio")}
                  onClick={(e) => {
                    e.stopPropagation()
                    router.push(href)
                  }}
                >
                  <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
