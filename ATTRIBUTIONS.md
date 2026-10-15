# Attributions

SeleneQuant Community Edition is built and maintained by papisoV.

This project would not be possible without the following open-source projects
and research contributions. Each is vendored or referenced in compliance with
its license terms.

## Vendored & Absorbed Components

### Vibe-Trading (MIT)

- Upstream: https://github.com/HKUDS/Vibe-Trading
- Authors: HKUDS contributors
- License: MIT © 2026 Vibe-Trading Contributors

SeleneQuant absorbed Vibe-Trading **by design study**: patterns, connector
rosters, and capability inventories were used as a reference for what a
finance agent platform should cover. The runtime itself is **not bundled in
this repository** — SeleneQuant implements its own equivalents (its own
broker/data/IM layers, its own factor zoo, its own ReAct agent runtime).
License texts and vendor records for the private-tree vendoring experiment
are preserved in the private tree only and are not part of this public
snapshot.

What we absorb from Vibe-Trading (design-level reference):
- 12 broker connectors (IBKR, Alpaca, Tiger, Longbridge, OKX, Binance,
  Robinhood, MT5, Futu, Dhan, Shoonya, Trading212)
- 18 market-data loaders (yfinance, akshare, tushare, mootdx, baostock,
  ccxt, etc.)
- 16 IM channels (Telegram, Discord, Slack, Feishu, WeCom, WeChat, QQ,
  DingTalk, MS Teams, WhatsApp, Matrix, MoChat, NapCat, ...)
- 460+ academic alpha factors (Alpha101, GTJA191, Qlib158, Academic,
  Fundamental)
- MCP server (54 finance research tools)
- Shadow Account (no-broker simulated trading pattern extraction)
- AST backtest sandbox
- 25 LLM providers (OpenAI-compatible)

What we do NOT absorb (architectural mismatch):
- Vibe-Trading's LangChain/LangGraph agent runtime (SeleneQuant uses its
  own ReAct runtime)
- Swarm DAG orchestrator
- Live trading mandate system (SeleneQuant has its own live-trading design)
- 88 agent skills (SeleneQuant uses its own skill format)

## UI primitives — shadcn/ui and Radix UI (MIT)

