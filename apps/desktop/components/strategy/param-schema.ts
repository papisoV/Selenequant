// US-1 paramSchema:从 param-panel 抽出的纯推导层(无 React 依赖,可单测)。
// 数据源 = factory-templates default_params 按值 typeof(StrategyMetadata 无类型
// 信息,后端 param_schema rider YAGNI 不做 — 0927 拍板)。string 参数中已知
// 枚举键走 Select,其余 fallback Input;错型输入由 validate 端点兜底(strategies.py validate)。

export interface ParamBounds {
  min: number
  max: number
  step: number
  allowZero: boolean
}

// Business bounds derived from strategy-engine schema
// (packages/strategy-engine/selene_space_strategy/models.py RiskParams + live.py leverage Field).
// allowZero mirrors schema ge constraint: ge=0 → allowZero:true, ge=1 → allowZero:false.
export const PARAM_BOUNDS: Record<string, ParamBounds> = {
  stop_loss_pct: { min: 0, max: 1, step: 0.01, allowZero: true },
  take_profit_pct: { min: 0, max: 1, step: 0.01, allowZero: true },
  max_position_pct: { min: 0, max: 1, step: 0.01, allowZero: true },
  position_size_pct: { min: 0.01, max: 1, step: 0.01, allowZero: false },
  max_open_positions: { min: 1, max: 100, step: 1, allowZero: false },
  leverage: { min: 1, max: 125, step: 1, allowZero: false },
  cooldown_bars: { min: 0, max: 500, step: 1, allowZero: true },
  kelly_fraction: { min: 0.1, max: 1, step: 0.05, allowZero: false },
  atr_sl_mult: { min: 0.5, max: 20, step: 0.1, allowZero: false },
  atr_tp_mult: { min: 0.5, max: 20, step: 0.1, allowZero: false },
  atr_initial_mult: { min: 0.5, max: 20, step: 0.1, allowZero: false },
  atr_breakeven_mult: { min: 1, max: 20, step: 0.1, allowZero: false },
  trailing_atr_mult: { min: 0.5, max: 20, step: 0.1, allowZero: false },
}

// string 枚举参数白名单 — 合法值取自引擎消费点(== 分支),不是猜的:
//   ma_type: strategies/* `ma_type == "ema"|"rma"|"lsma"`(缺省 sma)
//   *_src:   indicators `*_src == "close"|"high"|"low"|"typical"`
export const PARAM_ENUM_OPTIONS: Record<string, string[]> = {
  ma_type: ["sma", "ema", "rma", "lsma"],
  price_src: ["close", "high", "low", "typical"],
  rsi_src: ["close", "high", "low", "typical"],
  bb_src: ["close", "high", "low", "typical"],
}

export type ParamFieldKind = "number" | "boolean" | "enum" | "string" | "unknown"

export interface ParamField {
  kind: ParamFieldKind
  options?: string[]
  bounds?: { min: number; max: number; step: number }
}

// Fallback heuristic for params not in schema (e.g. indicator periods)
export function sliderBounds(value: number, paramName?: string): { min: number; max: number; step: number } {
  const bounds = paramName ? PARAM_BOUNDS[paramName] : undefined
  if (bounds) {
    if (value === 0) {
      if (bounds.allowZero) return { min: 0, max: bounds.max, step: bounds.step }
      // schema requires min>0: clamp to bounds.min (>=1), don't allow 0 drag
      return { min: bounds.min, max: bounds.max, step: bounds.step }
    }
    return { min: bounds.min, max: bounds.max, step: bounds.step }
  }

  const step = value >= 10 ? 1 : 0.1
  if (value === 0) return { min: 0, max: 100, step: 1 }
  if (value < 0) {
    const abs = Math.abs(value)
    const maxNeg = -Math.max(1, Math.floor(abs * 0.2))
    let minNeg = Math.floor(value * 3)
    if (minNeg >= maxNeg) minNeg = maxNeg - step
    return { min: minNeg, max: maxNeg, step: abs >= 10 ? 1 : 0.1 }
  }
  const min = Math.max(1, Math.floor(value * 0.2))
  let max = Math.ceil(value * 3)
  if (max <= min) max = min + step
  return { min, max, step }
}

// 按值 typeof + 键名枚举白名单推导字段形态
export function deriveParamField(key: string, value: unknown): ParamField {
  if (typeof value === "number") {
    return { kind: "number", bounds: sliderBounds(value, key) }
  }
  if (typeof value === "boolean") {
    return { kind: "boolean" }
  }
  if (typeof value === "string") {
    const options = PARAM_ENUM_OPTIONS[key]
    if (options) {
      // 当前值不在白名单内(引擎新增/用户手填)→ 保底仍给 Select,首项补当前值
      if (!options.includes(value)) {
        return { kind: "enum", options: [value, ...options] }
      }
      return { kind: "enum", options }
    }
    return { kind: "string" }
  }
  return { kind: "unknown" }
}
