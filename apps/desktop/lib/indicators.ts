// 自定义指标目录 — 静态镜像 packages/strategy-engine/selene_space_strategy/templates/*.yaml
// 数量以实读为准:26 个模板文件(禁臆造)。id/name/description/params 默认值与 YAML 对齐。
// 分类:优先取 factory.py STRATEGY_TEMPLATES 的 StrategyCategory;仅 YAML 存在的模板按语义归类。

export type IndicatorCategory =
  | "trend"
  | "momentum"
  | "reversion"
  | "breakout"
  | "volatility"
  | "arbitrage"
  | "ict"
  | "timing"
  | "ai";

export const INDICATOR_CATEGORY_LABELS: Record<IndicatorCategory, string> = {
  trend: "趋势",
  momentum: "动量",
  reversion: "均值回归",
  breakout: "突破",
  volatility: "波动率",
  arbitrage: "套利",
  ict: "ICT 结构",
  timing: "择时",
  ai: "AI",
};

export type IndicatorParamType = "number" | "string" | "boolean";

export interface IndicatorParam {
  key: string;
  label: string;
  type: IndicatorParamType;
  default: number | string | boolean;
  min?: number;
  max?: number;
  step?: number;
}

export interface IndicatorMeta {
  /** 模板 id(镜像 YAML 的 id 字段) */
  id: string;
  /** 策略类型(镜像 YAML 的 type 字段) */
  type: string;
  /** 英文名(镜像 YAML 的 name 字段) */
  name: string;
  nameZh: string;
  category: IndicatorCategory;
  interval: string;
  description: string;
  params: IndicatorParam[];
}

const PARAM_LABELS: Record<string, string> = {
  adx_period: "ADX 周期",
  adx_threshold: "ADX 阈值",
  adx_range_threshold: "区间阈值",
  atr_period: "ATR 周期",
  atr_multiplier: "ATR 倍数",
  bb_period: "布林周期",
  bb_std: "标准差",
  fast_ma: "快线周期",
  slow_ma: "慢线周期",
  ma_type: "均线类型",
  ema_fast: "快线 EMA",
  ema_slow: "慢线 EMA",
  funding_rate_threshold: "费率开仓阈值",
  funding_rate_exit: "费率平仓阈值",
  funding_rate_z_threshold: "费率 Z 阈值",
  min_oi_change: "最小持仓变化",
  fvg_min_gap_atr: "缺口 ATR 倍数",
  ob_move_threshold: "位移阈值",
  ob_max_age: "订单块存续(K)",
  volume_threshold: "量比阈值",
  reversal_bars: "反转确认 K",
  swing_lookback: "摆动回看",
  kdj_n: "KDJ N",
  kdj_m1: "KDJ M1",
  kdj_m2: "KDJ M2",
  fast_period: "快周期",
  slow_period: "慢周期",
  signal_period: "信号周期",
  ma_len: "均线长度",
  deviation: "连续偏离",
  mtf_min_confluence: "最小共识度",
  mtf_require_trend_agreement: "需趋势一致",
  strategy_class: "策略实现类",
  entry_z: "开仓 Z",
  exit_z: "平仓 Z",
  hurst_threshold: "Hurst 阈值",
  lookback: "回看期",
  rsi_period: "RSI 周期",
  rsi_oversold: "超卖阈值",
  rsi_overbought: "超买阈值",
  oversold: "超卖阈值",
  overbought: "超买阈值",
  news_source: "新闻源",
  sentiment_threshold_positive: "正向阈值",
  sentiment_threshold_negative: "负向阈值",
  lookback_period: "聚合回看(h)",
  aggregation_method: "聚合方式",
  sma_short: "短期 SMA",
  sma_long: "长期 SMA",
  sma_fast: "快线 SMA",
  sma_slow: "慢线 SMA",
  stoch_k: "K 周期",
  stoch_d: "D 周期",
  stoch_smooth: "平滑周期",
};

const n = (key: string, def: number, extra?: Partial<IndicatorParam>): IndicatorParam => ({
  key,
  label: PARAM_LABELS[key] ?? key,
  type: "number",
  default: def,
  ...extra,
});
const s = (key: string, def: string): IndicatorParam => ({
  key,
  label: PARAM_LABELS[key] ?? key,
  type: "string",
  default: def,
});
const b = (key: string, def: boolean): IndicatorParam => ({
  key,
  label: PARAM_LABELS[key] ?? key,
  type: "boolean",
  default: def,
});
const per = (key: string, def: number): IndicatorParam => n(key, def, { min: 1, step: 1 });
const osc = (key: string, def: number): IndicatorParam => n(key, def, { min: 0, max: 100, step: 5 });

