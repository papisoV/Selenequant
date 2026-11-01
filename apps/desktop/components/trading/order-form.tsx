"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { RiskAlertDialog } from "@/components/trading/risk-alert-dialog"
import { cn } from "@/lib/utils"
import { toastSuccess, toastError } from "@/lib/toast"
import { useAccountId } from "@/lib/hooks/use-account-id"
import { useRiskAwareSubmit } from "@/lib/hooks/use-risk-aware-submit"
import { useUiText } from "@/components/i18n/use-ui-text"

type OrderType = "market" | "limit" | "stop_market" | "stop_limit"

interface OrderResult {
  has_risk_alerts?: boolean
  risk_alerts?: { level: string; rule_id?: string; message: string }[]
  order?: { id: string }
}

interface SubmitParams {
  symbol: string
  side: "buy" | "sell"
  order_type: OrderType
  quantity: number
  price?: number
  stop_price?: number
  account_id: string
}

interface OrderFormProps {
  /** 受控 symbol(外部传入;缺省时用内部默认 BTC/USDT) */
  symbol?: string;
  /** symbol 变化回调(受控模式;缺省时 symbol 只读) */
  onSymbolChange?: (sym: string) => void;
  /** 下单成功回调 */
  onOrderPlaced?: () => void;
}

export function OrderForm({ symbol: symbolProp, onSymbolChange, onOrderPlaced }: OrderFormProps) {
  const tr = useUiText()

  const [internalSymbol, setInternalSymbol] = useState("BTC/USDT")
  // 受控优先:外部传 symbol 用外部,否则用内部 state
  const symbol = symbolProp ?? internalSymbol
  const [side, setSide] = useState<"buy" | "sell">("buy")
  const [orderType, setOrderType] = useState<OrderType>("market")
  const [quantity, setQuantity] = useState("")
  const [price, setPrice] = useState("")
  const [stopPrice, setStopPrice] = useState("")
  const [error, setError] = useState<string | null>(null)

  const accountId = useAccountId()

  // symbol 变化:受控模式调 onSymbolChange,非受控更新内部 state
  function handleSymbolChange(next: string) {
    if (onSymbolChange) {
      onSymbolChange(next)
    } else if (symbolProp === undefined) {
      setInternalSymbol(next)
    }
  }

  const showPrice = orderType === "limit" || orderType === "stop_limit"
  const showStopPrice = orderType === "stop_market" || orderType === "stop_limit"

  const { submit, riskAlerts, showRiskDialog, confirm, cancel, submitting } =
    useRiskAwareSubmit<SubmitParams, OrderResult>({
      url: "/api/v1/trading/orders",
      onSuccess: () => {
        setQuantity("")
        setPrice("")
        setStopPrice("")
        toastSuccess(`${side === "buy" ? tr("买入") : tr("卖出")} ${quantity} ${symbol} 已提交`)
        onOrderPlaced?.()
      },
      onError: (err) => {
        setError(err.message)
        toastError(`下单失败: ${err.message}`)
      },
    })

  function buildParams(): SubmitParams {
    const params: SubmitParams = {
      symbol,
      side,
      order_type: orderType,
      quantity: parseFloat(quantity),
      account_id: accountId,
    }
    if (showPrice && price) params.price = parseFloat(price)
    if (showStopPrice && stopPrice) params.stop_price = parseFloat(stopPrice)
    return params
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    await submit(buildParams())
  }

  return (
    <>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{tr("品种")}</Label>
            <Input
              value={symbol}
              onChange={(e) => handleSymbolChange(e.target.value)}
              placeholder="BTC/USDT"
              className="h-9"
              readOnly={symbolProp !== undefined && !onSymbolChange}
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">{tr("类型")}</Label>
            <select
              className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
              value={orderType}
              onChange={(e) => setOrderType(e.target.value as OrderType)}
            >
              <option value="market">{tr("市价")}</option>
              <option value="limit">{tr("限价")}</option>
              <option value="stop_market">{tr("止损市价")}</option>
              <option value="stop_limit">{tr("止损限价")}</option>
            </select>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">{tr("数量")}</Label>
          <Input
            type="number"
            step="any"
            min="0"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            placeholder="0.001"
            className="h-9"
            required
          />
        </div>

        {showPrice && (
          <div className="space-y-1.5">
            <Label className="text-xs">{tr("价格")}</Label>
            <Input
              type="number"
              step="any"
              min="0"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="60000"
              className="h-9"
            />
          </div>
        )}

        {showStopPrice && (
          <div className="space-y-1.5">
            <Label className="text-xs">{tr("止损价")}</Label>
            <Input
              type="number"
              step="any"
              min="0"
              value={stopPrice}
              onChange={(e) => setStopPrice(e.target.value)}
              placeholder="59000"
              className="h-9"
            />
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <Button
            type="submit"
            onClick={() => setSide("buy")}
            disabled={submitting || !quantity}
            className={cn(
              "h-10 font-semibold",
              side === "buy" ? "bg-green-600 hover:bg-green-700" : ""
            )}
            variant={side === "buy" ? "default" : "outline"}
          >
            {tr("买入")}
          </Button>
          <Button
            type="submit"
            onClick={() => setSide("sell")}
            disabled={submitting || !quantity}
            className={cn(
              "h-10 font-semibold",
              side === "sell" ? "bg-red-600 hover:bg-red-700" : ""
            )}
            variant={side === "sell" ? "default" : "outline"}
          >
            {tr("卖出")}
          </Button>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
      </form>

      <RiskAlertDialog
        open={showRiskDialog}
        alerts={riskAlerts}
        onConfirm={confirm}
        onCancel={cancel}
      />
    </>
  )
}
