"use client"

import { useState, useMemo, useCallback } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  ArrowRight,
  FlaskConical,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Trash2,
  Circle,
  Clock,
  Eye,
  Square,
  Loader2,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { CurveChart } from "./shared-charts"
import type {
  StrategyData,
  BacktestData,
  StrategyDeploymentData,
  PaperEquityPoint,
  PaperAccountData,
} from "./types"
import { cn } from "@/lib/utils"
import { useUiText } from "@/components/i18n/use-ui-text"
import { identityTranslate, type Translate } from "@/lib/i18n/types"

export interface StrategyCardProps {
  strategy: StrategyData
  latestBacktest?: BacktestData | null
  deployment?: StrategyDeploymentData | null
  liveEquity?: PaperEquityPoint[]
  livePnl?: { realized: number; unrealized: number } | null
  liveAccount?: PaperAccountData | null
  onDelete?: (id: string) => void
  onStop?: (strategyId: string) => Promise<void>
  studioHref?: (id: string) => string
}

/**
 * 纯函数,默认 identity 翻译(测试按中文断言);渲染点传当前 `tr`。
 * 加参数而不是在函数内调 hook —— 它被 `.map()` 与模块级调用点共用,
 * 在纯函数里调 `useUiText()` 就是 Rules-of-Hooks 违规。
 */
export function directionLabel(direction: StrategyData["direction"], tr: Translate = identityTranslate): string {
  if (direction === "short_only") return tr("只做空")
  if (direction === "neutral") return tr("中性")
  return tr("只做多")
}

export function extractMetrics(bt: BacktestData) {
  const m = bt.result?.metrics
  const sharpe = m?.sharpe_ratio ?? bt.sharpe_ratio ?? 0
  const ret = m?.total_return ?? bt.total_return ?? 0
  const dd = m?.max_drawdown ?? bt.max_drawdown ?? 0
  return { sharpe, ret, dd }
}

export function extractEquityCurve(bt: BacktestData): number[] {
  if (Array.isArray(bt.result?.equity_curve)) return bt.result!.equity_curve ?? []
  if (Array.isArray(bt.equity_curve)) return bt.equity_curve ?? []
  return []
}