export const INDICATORS: IndicatorMeta[] = [
  {
    id: "template-adx-breakout",
    type: "adx_breakout",
    name: "ADX Volatility Breakout",
    nameZh: "ADX 波动突破",
    category: "breakout",
    interval: "1h",
    description: "ADX 确认 + 价格动量的突破策略。",
    params: [per("adx_period", 14), n("adx_threshold", 20, { min: 0, max: 100, step: 5 })],
  },
  {
    id: "template-adx-range",
    type: "adx_range",
    name: "ADX Range Strategy",
    nameZh: "ADX 区间策略",
    category: "volatility",
    interval: "1h",
    description: "ADX 低位判定低波动震荡市,区间交易。",
    params: [per("adx_period", 14), n("adx_range_threshold", 20, { min: 0, max: 100, step: 5 })],
  },
  {
    id: "template-adx-trend",
    type: "adx_trend",
    name: "ADX Trend Follower",
    nameZh: "ADX 趋势跟踪",
    category: "trend",
    interval: "1h",
    description: "ADX 大于阈值判定强趋势,配合 SMA 交叉入场。",
    params: [per("adx_period", 14), n("adx_threshold", 25, { min: 0, max: 100, step: 5 })],
  },
  {
    id: "template-atr-breakout",
    type: "atr_breakout",
    name: "ATR Breakout",
    nameZh: "ATR 突破",
    category: "breakout",
    interval: "1h",
    description: "价格突破 ATR 波动范围的高波动突破策略。",
    params: [per("atr_period", 14), n("atr_multiplier", 2.0, { min: 0.1, step: 0.5 })],
  },
  {
    id: "template-atr-trailing",
    type: "atr_trailing",
    name: "ATR Trailing Stop",
    nameZh: "ATR 移动止损",
    category: "trend",
    interval: "1h",
    description: "SMA 交叉入场,ATR 动态止损跟踪。",
    params: [per("atr_period", 14), n("atr_multiplier", 3.0, { min: 0.1, step: 0.5 })],
  },
  {
    id: "template-atr-volatility",
    type: "atr_volatility",
    name: "ATR Volatility Strategy",
    nameZh: "ATR 波动率",
    category: "volatility",
    interval: "1h",
    description: "波动率扩张入场,ATR 定位止损。",
    params: [per("atr_period", 14), n("atr_multiplier", 2.5, { min: 0.1, step: 0.5 })],
  },
  {
    id: "template-bollinger-breakout",
    type: "custom",
    name: "Bollinger Breakout",
    nameZh: "布林带突破",
    category: "breakout",
    interval: "1h",
    description: "收盘价突破布林上轨入场,跌破中轨平仓。",
    params: [per("bb_period", 20), n("bb_std", 2.0, { min: 0.1, step: 0.5 })],
  },
  {
    id: "template-bollinger-reversal",
    type: "bollinger_reversal",
    name: "Bollinger Band Reversal",
    nameZh: "布林带反转",
    category: "reversion",
    interval: "1h",
    description: "触下轨买入、中轨/上轨卖出的均值回归策略。",
    params: [per("bb_period", 20), n("bb_std", 2, { min: 0.1, step: 0.5 })],
  },
  {
    id: "template-dca-grid",
    type: "dca_grid",
    name: "DCA Grid",
    nameZh: "DCA 网格策略",
    category: "trend",
    interval: "1h",
    description: "MA 交叉触发首仓,网格逐级加仓摊薄成本,带止损防无限下行。",
    params: [per("fast_ma", 10), per("slow_ma", 30), s("ma_type", "ema")],
  },
  {
    id: "template-ema-cross",
    type: "ema_cross",
    name: "EMA Crossover",
    nameZh: "EMA 交叉",
    category: "trend",
    interval: "1h",
    description: "快 EMA 上穿慢 EMA 做多,反向交叉平仓。",
    params: [per("ema_fast", 12), per("ema_slow", 26)],
  },
  {
    id: "template-funding-rate-arb",
    type: "custom",
    name: "Funding Rate Arbitrage",
    nameZh: "期现资金费率套利",
    category: "arbitrage",
    interval: "4h",
    description: "费率高位做空合约+做多现货收取资金费,Delta 中性,费率反转平仓。",
    params: [
      n("funding_rate_threshold", 0.0003, { step: 0.0001 }),
      n("funding_rate_exit", -0.0001, { step: 0.0001 }),
      n("funding_rate_z_threshold", 2.0, { step: 0.1 }),
      n("min_oi_change", 0.0, { step: 0.1 }),
    ],
  },
  {
    id: "template-ict-fvg",
    type: "ict",
    name: "ICT FVG Reentry",
    nameZh: "ICT 公允价值缺口",
    category: "ict",
    interval: "4h",
    description: "检测 FVG 公平价值缺口后回补入场(仅用已注册指标)。",
    params: [n("fvg_min_gap_atr", 0.1, { min: 0, step: 0.1 }), n("ob_move_threshold", 2.0, { min: 0, step: 0.5 })],
  },
  {
    id: "template-ict-ob",
    type: "ict",
    name: "ICT Order Block",
    nameZh: "ICT 订单块",
    category: "ict",
    interval: "4h",
    description: "价格回踩订单块区间入场(仅用已注册指标)。",
    params: [n("ob_move_threshold", 2.0, { min: 0, step: 0.5 }), per("ob_max_age", 30)],
  },
  {
    id: "template-ict-sweep",
    type: "ict",
    name: "ICT Liquidity Sweep",
    nameZh: "ICT 流动性扫荡",
    category: "ict",
    interval: "4h",
    description: "流动性扫荡 + 放量确认后反向入场(仅用已注册指标)。",
    params: [
      n("volume_threshold", 1.5, { min: 0, step: 0.5 }),
      per("reversal_bars", 3),
      per("swing_lookback", 20),
    ],
  },
  {
    id: "template-kdj-momentum",
    type: "kdj_momentum",
    name: "KDJ Momentum",
    nameZh: "KDJ 动量",
    category: "momentum",
    interval: "1h",
    description: "KDJ J 值穿越触发动量入场。",
    params: [per("kdj_n", 9), per("kdj_m1", 3), per("kdj_m2", 3)],
  },
  {
    id: "template-kdj-trend",
    type: "kdj_trend",
    name: "KDJ Trend Following",
    nameZh: "KDJ 趋势",
    category: "trend",
    interval: "1h",
    description: "KDJ 金叉判定趋势方向的趋势跟踪策略。",
    params: [per("kdj_n", 9), per("kdj_m1", 3), per("kdj_m2", 3)],
  },
  {
    id: "template-macd-momentum",
    type: "macd",
    name: "MACD Momentum",
    nameZh: "MACD 动量",
    category: "momentum",
    interval: "1h",
    description: "MACD 线上穿信号线做多,下穿平仓。",
    params: [per("fast_period", 12), per("slow_period", 26), per("signal_period", 9)],
  },
  {
    id: "template-ma-extended-runs",
    type: "ma_extended_runs",
    name: "MA Extended Runs",
    nameZh: "MA 连续偏离",
    category: "reversion",
    interval: "1h",
    description: "价格连续 N 根 K 偏离均线后回归入场。",
    params: [per("ma_len", 10), s("ma_type", "sma"), per("deviation", 3)],
  },
  {
    id: "template-mtf-trend",
    type: "custom",
    name: "MTF Trend Confluence",
    nameZh: "多周期趋势共振",
    category: "trend",
    interval: "15m",
    description: "1h 定趋势 + 15m 定入场,双周期共识度>=0.7 才发信号(需 MTF 聚合器)。",
    params: [
      n("mtf_min_confluence", 0.7, { min: 0, max: 1, step: 0.1 }),
      b("mtf_require_trend_agreement", true),
      per("adx_period", 14),
      n("adx_threshold", 25, { min: 0, max: 100, step: 5 }),
      per("ema_fast", 9),
      per("ema_slow", 21),
    ],
  },
  {
    id: "template-pairs-btc-eth",
    type: "custom",
    name: "Pairs BTC/ETH",
    nameZh: "BTC/ETH 配对套利",
    category: "reversion",
    interval: "1h",
    description: "BTC/ETH 统计套利:OLS 对冲比 + Z 分数价差进出 + Hurst 均值回归校验。",
    params: [
      s("strategy_class", "pairs_trading"),
      n("entry_z", 2.0, { step: 0.1 }),
      n("exit_z", 0.5, { step: 0.1 }),
      n("hurst_threshold", 0.5, { step: 0.1 }),
      per("lookback", 20),
    ],
  },
  {
    id: "template-rsi-momentum",
    type: "rsi_momentum",
    name: "RSI Momentum",
    nameZh: "RSI 动量",
    category: "momentum",
    interval: "1h",
    description: "RSI 上穿超卖线做多,超买区卖出。",
    params: [per("rsi_period", 14), osc("rsi_oversold", 30), osc("rsi_overbought", 70)],
  },
  {
    id: "template-rsi-reversal",
    type: "rsi_reversal",
    name: "RSI Reversal",
    nameZh: "RSI 反转",
    category: "reversion",
    interval: "1h",
    description: "RSI(14) 跌破 30 超卖买入,升破 70 超买平仓。",
    params: [per("rsi_period", 14), osc("oversold", 30), osc("overbought", 70)],
  },
  {
    id: "template-sentiment",
    type: "sentiment",
    name: "Sentiment Analysis",
    nameZh: "情绪分析(实际 RSI 动量)",
    category: "momentum",
    interval: "1h",
    description: "名为情绪分析实为 RSI 动量策略;sentiment_config 是无人消费的占位配置。",
    params: [
      s("news_source", "crypto_news"),
      n("sentiment_threshold_positive", 0.5, { min: -1, max: 1, step: 0.1 }),
      n("sentiment_threshold_negative", -0.5, { min: -1, max: 1, step: 0.1 }),
      per("lookback_period", 24),
      s("aggregation_method", "mean"),
    ],
  },
  {
    id: "template-sma-cross",
    type: "sma_cross",
    name: "SMA Crossover",
    nameZh: "SMA 交叉",
    category: "trend",
    interval: "1h",
    description: "经典双均线交叉:快 SMA 上穿慢 SMA 做多,下穿平仓。",
    params: [per("sma_short", 20), per("sma_long", 50)],
  },
  {
    id: "template-sma-fvg-mixed",
    type: "custom",
    name: "SMA + FVG Mixed",
    nameZh: "SMA+FVG 混合",
    category: "trend",
    interval: "4h",
    description: "SMA 交叉判趋势 + FVG 位移确认的混合趋势跟随策略。",
    params: [per("sma_fast", 10), per("sma_slow", 50)],
  },
  {
    id: "template-stoch-momentum",
    type: "stochastic_momentum",
    name: "Stochastic Momentum",
    nameZh: "随机指标动量",
    category: "momentum",
    interval: "1h",
    description: "随机指标 %K/%D 交叉触发入场。",
    params: [per("stoch_k", 14), per("stoch_d", 3), per("stoch_smooth", 3)],
  },
];