The React components under `apps/desktop/components/ui/` are built on
**shadcn/ui** (MIT, https://github.com/shadcn-ui/ui) and **Radix UI**
(MIT, https://github.com/radix-ui/primitives), via the
`class-variance-authority` and `@radix-ui/*` packages declared in
`apps/desktop/package.json`. shadcn/ui components are distributed as source
to be copied into the consuming project, so these files are near-identical
across every project that uses them.

## Bundled Factor Definitions

These are mathematical formulas reimplemented from factual mathematical
content (formulas are not copyrightable). SeleneQuant carries its own
reimplementation of these factor sets; license texts for the source factor
collections are preserved in the private tree.

- **Microsoft Qlib** (Apache 2.0) — qlib158 factor set
- **Kakushadze (2015) "101 Formulaic Alphas"** (arXiv:1601.00991)
- **Guotai Junan Securities (2014) "191 Short-period Trading Alpha Factors"**
- **Academic factor models**: Fama-French five-factor model, Carhart
  momentum, Hou-Xue-Zhang q-factor

## ai-berkshire (MIT)

- Upstream: https://github.com/xbtlin/ai-berkshire
- Authors: xbtlin
- License: MIT © 2026 xbtlin
- Assets: `claim_audit.py` (paper-engine, LLM claim audit), `rigor.py` (core, Decimal-precise computation), `taiwan_finmind.py` (stock, Taiwan FinMind market data), `SemanticResonanceRule` (risk-engine, narrative-group concentration risk) + 7 methodology docs
- Provenance headers preserved verbatim on every absorbed file

## Pnlclaw-community (AGPL-3.0)

- Upstream: https://github.com/YicunAI/Pnlclaw-community
- Authors: YicunAI
- License: GNU Affero General Public License v3.0 (see LICENSE — SeleneQuant
  is itself AGPL-3.0-only, the same license)

Substantial parts of SeleneQuant were derived from Pnlclaw-community and
have since been substantially rewritten. Derivation under the same license
is permitted by AGPL-3.0 §5, which also requires prominent notice of
modification — provided here and in the per-file provenance records listed
below.

## US_Stock_Crash_Monitor (MIT)

- Upstream: https://github.com/middletoo/US_Stock_Crash_Monitor
- Authors: 竹林寺/middletoo
- License: MIT © 2025 竹林寺
- Upstream commit: `a7e5003cbd6521c2d5e4b5f9d972aab404d45e57` (v1.3)
- Absorbed (code): `cnn_fng.py` (macrodata providers, CNN Fear & Greed fetch) — see `THIRD_PARTY_NOTICES.md` for the MIT text block
- Absorbed (pattern): 5-year rolling percentile credit scoring → `event_guard/credit_source.py` (rewrite, no code copied); degradation-chain honest-status pattern → SeleneQuant's data-ingest degradation spec
- Not absorbed: six-factor scoring model (hand-tuned thresholds, no edge), Streamlit UI, hardcoded historical "benchmarks" (mock data)
- Provenance headers preserved verbatim on absorbed files

## daily_stock_analysis (MIT)

- Upstream: https://github.com/ZhuLinsen/daily_stock_analysis
- Authors: ZhuLinsen
- License: MIT © 2026 ZhuLinsen
- Upstream commit: `ce364e45`
- Absorbed: notification noise control (dedup / cooldown / quiet-hours five-tuple
  gate, selene-ized from in-process dicts to SQLite persistence in
  `packages/pulse/selene_space_pulse/noise.py`), six notification channels
  (DingTalk with HMAC secret signing, ServerChan3, PushPlus, ntfy, Gotify,
  Pushover — payload/URL construction mirrored from
  `src/notification_sender/*`, rebuilt on the stdlib client with Selene's
  SSRF guard enforced on every new channel), and optional markdown-to-image
  rendering (engine probe order from `src/md2img.py`, in
  `packages/pulse/selene_space_pulse/img_render.py`)
- Provenance headers preserved on absorbed files
- Also absorbed (阶段 C / P5): forward-validation evaluation semantics from
  `src/core/backtest_engine.py` (`evaluate_single` / `_classify_outcome`) and
  the maturity/eval-window config defaults from `src/services/backtest_service.py`
  — ported as pure functions in
  `packages/backtest-engine/selene_space_backtest/forward_validation.py`.
  The direction source is re-bound to Selene's structured analysis record
  (`stage2.decision` + `trade_setup.direction`) because DSA's free-text
  `operation_advice` field has no Selene equivalent.
- Not absorbed: request-based channels, DSA config system, chunked-send
  state machines (Selene truncates instead), imgkit/markdown-to-file
  dependencies (stdlib subprocess is used against the same binaries), DSA's
  free-text keyword direction parser (no field to feed it)
- Also absorbed (阶段 D / B2, 2026-10-10): multi-market prompt context from
  `src/market_context.py` (`detect_market` / `get_market_guidelines`) — ported
  as `packages/shared-types/selene_space_types/market_context.py`
  (`resolve_market` / `market_guidance` / `render_market_guidance`) and wired
  into the pulse asset system prompt
  (`packages/pulse/selene_space_pulse/llm.py`). DSA's jp/kr/tw guardrails
  name the A-share concepts a foreign equity must *not* import; Selene extends
  this two-way (crypto-only concepts are also excluded from equities) and adds
  a CRYPTO block, since SeleneQuant is crypto-first. Selene's own
  `detect_market_domain` / `is_ashare_symbol` primitives (`routing.py`) are
  reused rather than re-created; `market_context.py` is a new sibling module.
- Not absorbed (阶段 D): DSA's stock-code regexes (`detect_market` input
  parsing) — Selene already has `routing.py` detection primitives, so only the
  guidance *data* (focus/avoid per market) was carried over.
- Also absorbed (M1, 2026-10-10): the market-level traffic-light composite from
  `src/market_analyzer.py` (`_build_market_light_scores` /
  `build_market_light_snapshot`) — ported as a pure compute function in
  `packages/shared-types/selene_space_types/market_light.py`
  (`compose_market_light` plus the per-dimension scorers). DSA blends
  breadth (0.45) + index (0.35) + limit (0.20) into a 0-100 score and a
  green/yellow/red band; Selene carries that weighting verbatim but re-binds
  the `limit` dimension to market capability — daily price limits exist for CN
  and TW only, so for HK/US/JP/KR/CRYPTO the dimension is marked
  `applicable=False` (neutral 50) instead of merely missing, and does not
  degrade `data_quality`. The `index` dimension stays the anchor (no index data
  → `unavailable`), matching DSA.
- Also absorbed (ETF 轮动, 2026-10-10): the rule-based dual-momentum ETF
  rotation engine from `src/core/etf_rotation.py` — ported as pure functions in
  `packages/backtest-engine/selene_space_backtest/etf_rotation.py`. DSA ranks a
  risk pool by `lookback_days` return on the last trading day of each week/month
  (relative momentum) and only keeps assets with a positive lookback return
  (absolute momentum); a `switch_buffer_pct` band keeps an eligible incumbent to
  cut churn, and orders fill at the next session's close (`EXECUTION_LAG_DAYS`).
  Selene carries the algorithm verbatim but drops DSA's data-loading, `Config`
  and notification layers (`etf_rotation_service.py`): the caller supplies wide,
  forward-adjusted close frames. The annualisation factor is a parameter
  (`annualization_days`, default 244 = A-share) rather than a hard-coded
  constant, and upstream's `compute_metrics` was renamed `momentum_metrics` to
  avoid colliding with the package's existing `metrics.compute_metrics`
  (trade-blotter metrics).
- Also absorbed (市场策略蓝图, 2026-10-10): the region-specific market strategy
  blueprints from `src/core/market_strategy.py` (`MarketStrategyBlueprint` /
  `StrategyDimension` / `to_prompt_block` / `to_markdown_block`) plus the
  alternate-language variants kept inline in `src/market_analyzer.py`
  (`_get_strategy_prompt_block` / `_get_strategy_markdown_block`) — ported and
  consolidated into `packages/shared-types/selene_space_types/market_strategy.py`
  (one bilingual table; `strategy_blueprint` / `render_strategy_blueprint`, with
  lookups reusing `resolve_market`). Upstream covers CN/HK/US/JP/KR; Selene adds
  TW and CRYPTO blueprints (upstream has neither), since SeleneQuant spans TW and
  is crypto-first. Rendering is fail-open for unknown markets, mirroring
  `market_context`. This is complementary to the B2 absorption: a blueprint says
  how to *analyse* a market, `market_context` says which concepts *do not apply*
  to it.
- Also absorbed (八态决策分类学, 2026-10-11): the eight-state decision action
  vocabulary, canonical score scale, and decision-profile normalizer from
  `src/schemas/decision_action.py` / `decision_scale.py` / `decision_profile.py`
  — consolidated into `packages/shared-types/selene_space_types/action_taxonomy.py`
  (pure declarative data + pure functions, stdlib only). DSA's eight-state
  `action` (buy/add/hold/reduce/sell/watch/avoid/alert) is its *display
  taxonomy*; it deliberately coexists with the three-state `decision_type`
  statistics contract rather than replacing it, and Selene follows the same
  philosophy: `FinalDirection` / `LLMSignalAction` are left untouched, and the
  new eight-state vocabulary lives beside them as a display/advice layer. The
  0-100 → band → action scale (`DECISION_SCALE`), the three-state rollup
  (`ACTION_DECISION_TYPE`), and profile normalization (`conservative` /
  `balanced` / `aggressive`, null-safe) are carried verbatim. Selene-ized: a
  one-way `action_direction` bridge maps an eight-state action to Selene's
  existing long/short/neutral vocabulary (the four non-directional states
  hold/watch/avoid/alert → `neutral`); the Korean label column is carried as-is.
- Not absorbed (八态决策分类学): DSA's ~270-line Chinese/English **report-prose
  parser** (`normalize_decision_action` with its negation-phrase tables:
  "不建议买入" → avoid, "no buy" → avoid, buy-back / sell-off compound masking).
  Selene consumes structured LLM signals (`LLMSignal`), not free-form report
  advice text, so there is no prose entry point to feed it — the same reason
  `forward_validation` re-binds the direction source. Only explicit tokens and
  aliases ("buy", "strong_buy", "accumulate", "trim", "wait") are recognized;
  unrecognized text returns `None` rather than defaulting to a neutral action.
- Also absorbed (组合风控三件, 2026-10-11): three portfolio/decision-risk
  modules, each a new standalone pure module in an existing package.
  (1) **Stop-loss proximity** from
  `src/services/portfolio_risk_service.py::_build_stop_loss` — ported as
  `packages/risk-engine/selene_space_risk/stop_proximity.py`
  (`audit_stop_proximity` / `StopPriceProvider` / `StaticStopPriceProvider`).
  Only the stop-loss aggregation is taken; the sibling
  `_build_decision_signal_risk` is **not** (it depends on DSA's
  `DecisionSignalService` persistence layer, out of scope). The upstream
  cost-vs-price near-band path is carried verbatim
  (`loss_pct = max(0, (avg_cost - last_price) / avg_cost * 100)` against
  `stop_loss_pct` / `near_ratio` = 10.0 / 0.8); Selene adds a **stop path**
  that bands on distance to a real dynamic stop (Selene's own
  `ATRDynamicStopRule.compute_stop_levels`) when one is supplied, and emits
  banded `RiskAlert`s (near → WARNING, at/through → DANGEROUS, never
  CRITICAL — post-trade advisory only, mirroring `position_audit.py`).
  (2) **Risk override planning** from `src/agent/risk_override.py` — ported as
  `packages/agent-runtime/selene_space_agent/risk_override.py` (the frozen
  `RiskOverridePlan` / `RiskOverrideApplication` pair, the six
  `RiskApplicationReason` states, `_VALID_DOWNGRADE_TRANSITIONS`, the
  `to_low_sensitivity_dict` prompt-safe view, and the buy→hold→sell downgrade
  ladder are all carried verbatim). The one input re-bind: upstream read DSA's
  `AgentContext.opinions` / `.risk_flags`, which Selene has no equivalent of,
  so `build_risk_override_plan` takes explicit keyword params
  (`risk_evidence` + `high_severity_flag`). The three-state ladder is kept
  as-is rather than mapped onto the eight-state vocabulary — the eight-state
  layer is display/advice, not a severity ordering.
  (3) **Market theme layer** from `src/schemas/market_structure.py` +
  `src/services/market_hotspot_service.py` +
  `src/services/market_structure_service.py` — ported as
  `packages/market-analysis/selene_space_market_analysis/theme_layer.py`
  (`build_theme_context` / `phase_from_change` / `strength_from_change` /
  `build_active_themes` / `infer_stock_role` + the theme schema models). It is
  a **no-IO aggregator**: the caller supplies already-fetched industry/concept
  rankings, so market-analysis gains no new `ashare` dependency. Phase
  (`>=3 → accelerating`, `>0 → warming`, else cooling), strength
  (`clamp(0,100, round(50 + value*8))`), and the honest partial-status data
  quality contract (`status="partial"` + `missing_fields`) are carried
  verbatim.
- Not absorbed (组合风控三件): DSA's `portfolio_risk_service` account/snapshot
  orchestration and report rendering; DSA's `AgentContext` container; DSA's
  `stock_profile_service` composition (its `_research_block` reaches the
  separately-scoped analysis-context-pack helper). Selene's theme layer takes
  data in rather than fetching it, so DSA's `data_provider` wiring, trade-date
  formatting, and report layer are all out of scope.

- Also absorbed (决策信号 service 层, 2026-10-11): the persisted decision-signal
  lifecycle — a signal table, an out-of-sample outcome table, and the direction
  scoring that connects them — from DSA's `src/storage.py`
  (`decision_signals` / `decision_signal_outcomes`),
  `src/services/decision_signal_outcome_service.py`, and
  `src/core/backtest_engine.py::_classify_signal_outcome`. Split across three
  layers by dependency direction, the same way P5's forward validation was:
  (a) the **pure scorer** as
  `packages/backtest-engine/selene_space_backtest/signal_outcome.py`
  (`direction_for_action` + `classify_signal_outcome` + `evaluate_signal`);
  (b) the **storage facade** as
  `packages/storage/selene_space_storage/repositories/decision_signals.py`
  (identity-keyed `create_if_absent`, upsert-on-unique-key outcomes, stats
  scan); (c) the **orchestrator** as
  `services/local-api/app/core/decision_signal_outcome.py` (candidate scan →
  fetch bars → classify → persist), since only the local-api assembly layer
  sees both storage and backtest-engine. Migration
  `v028_decision_signals` adds both tables plus 8 indexes. Two behaviours are
  carried **deliberately different from DSA**, each for a correctness reason
  rather than a style one: (1) the scorer keeps DSA's `not_up` direction
  (reduce/sell/avoid) and its no-neutral-band scoring, which Selene's existing
  `forward_validation.classify_outcome` does not model — feeding a sell call
  through that function scores a price **fall** as a loss (a false negative on
  the call's best case), so reusing it was rejected after being reproduced
  against the function itself; (2) the v028 schema carries a
  `CHECK (unable ⇒ outcome IS NULL, completed ⇒ outcome IS NOT NULL)` so the
  honest-refusal discipline is enforced structurally, not only by convention.
- Not absorbed (决策信号 service 层): DSA's `decision_signal_service.py` report
  coupling (`_normalize_payload`, report_language / decision_profile / expiry
  defaulting), the reassess service (its `analysis_context_pack_overview`
  dependency is a separately-scoped helper Selene does not carry, so it would
  be dead weight), the feedback table, the `expired → active` revive branch and
  the relaxed-horizon-fill branch of `create_if_absent` (both need DSA's expiry
  sweep and report semantics), and DSA's signal *production* layer. Selene
  currently has no eight-state action producer (`action_taxonomy.py` is
  imported only by its own `__init__.py`; the LLM side is long/short/hold), so
  this batch ships the storage/compute/query substrate plus an explicit
  `POST /api/v1/decision-signals` write endpoint that accepts an eight-state
  action and **does not** guess one — the long→buy-or-add mapping is a product
  decision, not an absorption one.
- Also absorbed (per-skill 表现权重, 2026-10-11, 批次 B3): the *per-Skill*
  weight policy that DSA layers on top of the same outcome table — its
  `SkillOpinionWeightService` / `SkillOpinionPerformanceService` /
  `SkillOpinionOutcomeService` triple. DSA folds it into an agent aggregator
  (`src/agent/skills/aggregator.py`); Selene splits it by dependency direction:
  (a) the **pure maths** as
  `packages/backtest-engine/selene_space_backtest/skill_weight.py`
  (`compute_skill_weight_factors` + `score_bucket`) — zero IO, replayable, with
  the same symmetric 15/15 Beta prior, `[1/1.2, 1.2]` clamp, and fail-neutral
  contract; (b) the **aggregation** as `DecisionSignalRepository.skill_weight_rows`
  in `packages/storage` — GROUP BY `(source_agent, horizon)`, emitting **raw
  counts only** (the sample-size threshold constant lives in backtest-engine, so
  storage stays free of that dependency); (c) the **assembly + endpoint** as
  `DecisionSignalOutcomeService.skill_weights` and
  `GET /api/v1/decision-signals/skill-weights` in `services/local-api`; (d) the
  **consumption seam** as an optional `weights` map on
  `SkillRegistry.top_n_for_prompt` in `packages/agent-runtime`. No new migration:
  ``source_agent`` is already a write-time snapshot column on
  ``decision_signal_outcomes`` (added in B2), so per-Skill statistics reuse the
  B2 substrate. One behaviour is carried **deliberately different from DSA**: the
  identity key grew an eighth dimension (`source_agent`) so two Skills in one
  report that issue literally identical action/horizon claims stay two rows —
  without it the later write collapses the earlier and one Skill's opinions and
  outcomes vanish. **Honest status — not yet live end-to-end**: no code path
  currently turns a Skill into a persisted decision signal, and
  `top_n_for_prompt` has no production consumer yet; this batch wires and tests
  the seam (`skill_weight_rows` → `compute_skill_weight_factors` →
  `skill_weights` → endpoint → registry ranking) but the producing side is still
  absent, so the factor is not yet exercised on real signals.
- Not absorbed (per-skill 表现权重): DSA's `SkillOpinionSampleService` (its
  sampling walks DSA's report/opinion store, a separately-scoped helper Selene
  does not carry) and DSA's aggregator-level blending of the factor into an
  agent context object (Selene injects Skills as markdown prompt fragments at
  `main.py`, with no ranking step, so the seam is the registry method, not an
  agent container).
- Also absorbed (个股画像聚合端点, 2026-10-11, 批次 B4): DSA's one-call stock
  profile aggregation from `src/services/stock_profile_service.py` — ported as
  `services/local-api/app/services/stock_profile_service.py` (the IO-free
  `StockProfileService` aggregator) plus its router
  `services/local-api/app/api/v1/stock_profile.py` (`GET
  /api/v1/stock-profile/{symbol}`). The load-bearing idea carried over is the
  **partial-data contract**: each of the six blocks (`quote` / `history` /
  `research` / `intelligence` / `portfolio` / `monitors`) reports its own
  `{status: fresh|partial|unavailable, data, limitations}` and a failing or
  absent source degrades that block alone, never the whole response; the
  global `evidence_quality` is a rollup of the six block statuses (`fresh`
  only if all six are fresh, `unavailable` only if all six are unavailable,
  else `partial`), with limitations deduplicated in block order. Selene keeps
  DSA's block set but **re-binds every block to a real Selene source** rather
  than DSA's `data_provider` bag: `quote`/`history` →
  `MarketDataService.get_ticker`/`get_klines`; `research` →
  `AnalysisRecordRepository.list_records`; `intelligence` →
  `NewsStore.query_news` (through `asyncio.to_thread`, since the store is
  blocking); `portfolio` → `AccountManager.list_accounts` ×
  `PositionManager.get_positions`; `monitors` →
  `PriceAlertService.list_alerts`. Every source is **`Depends`-injectable**,
  so the route is hermetically testable with no real SQLite file or network —
  a deliberate improvement on DSA's service-construction style.
  **Honest gaps, not papered over:** a block whose provider is absent reports
  `{name}_source_not_configured`, which is deliberately distinct from
  `{name}_unavailable` (source present but returned nothing) and
  `{name}_source_error` (source raised); the `portfolio` block is permanently
  `status="partial"` with `limitations=["paper_accounts_only"]` because Selene
  paper positions are keyed per account with no global listing, and Selene's
  ashare package has **no realtime quote function**, so `quote` is only as
  live as `MarketDataService` happens to be. Selene-ized: `canonicalize_symbol`
  strips and uppercases but **refuses to infer a market** from the code shape
  (a deliberate conservative choice — the caller must know the exchange), and
  a blank symbol raises `InvalidStockProfileSymbol`, mapped to
  `ErrorCode.INVALID_PARAMETER`.
- Not absorbed (个股画像聚合端点): DSA's report-rendering layer, its
  trade-date formatting helpers, and its `_research_block`'s dependency on the
  separately-scoped analysis-context-pack helper (Selene builds the research
  block from structured analysis records instead). DSA's free-text
  `operation_advice` field has no Selene equivalent, the same reason
  `forward_validation` re-binds its direction source.

- Absorbed (运行复盘 / run retrospective, 批次 B5, 2026-10-11): DSA's
  `run_diagnostics.py` aggregated a `ContextVar`-held trace of four record types
  (`ProviderRun` / `LLMRun` / `NotificationRun` / `HistoryRun`) sharing one
  `trace_id` for a single report request, and `run_flow.py` rendered those
  records into a four-lane node graph. Selene's run is a **phase pipeline**, not
  a report pipeline, so nothing is lifted verbatim: a run is re-bound to one
  `DailyTradingCycleWorkflow.run(trading_date)` execution, the record unit is the
  `PhaseOutcome` it already emits, and the trace id is the `WorkflowResult`
  `.workflow_id` it already mints — no new write-side model is invented. The
  four-value rollup (normal / degraded / failed / unknown) is preserved but
  mapped onto Selene's `WorkflowStatus`, with one Selene-specific case: a SKIPPED
  phase degrades the run even though `daily_cycle` finalises such runs to
  SUCCESS (it accepts SUCCESS-or-SKIPPED as a clean finish). Provider / LLM
  records are **additive, caller-supplied, optional** (Selene has no such
  recorder yet); absent them the retrospective degrades to phase-level. DSA's
  `NotificationRun` / `HistoryRun` have no Selene counterpart and are not ported.
  Selene-ized: the module is a **pure function** (zero IO, no clock — durations
  derive from each phase's own timestamps, so tests are deterministic),
  **fail-open** (never raises; an unexpected failure degrades to an `unknown`
  retrospective), and reuses `selene_space_security.redaction.redact_text` for
  text sanitisation (resolved lazily so importing the package stays cheap, with a
  minimal regex fallback if security-gateway is absent).
- Not absorbed (运行复盘): DSA's `run_diagnostics.py` ContextVar plumbing, its
  notification / history record types, and `run_flow.py`'s four-lane layout
  (re-bound here to Selene's canonical phase spine). Nothing is persisted — DSA
  is explicitly Phase-1 in-memory and fail-open, and Selene has no diagnostic
  store to align with, so none is invented; a future store is a separate item
  with its own migration. The retrospective is likewise **not** wired into any
  endpoint in this batch: it stays a library module, and exposing it on
  `/daily-cycle/run` is a later, standalone decision.

## tick-stock-panel / TSP (MIT)

- Upstream: https://github.com/shy3130/tick-stock-panel
- Authors: tick-stock-panel contributors (GitHub `shy3130`)
- License: MIT © 2026 tick-stock-panel contributors
- Upstream snapshot: `c5ffe38c7543b7478558a48168ba89c160c0c6df` (2026-10-11) —
  the local clone is shallow (single-commit snapshot), so this hash is the
  reference point for "absorbed as of".
- Absorbed (code, port): the A-share sentiment-cycle model —
  `backend/app/services/market_phase.py` (6 阶段 冰点/启动/主升/高潮/退潮/修复)
  → `packages/ashare/selene_space_ashare/market_phase.py`, and the limit-up
  ladder structure layer → `packages/ashare/selene_space_ashare/ladder.py`.
  Both are pandas rewrites of TSP's polars code (this package has zero
  `import polars`); the ladder run-length semantics mirror TSP
  `backend/app/indicators/pipeline.py:896-915`. Thresholds are **not** copied —
  selene re-calibrates all 22 constants from its own backfilled panel (17/22
  differ from TSP; see `MERGE_CHANGELOG.md` B1 段). Provenance headers
  preserved on both files.
- Absorbed (pattern, no code copied): the exchange abnormal-deviation 口径 of
  TSP `backend/app/services/abnormal_moves.py` — 偏离值 = 个股 N 日累计涨跌幅
  − 指数同期涨跌幅; 板块阈值 (主板3日±20% / 创科±30% / 北交所±40%; 严重异动
  10日+100%/−50%、30日+200%/−70%); 接近度分档 (≥1.0 触发 / ≥0.7 边缘 /
  ≥0.5 观察)。Reimplemented as pure functions in
  `packages/ashare/selene_space_ashare/abnormal.py` (selene feeds daily closes /
  live pct from its own `market_rules.board_of()`); carries a
  `Pattern absorbed from` marker. The per-board bench-index preference
  (`_BENCH_PREFERENCE`: 沪主板=上证A指 000002.SH、科创板=科创50 000688.SH、
  深主板=深证A指 399107.SZ、创业板=创业板综指 399102.SZ、北交所=北证50
  899050.BJ) likewise mirrors TSP `indicators/pipeline.py:_BENCHMARK_PREFERENCE`
  verbatim as a specification — the 口径 only means the same thing if the
  same benchmark is used. TSP reads precomputed polars columns — no line of
  it was copied.
- Absorbed (design reference, no code copied): the realtime 作战层 plane —
  TSP's L1 tick + intraday-anomaly concept informed selene's own
  `sources/astock_intraday_source.py`, `aggregators/astock_anomaly.py`, and
  `services/local-api/app/astock_anomaly_wiring.py`. These are Selene-original
  implementations (Sina endpoints, dependency-injected rule core, immutable
  state); TSP ships no equivalent Sina source, so nothing was ported. The
  planned B2 偏离/接近度 milestone reuses TSP `services/abnormal_moves.py`'s
  board-threshold 口径 (主板3日±20% / 创科±30% / 北交所±40%) as a specification.
- Not absorbed: TSP's polars runtime, TickFlow/fuyao data providers, its
  `_VETO_STATES` weak-market veto (selene's `regime` vocabulary cannot express
  it — declared a known limitation and pinned by test), and the
  `high_days_value` board encoding (documented as self-contradictory in TSP's
  own data model; selene uses `continue_num` instead).
- Attribution note: TSP is published at `github.com/shy3130/tick-stock-panel`
  and its LICENSE names `tick-stock-panel contributors`. The inline headers on
  the two ported files (`market_phase.py` / `ladder.py`) carry a different
  holder string and URL — `Copyright (c) 2026 papisoV` /
  `github.com/papisoV/tick-stock-panel` (`papisoV` is also this repository's
  own maintainer handle, and the `papisoV/tick-stock-panel` path does not
  resolve). The two ported files' headers are unchanged in this batch; this
  section is the authoritative record of the upstream.

## How to Verify Attributions

Run the license compliance checks:

```bash
# Root license files exist
test -f LICENSE
test -f NOTICE

# AGPL-3.0-only
grep -q "GNU Affero General Public License" LICENSE

# Third-party attribution records present
grep -q "Pnlclaw-community" ATTRIBUTIONS.md
grep -q "HKUDS" ATTRIBUTIONS.md
grep -q "tick-stock-panel" ATTRIBUTIONS.md
grep -q "tick-stock-panel" THIRD_PARTY_NOTICES.md

# Absorbed files carry their provenance header
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/market_phase.py
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/ladder.py
grep -q "Pattern absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/abnormal.py
```

## Updating Attributions

When adding a new dependency on third-party work:
1. Add an entry to this file (project name, upstream URL, authors, license)
2. Add an entry to NOTICE
3. If the dependency has NOTICE, preserve it verbatim
4. If the license is not MIT/Apache-2.0 (or is AGPL-3.0 and this repository
   remains AGPL-3.0-only), consult a maintainer before absorbing