export function formatDuration(ms: number): string {
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m`
  const hr = Math.floor(min / 60)
  const remMin = min % 60
  if (hr < 24) return remMin > 0 ? `${hr}h ${remMin}m` : `${hr}h`
  const days = Math.floor(hr / 24)
  const remHr = hr % 24
  return remHr > 0 ? `${days}d ${remHr}h` : `${days}d`
}

/** 策略卡的边框样式(运行中高亮, 否则常规)。纯函数。 */
export function cardBorderClass(isRunning: boolean): string {
  return isRunning
    ? "border-emerald-500/50 hover:border-emerald-400/70 shadow-[0_0_12px_rgba(16,185,129,0.08)]"
    : "border-border hover:border-primary/40"
}

/** 方向 → 图标组件名(short_only→TrendingDown, neutral→BarChart3, 否则 TrendingUp)。 */
export function directionIconName(
  direction: StrategyData["direction"],
): "TrendingDown" | "BarChart3" | "TrendingUp" {
  if (direction === "short_only") return "TrendingDown"
  if (direction === "neutral") return "BarChart3"
  return "TrendingUp"
}

/** 方向 → 图标组件(按 directionIconName 的名字取)。 */
function directionIconFor(
  name: "TrendingDown" | "BarChart3" | "TrendingUp",
): typeof TrendingUp {
  return { TrendingDown, BarChart3, TrendingUp }[name]
}

/** 两次点击确认的停止流程(首击进入待确认, 再击才执行)。 */
function useStopConfirmation(
  confirmStop: boolean,
  setConfirmStop: (v: boolean) => void,
  setStopping: (v: boolean) => void,
  onStop: ((id: string) => Promise<void> | void) | undefined,
  strategyId: string,
) {
  return useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation()
      if (!confirmStop) {
        setConfirmStop(true)
        return
      }
      if (!onStop) return
      setStopping(true)
      try {
        await onStop(strategyId)
      } finally {
        setStopping(false)
        setConfirmStop(false)
      }
    },
    [confirmStop, setConfirmStop, setStopping, onStop, strategyId],
  )
}

/** 策略卡的状态标签(优先级: 运行中 > 已停止 > 已回测 > 草稿)。纯函数。 */
export function strategyStatusLabel(
  isRunning: boolean,
  isStopped: boolean,
  hasBacktest: boolean,
  tr: Translate = identityTranslate,
): string {
  if (isRunning) return tr("运行中")
  if (isStopped) return tr("已停止")
  return hasBacktest ? tr("已回测") : tr("草稿")
}

/**
 * 由实时权益点与账户快照算出展示曲线。纯函数。
 * - ≥2 点: 取 equity 序列, 有账户权益则补末尾
 * - 恰好 1 点且有正账户权益: 补成两点
 * - 其余: 空
 */
export function buildLiveEquityCurve(
  liveEquity: { equity: number }[] | undefined,
  accountEquity: number | undefined,
): number[] {
  const pts = liveEquity ?? []
  const hasAccount = accountEquity !== undefined && accountEquity > 0
  if (pts.length < 2) {
    if (hasAccount && pts.length === 1) return [pts[0].equity, accountEquity]
    return []
  }
  const curve = pts.map((p) => p.equity)
  if (hasAccount) curve.push(accountEquity)
  return curve
}

export function StrategyCard({
  strategy,
  latestBacktest,
  deployment,
  liveEquity,
  liveAccount,
  onDelete,
  onStop,
  studioHref,
}: StrategyCardProps) {
  const tr = useUiText()

  const router = useRouter()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmStop, setConfirmStop] = useState(false)
  const [stopping, setStopping] = useState(false)
  const studioPath = studioHref ? studioHref(strategy.id) : `/strategies/${strategy.id}/studio`

  const isRunning = deployment?.status === "running"
  const isStopped = deployment?.status === "stopped"

  const runtimeMs = useMemo(() => {
    if (!deployment?.created_at) return 0
    return Date.now() - new Date(deployment.created_at).getTime()
  }, [deployment?.created_at])

  const liveEquityCurve = useMemo(
    () => buildLiveEquityCurve(liveEquity, liveAccount?.equity),
    [liveEquity, liveAccount?.equity],
  )

  const accountMetrics = useMemo(() => {
    const initialBalance = liveAccount?.initial_balance ?? 0
    const currentEquity = liveAccount?.equity ?? 0
    const pnlAmount = currentEquity - initialBalance
    const pnlPercent = initialBalance > 0 ? (pnlAmount / initialBalance) * 100 : 0
    return { initialBalance, currentEquity, pnlAmount, pnlPercent, hasData: !!liveAccount }
  }, [liveAccount])

  const DirectionIcon = directionIconFor(directionIconName(strategy.direction))

  const { sharpe, ret, dd } = latestBacktest
    ? extractMetrics(latestBacktest)
    : { sharpe: 0, ret: 0, dd: 0 }
  const backtestEquityCurve = latestBacktest ? extractEquityCurve(latestBacktest) : []

  const statusLabel = strategyStatusLabel(isRunning, isStopped, !!latestBacktest, tr)

  const handleStop = useStopConfirmation(
    confirmStop,
    setConfirmStop,
    setStopping,
    onStop,
    strategy.id,
  )

  return (
    <Card
      className={cn("transition-all cursor-pointer group bg-card", cardBorderClass(isRunning))}
      onClick={() => router.push(studioPath)}
    >
      <CardContent className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h3 className="font-medium text-sm truncate">{strategy.name}</h3>
            <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
              {strategy.description ||
                `${strategy.type} on ${strategy.symbols?.join(", ") ?? ""}`}
            </p>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {strategy.source === "template" && (
              <Badge variant="secondary" className="text-[10px]">
                {tr("模板")}
              </Badge>
            )}
            {isRunning ? (
              <Badge className="text-[10px] gap-1 bg-emerald-600 hover:bg-emerald-600">
                <Circle className="h-2 w-2 fill-current animate-pulse" />
                {statusLabel}
              </Badge>
            ) : isStopped ? (
              <Badge variant="secondary" className="text-[10px] gap-1 text-amber-400">
                <Circle className="h-2 w-2 fill-current" />
                {statusLabel}
              </Badge>
            ) : (
              <Badge variant="outline" className="text-[10px]">
                {statusLabel}
              </Badge>
            )}
          </div>
        </div>

        {isRunning && (
          <div className="rounded-md bg-emerald-500/5 border border-emerald-500/20 px-3 py-2 space-y-1.5">
            <div className="flex items-center gap-3 text-[11px]">
              <span className="flex items-center gap-1 text-emerald-400">
                <Clock className="h-3 w-3" />
                {tr("运行时长: {t}", { t: runtimeMs > 0 ? formatDuration(runtimeMs) : "—" })}
              </span>
              <span className="text-muted-foreground">v{deployment?.strategy_version ?? strategy.version ?? 1}</span>
            </div>

            {accountMetrics.hasData && (
              <div className="grid grid-cols-4 gap-1.5 pt-0.5">
                <div>
                  <div className="text-[9px] text-muted-foreground leading-tight">{tr("初始资金")}</div>
                  <div className="text-[11px] font-mono font-medium">{accountMetrics.initialBalance.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}</div>
                </div>
                <div>
                  <div className="text-[9px] text-muted-foreground leading-tight">{tr("当前资金")}</div>
                  <div className="text-[11px] font-mono font-medium">{accountMetrics.currentEquity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                </div>
                <div>
                  <div className="text-[9px] text-muted-foreground leading-tight">{tr("盈亏")}</div>
                  <div className={cn(
                    "text-[11px] font-mono font-medium",
                    accountMetrics.pnlAmount >= 0 ? "text-emerald-400" : "text-red-400",
                  )}>
                    {accountMetrics.pnlAmount >= 0 ? "+" : ""}{accountMetrics.pnlAmount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                </div>
                <div>
                  <div className="text-[9px] text-muted-foreground leading-tight">{tr("收益率")}</div>
                  <div className={cn(
                    "text-[11px] font-mono font-medium",
                    accountMetrics.pnlPercent >= 0 ? "text-emerald-400" : "text-red-400",
                  )}>
                    {accountMetrics.pnlPercent >= 0 ? "+" : ""}{accountMetrics.pnlPercent.toFixed(2)}%
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-1.5 flex-wrap">
          <Badge variant="outline" className="text-[10px] gap-0.5">
            <FlaskConical className="h-3 w-3 opacity-70" />
            {strategy.type}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            {strategy.interval}
          </Badge>
          <Badge variant="outline" className="text-[10px] gap-0.5">
            <DirectionIcon className="h-3 w-3" />
            {directionLabel(strategy.direction, tr)}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            v{strategy.version ?? 1}
          </Badge>
        </div>

        {isRunning ? (
          liveEquityCurve.length >= 2 ? (
            <div className="pt-1">
              <div className="text-[10px] text-emerald-400/70 mb-1">{tr("实时权益曲线")}</div>
              <CurveChart curve={liveEquityCurve} height={90} />
            </div>
          ) : (
            <div className="text-[10px] text-muted-foreground text-center py-3">
              {tr("暂无交易记录")}
            </div>
          )
        ) : latestBacktest ? (
          <>
            <div className="grid grid-cols-3 gap-2 pt-1">
              <div className="text-center">
                <div className="text-[10px] text-muted-foreground">{tr("夏普")}</div>
                <div className="text-xs font-mono font-medium">{sharpe.toFixed(2)}</div>
              </div>
              <div className="text-center">
                <div className="text-[10px] text-muted-foreground">{tr("收益")}</div>
                <div className={cn("text-xs font-mono font-medium", ret >= 0 ? "text-emerald-400" : "text-red-400")}>
                  {ret >= 0 ? "+" : ""}{(ret * 100).toFixed(1)}%
                </div>
              </div>
              <div className="text-center">
                <div className="text-[10px] text-muted-foreground">{tr("最大回撤")}</div>
                <div className="text-xs font-mono font-medium text-red-400">{(dd * 100).toFixed(1)}%</div>
              </div>
            </div>
            {backtestEquityCurve.length >= 2 && (
              <div className="pt-1">
                <CurveChart curve={backtestEquityCurve} height={90} />
              </div>
            )}
          </>
        ) : (
          <div className="text-[10px] text-muted-foreground text-center py-2">
            {tr("暂无回测记录")}
          </div>
        )}

        <div className="flex items-center gap-2 pt-1">
          {isRunning ? (
            <>
              <Button
                size="sm"
                variant="outline"
                className="flex-1 text-xs h-7 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10"
                onClick={(e) => {
                  e.stopPropagation()
                  router.push(`/paper?account=${deployment?.account_id ?? ""}`)
                }}
              >
                <Eye className="h-3 w-3 mr-1" />
                {tr("查看模拟")}
              </Button>
              {onStop && (
                confirmStop ? (
                  <div className="flex items-center gap-1">
                    <Button
                      size="sm"
                      variant="destructive"
                      className="text-xs h-7 px-2"
                      disabled={stopping}
                      onClick={handleStop}
                    >
                      {stopping ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        tr("停止策略")
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-xs h-7 px-1.5"
                      onClick={(e) => {
                        e.stopPropagation()
                        setConfirmStop(false)
                      }}
                    >
                      ✕
                    </Button>
                  </div>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    className="text-xs h-7 px-2 border-red-500/30 text-red-400 hover:bg-red-500/10"
                    onClick={handleStop}
                    title={tr("停止策略")}
                  >
                    <Square className="h-3 w-3" />
                  </Button>
                )
              )}
            </>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="flex-1 text-xs h-7"
              onClick={(e) => {
                e.stopPropagation()
                router.push(studioPath)
              }}
            >
              {tr("打开 Studio")}
              <ArrowRight className="h-3 w-3 ml-1" />
            </Button>
          )}
          {onDelete && !isRunning && (
            confirmDelete ? (
              <>
                <Button
                  size="sm"
                  variant="destructive"
                  className="text-xs h-7 px-2"
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(strategy.id)
                  }}
                >
                  {tr("删除策略")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-xs h-7 px-2"
                  onClick={(e) => {
                    e.stopPropagation()
                    setConfirmDelete(false)
                  }}
                >
                  ✕
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                onClick={(e) => {
                  e.stopPropagation()
                  setConfirmDelete(true)
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )
          )}
        </div>
      </CardContent>
    </Card>
  )
}