export function indicatorById(idOrType: string): IndicatorMeta | undefined {
  return INDICATORS.find((i) => i.id === idOrType || i.type === idOrType);
}

export function validIndicator(idOrType: string): boolean {
  return indicatorById(idOrType) !== undefined;
}

export function listIndicatorsByCategory(category: IndicatorCategory | "all"): IndicatorMeta[] {
  if (category === "all") return INDICATORS;
  return INDICATORS.filter((i) => i.category === category);
}

export function formatParamValue(v: number | string | boolean): string {
  if (typeof v === "boolean") return v ? "是" : "否";
  if (typeof v === "number") return String(v);
  return v;
}

/** 可读公式串:模板名( 参数标签 = 值, ... ) */
export function buildFormulaString(
  meta: IndicatorMeta,
  values?: Record<string, number | string | boolean>,
): string {
  const parts = meta.params.map((p) => {
    const v = values?.[p.key] ?? p.default;
    return `${p.label} = ${formatParamValue(v)}`;
  });
  return parts.length > 0 ? `${meta.name}( ${parts.join(", ")} )` : meta.name;
}

/** 演示回测结果(确定性,便于测试断言) */
export interface BacktestResult {
  total_return: number;
  annual_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  profit_factor: number;
  equity_curve: number[];
}

function sineWave(n: number, amp: number, phase: number): number[] {
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const drift = 1 + 0.0025 * i;
    const wave = Math.sin((i / n) * Math.PI * 2 + phase) * amp;
    const prev = out.length > 0 ? out[out.length - 1] : 1;
    out.push(Number((prev * drift + wave * 0.001).toFixed(4)));
  }
  return out;
}

export function buildDemoBacktestResult(seed = 0): BacktestResult {
  const curve = sineWave(60, 0.02 + seed * 0.005, seed * 0.7);
  const total_return = (curve[curve.length - 1] / curve[0] - 1) * 100;
  const winRate = 42 + seed * 4;
  const trades = 24 + seed * 6;
  return {
    total_return: Number(total_return.toFixed(2)),
    annual_return: Number((total_return * 4.2).toFixed(2)),
    sharpe_ratio: Number((1.1 + seed * 0.2).toFixed(2)),
    max_drawdown: Number((-8 - seed * 1.5).toFixed(2)),
    win_rate: winRate,
    total_trades: trades,
    profit_factor: Number((1.25 + seed * 0.1).toFixed(2)),
    equity_curve: curve,
  };
}
