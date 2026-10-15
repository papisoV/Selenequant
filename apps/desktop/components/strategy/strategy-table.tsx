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

export interface StrategyTableProps {
  strategies: StrategyData[]
  getLatestBacktest: (strategyId: string) => BacktestData | null | undefined
  studioHref?: (id: string) => string
}

function directionLabel(d?: string): string {
  if (d === "short_only") return "只做空"
  if (d === "neutral") return "中性"
  return "只做多"
}

export function StrategyTable({ strategies, getLatestBacktest, studioHref }: StrategyTableProps) {
  const router = useRouter()

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="text-xs">名称</TableHead>
          <TableHead className="text-xs">类型</TableHead>
          <TableHead className="text-xs">标的</TableHead>
          <TableHead className="text-xs">周期</TableHead>
          <TableHead className="text-xs">方向</TableHead>
          <TableHead className="text-xs">状态</TableHead>
          <TableHead className="text-xs text-right">夏普</TableHead>
          <TableHead className="text-xs text-right">收益</TableHead>
          <TableHead className="text-xs text-right">最大回撤</TableHead>
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
              <TableCell className="text-xs">{directionLabel(s.direction)}</TableCell>
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
                  aria-label="打开 Studio"
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
