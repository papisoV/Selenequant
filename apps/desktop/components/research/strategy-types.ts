/**
 * Strategy Cockpit 数据 shape — 与 services/local-api research_missions.py
 * State(T) 三端点对齐(GET /research/strategy/{id}/state[/n][/decision])。
 */

export interface EpisodeRow {
  n: number;
  backtest_id: string;
  symbol: string;
  side: string | null;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number | null;
  pnl: number;
  return: number | null;
  quantity: number;
}

export interface BacktestMetrics {
  total_return: number;
  annual_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  calmar_ratio: number;
  sortino_ratio: number;
}

export interface EpisodesPack {
  episodes: EpisodeRow[];
  backtest_id: string;
  as_run: { symbol: string; interval: string };
  declared: { symbol: string | null; interval: string | null };
  drift: boolean;
  metrics: BacktestMetrics;
  created_at: string;
}

export interface StrategyMission {
  id: string;
  title: string;
  question: string;
  phase: string;
  updated_at: string;
  context: Record<string, unknown>;
  outcome: Record<string, unknown>;
}

export interface StrategyConfig {
  symbols?: string[];
  interval?: string;
  parameters?: Record<string, number | boolean>;
  template?: string;
}

export interface StrategyStateResponse {
  strategy: { id: string; name: string; config: StrategyConfig };
  episodes_pack: EpisodesPack;
  missions: StrategyMission[];
}

/* ---------- Episode State(T) ---------- */

export interface KeltnerBar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface KeltnerSnapshot {
  entry_bar: {
    t: string;
    close: number;
    upper_band: number;
    lower_band: number;
    centre: number;
    adx: number;
    adx_threshold: number;
  };
  rule: { entry: string; exit: string };
}

export type Provenance = "RECORDED" | "RECONSTRUCTED" | "NOT_AVAILABLE";

export interface UserDecision {
  decision: "execute" | "skip" | "investigate";
  decided_at: string;
  note: string | null;
  provenance: "RECORDED";
}

export interface EpisodeStateResponse {
  episode: EpisodeRow;
  as_run: { symbol: string; interval: string };
  drift: boolean;
  state_at_t: {
    market_state: {
      bars: KeltnerBar[];
      keltner: KeltnerSnapshot | null;
      provenance: Provenance;
      keltner_provenance: Provenance;
    };
    strategy_state: {
      declared_params: Record<string, number | boolean>;
      as_run_env: { symbol: string; interval: string };
      provenance: Provenance;
    };
    signal_state: {
      rule: { entry: string; exit: string } | null;
      note: string;
      provenance: Provenance;
    };
    event_state: { provenance: Provenance };
    funding: { provenance: Provenance };
    open_interest: { provenance: Provenance };
    news_at_t: { provenance: Provenance };
    memory_state: { note: string; provenance: Provenance };
    available_evidence: { kline_window_bars: number; note: string };
    user_decision: UserDecision | null;
    selene_decision: { note: string; provenance: Provenance };
  };
  outcome: {
    pnl: number;
    return: number | null;
    exit_time: string;
    exit_price: number | null;
    provenance: Provenance;
  };
}
