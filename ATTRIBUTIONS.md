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
  differ from TSP; see `the merge log` B1 段). Provenance headers
  preserved on both files.
- Absorbed (code, port → pandas rewrite): the concept *membership* layer —
  TSP's seed preset at `backend/app/services/ext_presets.py:33`
  (`_CONCEPT_DATA_URL` / `_INDUSTRY_DATA_URL`, the THS concept/industry
  exports) → `packages/ashare/selene_space_ashare/concept_map.py`. Divergences:
  TSP lands the pull in an `ext_gn_ths` extension table and enriches from it;
  this module parses the payload into a flat `symbol → concepts` frame and
  caches it on disk for 24 h. The endpoint is a single-host public GET
  maintained by the upstream author — no SLA, no documented licence — so it is
  treated as a degradable external dependency: a failed fetch logs and falls
  back to the cache (or an empty frame) and never raises.
- Absorbed (code, port → pandas rewrite): the concept-rotation matrix —
  TSP's equal-weight member daily return, concept blacklist, ≥3 covered-member
  floor, and the [7, 30] session window → `packages/ashare/selene_space_ashare/
  concept_rotation.py`. Divergences: the source reads polars-enriched parquet;
  this module reads selene's own daily base `("astock","daily_all","1d")`,
  derives the prior close inside the window, and takes membership as an
  argument rather than fetching it. Bars whose predecessor is more than one
  session earlier (a halt resumption carrying the whole suspended move) are
  excluded from both numerator and denominator.
- Absorbed (code, port → pandas rewrite): the mainline (主线) scoring —
  TSP `backend/app/services/market_mainline.py:compute_mainline_range`'s four
  cross-sectional features (limit-up count 0.35 / max boards 0.25 / rungs
  filled 0.25 / ≥2-board count 0.15), `(rank-1)/max(n-1,1)` normalisation ×100,
  `MIN_LIMIT_UP=3`, `TOP_PER_DAY=30`, `MAX_MEMBERS=600` →
  `packages/ashare/selene_space_ashare/mainline.py`. Divergences: board counts
  are recomputed from the close series via `market_rules.limit_up_price`
  rather than read from TSP's `consecutive_limit_ups` enriched column, so the
  streak is **window-bounded** and every response carries `streak_capped:
  true`; leaders break ties on symbol, not turnover (the daily base has no
  `amount` column).
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
- Absorbed (design reference, no code copied): the concept-level RPS idea —
  TSP's 「概念分析 → 涨幅RPS轮动」 dialog (`frontend/src/components/
  RpsRotationDialog.tsx`, backed by `backend/app/services/rps_rotation.py:
  build_rps_rotation`) ranks concepts by *per-day* return into a date × concept
  matrix; selene's `packages/ashare/selene_space_ashare/concept_rps.py` ranks
  concepts by their *trailing 120-session* equal-weight return instead, yielding
  a single percentile per concept (`rank(pct=True) * 100`). No TSP line was
  copied: the source reads polars-enriched `change_pct` columns and caches a
  matrix in-process, whereas this module reuses B3's `concept_rotation.py`
  return口径 over selene's own `("astock","daily_all","1d")` base and adds two
  things TSP has no equivalent of — a session-anchored (not row-shifted) window,
  and a published membership-snapshot drift bound (`DRIFT_NOTE` + the
  `thin`/`fair`/`solid` stability bands) that gates `is_strong`.
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

- Absorbed (rewritten Python; prompt text carried over) — B4-① 大盘总览 +
  AI 复盘 (2026-10-19). Read as specification: TSP
  `backend/app/services/market_overview_builder.py`,
  `backend/app/services/market_recap.py` (the system prompt lives inside it),
  `backend/app/services/dragon_tiger.py`, `backend/app/api/overview.py` and
  `backend/app/api/market_recap.py`. Wrote:
  `packages/ashare/selene_space_ashare/market_overview.py`,
  `market_recap_prompt.py`, `index_quotes.py`, `lhb_daily.py` and
  `services/local-api/app/api/v1/ashare_overview.py`.
  **What was carried over**: the recap `SYSTEM_PROMPT` is TSP's prompt text
  (52 of 54 lines identical, by design — it *is* the redline contract; the two
  changed lines substitute the module constant and the disclaimer f-string).
  **What was not copied**: all computation. TSP reads precomputed
  `kline_daily_enriched` columns
  (`ma5/ma20/ma60/vol_ratio_5d/consecutive_limit_ups/signal_*`), which selene
  does not have, so every indicator is recomputed from raw
  `("astock","daily_all","1d")` OHLCV + prior close. Divergences: `amount` is
  absent from the daily base, so the overview returns `amount_available:
  false` rather than 0; percentages are percent numbers throughout (TSP mixes
  fractions and percents); indices use a dedicated Sina table because
  `to_sina_symbol` rejects index codes by design. The redline survives as a
  module constant (`FORBIDDEN_PHRASES`) plus an idempotently appended
  `DISCLAIMER`, both pinned by tests. **龙虎榜 source divergence**: TSP's
  `dragon_tiger.py:1` is a `fuyao 专有` paid-source consumer, whereas
  `lhb_daily.py` reads the free Eastmoney datacenter report — same view,
  different provider, so row shape and throttling are selene's own. B4-②
  「盘前风向标」 was **not** absorbed: it also consumes a paid fuyao feed this
  deployment cannot reach.

- Absorbed (UI, independently written) — B4-① / B2 frontend surfaces
  (2026-10-20). TSP ships equivalents at
  `frontend/src/components/dashboard/widgets.tsx` (`EmotionRadar`,
  `BreadthBar`, `DistributionBars`, `IndexTicker`) and
  `frontend/src/pages/Review.tsx` + `AbnormalMoves.tsx` (streaming recap,
  deviation tab). Those files were **read as design reference only**. The
  selene surfaces are written against selene's own design tokens
  (`--color-gold`, `ink-*`, `.num`), its own i18n gate (`tr()` on every
  string), and its own data contracts, which diverge from TSP's in the
  places that matter: TSP's radar takes `score: number`; selene's renders
  `score: null` as 「—」 and suppresses the radar entirely on an empty
  cross-section; TSP mixes fractions and percent numbers across the same
  page, whereas selene keeps two separate formatters for the two payloads
  (overview = percent numbers, deviation = fractions). A line-level diff
  against the four TSP reference files found **no shared substantive line**
  (only two generic Tailwind wrapper `className` strings, 25+ chars, match).
  No TSP frontend code was copied, so no provenance header is carried on
  these files; this entry records them as design-derived rather than ported.

- Absorbed (pattern, no code copied) — B / 交互式选股 (2026-10-20). TSP's
  screener was read as **specification**: `backend/app/services/screener.py`
  (555 lines, `ScreenerService`), `backend/app/api/screener.py` (1176 lines),
  `frontend/src/components/screener/ScreenerFilter.tsx` and
  `frontend/src/pages/Screener.tsx`. What was carried over is the **condition
  vocabulary and its semantics** — which fields a Chinese A-share trader
  actually filters on (涨跌幅区间 / 连板数 / 量比 / 成交额 / 流通市值 /
  板块 / 排除 ST / 均线站位 / 60 日新高低 / 涨停), inclusive ranges, the
  ST matcher `(?i)ST|退` (selene implements it case-sensitively as
  `"ST" in name or "退" in name` in `market_rules.is_st`; A-share names are
  uppercased upstream, so the two agree on real symbols — the narrowing is
  recorded rather than relied on silently), and the "empty input = no
  constraint" rule for
  numeric fields. **What was not copied is all of the execution**: TSP loads
  a precomputed `kline_daily_enriched` polars frame into DuckDB and runs a
  caller-supplied `WHERE` fragment (`CustomRequest.conditions: list[str]`);
  selene has no enriched table, computes every indicator in
  `compute_baseline`, and screens a ~4–5k-row cross-section with boolean
  masks in `packages/ashare/selene_space_ashare/screener.py`. Zero new
  dependencies (no DuckDB, no polars), and **no user-supplied SQL** — that
  was proposal B, explicitly declined. Divergences that change behavior:
  (a) a numeric bound **fails** any row whose value is `None`/NaN (TSP's
  `applyFilter` coalesces a missing value to 0 — `(r.change_pct ?? 0) * 100`,
  `(r.amount ?? 0) / 1e8` — so a name with no reading is silently scored as
  "0% / 0 元" and passes any floor of 0 or below, and fails a positive floor
  for the wrong reason); (b) 成交额 has two bases — the snapshot's reported `amount` vs. a
  `close × volume` estimate — and each row carries `turnover_est`;
  (c) TSP's `boards` filter is a *market board* (主板/科创/创业/北交), so
  selene names that field `board_types` and keeps `boards_min`/`boards_max`
  for the consecutive-board streak, because `row["boards"]` is the streak
  count in `build_quote_rows`; (d) the session scored must clear a
  coverage floor (`MIN_COVERAGE`), walking back from a half-written newest
  session (measured 2026-10-20: 67/5574 = 1.2% → score 10-19 at 71.3%).
  The selene endpoint (`services/local-api/app/api/v1/ashare_screen.py`,
  `POST/GET /api/v1/ashare/screen`) is written against selene's own
  contracts; it reuses `build_quote_rows` and `_baseline_for` so the
  screener and the overview can never disagree about the same stock on the
  same day (that claim is pinned end-to-end by
  `TestSharedRowContract` in `packages/ashare/tests/test_screener.py`, which
  feeds the real `build_quote_rows` output to both consumers — a hand-built
  row fixture could drift from the producer and stay green). The frontend
  (`components/watch/parts/ashare-screen-panel.tsx`)
  is original — TSP's filter UI is a separate full page with a DuckDB-driven
  result grid; this one is a panel inside 看盘台 that issues a POST. Files
  carry the `Absorbed from tick-stock-panel` /
  `Pattern absorbed from tick-stock-panel` provenance header accordingly.

- Absorbed (rule table + reform date, no code copied) — 涨跌停 band unification
  (2026-10-22). Read as specification: TSP `backend/app/price_limits.py`
  (band constants, `board_limit_pct`, `price_limit_pct`,
  `polars_price_limit_pct`, `polars_is_risk_warning_name`) and the rule
  commentary in `backend/app/services/abnormal_moves.py:1-13`. What was
  carried over is the **rule table and one date**, not any implementation:
  the board × risk-warning matrix (main 10%; main risk-warning 5% *before*
  2026-07-06 and 10% from it; 创业板/科创板 20%; 北交所 30%), the 风险警示
  matcher `"ST" in name.upper()`, and
  `MAIN_BOARD_ST_LIMIT_CHANGE_DATE = 2026-07-06`. Reimplemented from scratch
  as `packages/risk-engine/selene_space_risk/price_limits.py` in pure
  `decimal.Decimal` (no polars, no numpy): TSP ships three parallel
  implementations of the same table (scalar Python, a polars `Expr`
  builder and a numpy matrix writer) because it computes inside polars/numpy
  pipelines; selene has one scalar rule that its own vectorised callers index
  into, so no expression builder was needed. **Caveats recorded rather than
  glossed**: (a) the 2026-07-06 date is taken on TSP's citation of
  上交所《交易规则(2026年修订)》 — it was not independently re-verified
  against a primary source in this round; (b) TSP's 注册制 no-limit window
  (`no_limit_window_days` / `is_no_limit_day`, gated on an
  `instruments.listing_date` dimension) is **not** absorbed — selene has no
  `listing_date` data anywhere, so the first sessions of a new listing still
  get the board's ordinary band; documented in the module docstring.
  `market_rules.py` keeps its `daily_stock_analysis` header (its ST/board
  surface predates this batch); the band arithmetic it delegates to lives in
  the risk package and carries the TSP header.

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
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/concept_map.py
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/concept_rotation.py
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/mainline.py
grep -q "Pattern absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/abnormal.py
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/concept_rps.py
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/market_overview.py
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/market_recap_prompt.py
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/index_quotes.py
grep -q "Absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/lhb_daily.py
grep -q "Pattern absorbed from tick-stock-panel" packages/ashare/selene_space_ashare/screener.py
grep -q "shy3130/tick-stock-panel" packages/ashare/selene_space_ashare/screener.py
grep -q "Absorbed from tick-stock-panel" services/local-api/app/api/v1/ashare_screen.py
grep -q "shy3130/tick-stock-panel" services/local-api/app/api/v1/ashare_screen.py
grep -q "class TestSharedRowContract" packages/ashare/tests/test_screener.py
grep -q "shy3130/tick-stock-panel" packages/ashare/selene_space_ashare/market_overview.py
grep -q "Absorbed from tick-stock-panel" packages/risk-engine/selene_space_risk/price_limits.py
grep -q "shy3130/tick-stock-panel" packages/risk-engine/selene_space_risk/price_limits.py
grep -q "MAIN_BOARD_ST_LIMIT_CHANGE_DATE" packages/risk-engine/selene_space_risk/price_limits.py
```

## Updating Attributions

When adding a new dependency on third-party work:
1. Add an entry to this file (project name, upstream URL, authors, license)
2. Add an entry to NOTICE
3. If the dependency has NOTICE, preserve it verbatim
4. If the license is not MIT/Apache-2.0 (or is AGPL-3.0 and this repository
   remains AGPL-3.0-only), consult a maintainer before absorbing

### 因子 warmup / calc_requires 接线（2026-10-22）

- 来源：TSP（MIT）`backend/app` 因子定义表的元数据做法（`calc_requires` 声明 +
  warmup 属因子契约）。**未复制代码表达**：`packages/ashare/selene_space_ashare/factor_warmup.py`
  为 selene 新写；`FactorDef.warmup_bars` 为 selene 字段；窗口数值按 selene
  `factor_calc.py` 的实际 rolling window 独立核定。
- `get_daily_klines` 的 `start_date` 闸门缺陷是 **selene 自身**的 bug（不是 TSP 的），
  修复不涉及归属。
- 记录：`the merge log` 因子 warmup 段；`packages/ashare/PROVENANCE.md` 同段。

### 回测成交拦截(涨跌停封板闸门)(2026-10-22)

- 来源:TSP(MIT)`backend/app/backtest/engine.py` 的封板买入拒绝 + 跌停卖出顺延语义
  (`pending_exit`)。**未复制代码表达**:`backtest_gates.py` 为 selene 前向收益框架新写,
  两道闸门为面板级 DataFrame 变换;封板价格判定复用本仓 `price_limits`(吸收项 1)。
- 记录:`the merge log` 回测成交拦截段;`packages/ashare/PROVENANCE.md` 同段。
### A股缓存崩溃安全 — 原子写 + 不可读即 miss（2026-10-23）

- 来源：TSP（MIT）`backend/app/services/fs_utils.py`（`atomic_write_text` /
  `atomic_write_parquet`）与 `backend/app/tickflow/repository.py:43-67`
  （`replace_with_retry`）。**概念级吸收，零代码复制** —— selene 的实现是
  `atomic_replace(path, writer)` 回调形式，上游是「先写 `<name>.tmp` 再 replace」
  的两个具体函数（text / polars parquet），没有可移植的字节级 API 抽象；
  selene 用 pandas/pyarrow，故需要一个不假设格式的写入回调。
- 重试参数与上游一致：**10 次 × 0.5s**。之所以不能取更短的预算：预算必须盖住
  **最慢的读端**，而 selene 最大的原子写目标是 44 MB 的 `financials_v3.json`，
  单次解析就可能接近一秒；0.7s 会在这个目标上掷骰子。
- 判据本身不同：上游防的是 polars/DuckDB 扫描期间持句柄；selene 的读端是
  `pd.read_parquet` / `json.load`，同样是「目标被读端打开时 Windows 拒绝 replace」
  的同一类竞态，故同一个重试结构适用。
- 落点：`packages/core/selene_space_core/infra/atomic_write.py`（原语）；
  `data_fetcher.py` / `ths_data.py` / `fundamentals_pit.py`（四个缓存写端 + 两个
  读端守卫）。
- 一处**刻意不照搬**：上游的 `atomic_write_text` 支持 `mode`（凭证文件在 replace
  **之前** chmod，避免「以默认权限落盘」的窗口）。selene 的这四个目标是 A股行情
  缓存，不是凭证，没有 `mode` 需求；加一个恒为 `None` 的形参只是噪音。若将来有
  密钥落盘，需要的是 `os.open(..., 0o600)` + chmod-on-temp，不是这里的缓存路径。
- 记录：`the merge log` A股缓存崩溃安全段；`packages/core/PROVENANCE.md` 与
  `packages/ashare/PROVENANCE.md` 同段。

### 盘中快照判别（daily bar snapshot vs close）（2026-10-22）

来源：TSP（MIT）`backend/app/services/data_integrity.py` 的 `CLOSE_CUTOFF` /
快照行判别概念 —— 用一个「会话日 + 收盘后截止时刻」的判据把盘中快照行与
权威收盘行分开。**未复制代码表达**，且**机制不同**：TSP 每行带上游 `quote_ts`
并对该时刻判定；Sina 日线端点不发布任何逐行时刻，故 selene 的诚实代理是
**取数时刻**本身。

- 新模块 `packages/core/selene_space_core/session_clock.py`：
  - `daily_bar_is_final(session, *, as_of)` —— 纯日期/时间规则（不涉及事件类型）。
    会话日早于今天即终版；晚于今天（时钟偏移或错戳）永不算终版；等于今天则看
    `as_of` 是否已过 `DAILY_SESSION_SETTLES_AT = 15:05`。该常量刻意与
    `selene_space_ashare.daily_all.EOD_TIME` 同值 —— 两个回答「这一场何时结束」
    的常量迟早会打架。
  - `bar_payload_differs(stored, incoming)` —— OHLCV + `closed` 的逐项比较，供
    「同一时间戳是否是新版本」的判据复用（core 不能依赖 `selene-space-types`，
    故签名取 `Mapping`）。
- **规则归 core、赋值归各解析器**：`market-data` 与 `ashare` 都依赖 `core`，
  互不依赖；两份「是否终版」的实现必然漂移，而存储一旦相信半成品 bar 是终版
  就再也发现不了。
- 接线：
  - `packages/market-data/selene_space_market/sources/astock_source.py` ——
    `_parse_rows` 不再无条件 `closed=True`；`_poll_loop` 的去重从「只比时间戳」
    改为 payload 比较（`_is_publishable`），同日重述替换 `_kline_history` 末行
    而不是追加。
  - `packages/ashare/selene_space_ashare/sina_daily.py` —— `parse_rows` 同规则，
    新增 `as_of` 形参以便测试可复现。
  - `packages/market-data/selene_space_market/kline_manager.py` ——
    `sync_incremental` 的尾段过滤放行「同 `latest_ts` 但 payload 变化」的 bar
    （只对边界那一根查一次 `KlineDB.get_bar`，低位 bar 属空洞，归 gap sweeper）。
- **消费端刻意不改**：`astock_anomaly_wiring._settled_bars` 与
  `astock_deviation._settled_stock_series` 仍按「今日零点」切。`prev_close` 的
  语义是**上一场**收盘；若改按 `closed` 过滤，15:05 之后今日自己的收盘就会被
  当作基准，涨跌停判定反而恒不触发。`closed` 只让生产者不再撒谎，不改变基准口径。
- 验证：`packages/core/tests/test_session_clock.py` 22 条 +
  `packages/market-data/tests/test_daily_bar_restatement.py` 10 条；
  `test_sina_daily.py` 补 3 条；market-data + core **757 tests 0 fail**、
  ashare **990 tests 0 fail**；变异 **9/9 全杀**。
- **一处改动被撤回**：原打算把 `sync_incremental` 的合并去重从 `setdefault`
  改成「后者覆盖」。变异探针没能杀死它，追查后确认是**设计上不可达** ——
  尾段 `fresh_tail` 只保留 `timestamp >= latest_ts` 的行，深历史段锚在
  `oldest_ts - 1`（空分区时锚在 `min(fresh_tail) - 1`），严格落在其下，
  两段永不同时间戳。既然碰撞不会发生，改去重方向既无行为也无测试可依，
  保持原样，不写做不到的注释。
- **`_as_cst` 的 naive 分支不挂测试**：朴素 datetime 直接原样返回。对唯一调用方
  `daily_bar_is_final` 而言，它比较同一时刻的 `.date()` 与 `.time()`，两者随
  偏移一起移动 —— 给朴素值附上任何偏移（包括错的 UTC）都是 no-op，没有行为
  可保护，故该分支不设测试并在 docstring 里写明理由。aware 分支（
  `astimezone(CST)` → `astimezone(UTC)`）有牙，已被变异打红。

### 进程内长驻任务存活探针(`health` 组件 `task_liveness`)(2026-10-23)

- 来源:TSP(MIT)`backend/app/watchdog.py` 的「后台任务存活探测」概念。**未复制代码表达**,
  且**只吸收一半**:TSP 的 `os._exit(70)` 依赖会拉起进程的 supervisor,selene 不吸收
  (compose 的 `restart: unless-stopped` 只在**进程退出**时触发,不是 unhealthy 时重启;
  本地 `dev.py` 也不重启)。照搬会得到「进程消失且不回来」。
- 判据本身不同:TSP 探的是共享写锁是否被毒化(其 polars 数据管道),selene 探的是
  **协程是否已结束** —— asyncio 只在垃圾回收时写成一行日志、没人读的那类故障。
  **不覆盖**「任务存活但卡在 await 里»,那需要每循环心跳,不在该组件声称的范围。
- 落点:`services/local-api/app/core/task_liveness.py`(注册为 `task_liveness` 健康组件)。
  放 local-api 而非 core:它要读 lifespan 里 `create_task` 产生的 `asyncio.Task` 句柄,
  句柄只存在于 `app.main` 的 `state`。
- 记录:`the merge log` 进程内长驻任务存活探针段;`packages/core/PROVENANCE.md` 同段。

### LLM 流语义守卫 — 截断 / 空答 / 断流不再冒充完整回答（2026-10-23）

- 来源：TSP（MIT）`backend/app/custom/assistant/streaming.py:36,114-181`
  （`_LENGTH_FINISH_REASONS`、`round_end.finish_reason`、两条 `RuntimeError`）。
  **概念级吸收，零代码复制** —— 上游是 LangChain `AIMessageChunk` 的
  `finish_reason` 归一化 + 一个生成器，selene 是裸 SSE / NDJSON 解析器，没有
  可移植的中间层；吸收的是**判据**：`finish_reason` 必须随流走到终点，且只有
  干净停止才算答案。
- **上游形态与 selene 不同**：TSP 把 `finish_reason` 挂在 `round_end` 事件上、
  由消费方解读；selene 的 `chat_stream` 只 yield 字符串，没有地方携带这个信号
  —— 所以判据被收进 `stream_guard.settle_stream`，在**最后一个 chunk 之后**
  抛异常。关键取舍：**文字仍交付**，抛异常只用来告知「这不是完整答案」；若在
  中途抛，调用方会连已经拿到的真实文本一起丢掉。
- 判据顺序不同且有原因：**先判空答、再判截断**。推理模型可能烧光预算却没产出
  一个正文字符；此时建议「提高 Token 上限」是误导，调用方需要知道的是「什么
  都没回来」。
- **一处 selene 独有、上游没有的缺陷**：`LLMRouter` 的中途降级会把**第二个
  模型**的完整回答拼接到第一个模型已交付的半截之后，形成一条没有任何接缝标记
  的回答（探针实测：
  `'AI 复盘前半段, 到这里连接断了完整答案从头开始'`）。修复为发过文字就不再
  换源，改为抛 `LLMStreamInterruptedError` 让调用方自己决定重试或披露。
- Ollama 语义与 OpenAI 不同：干净停止只给 `done=true` 不给 `done_reason`，故
  「没有 reason」**不能**当作不完整；只有从未到达 `done` 帧才算断流。`num_predict`
  是它的输出上限，与 `length` 同族。另：Ollama 用 **200 + `{"error": ...}`** 报告
  失败，不读这个键就会把「拉取模型」「上下文超限」变成一句误导性的「流被截断」。
- 落点：`packages/llm-adapter/selene_space_llm/stream_guard.py`（新原语）；
  `base.py`（三个异常 + `chat_stream` 契约）；`openai_compat.py` / `ollama.py`
  （采集并上报停止原因）；`router.py`（拒绝拼接）。
- **`[DONE]` 与「连接没了」是两回事**：SSE 的 `[DONE]` 是服务端**自己写下的**
  结束符，body 自然耗尽则是连接消失。两者都让循环退出，只看循环的调用方无法区
  分，所以 `settle_stream` 收一个 `ended_explicitly` 参数：有 `[DONE]`、模型没
  报截断，就算完整；**没有** `[DONE]` 也没有 `finish_reason` 才判为断流。
  早先的实现把这两种都判成断流，连干净但沉默的端点也会被拒。
- 记录：`the merge log` LLM 流语义守卫段；`packages/llm-adapter/PROVENANCE.md`
  同段。

### 追加：传输层空消息（同日自查发现）

- `stream_guard.describe_transport_failure`：httpx 在连接被掐断时抛
  `ReadError('')`，直接插值会渲染成 `"Stream failed: "` —— 恰好在本守卫要说清
  「你的回答被截断了」的场合什么也没说。空消息回退到异常类型名。
- 该缺陷是**既有代码**（`f"Stream failed: {exc}"` 本就如此），但新守卫把「告诉
  调用方为什么停了」当作交付目标，空消息使这个目标落空，故纳入本次范围。
- 验证更新：138 条测试全绿；变异 15/15 全杀；另把渲染回退成旧写法单跑一次，
  新增测试如期转红（有牙），文件按 sha256 原字节还原。

### 追加：不可降级回答的调用方（评审 F2）

- 受影响调用方：`packages/market-analysis` 的 `flow.py:344-363`（stage1）与
  `flow.py:448-467`（stage2）。此前若流在 `finish_reason=length` 处结束，这段被
  截断的文本仍会走 `normalize_llm_json(..., "lenient")` 并**通过契约校验**，然后
  当成完整诊断走完 Stage。
- 现状：`settle_stream` 在已交付文字后抛 `LLMTruncatedError` →
  `llm_provider.py:137` 包成 `ProviderError` → `flow.py:232` 落 partial record
  并重新抛出。即**半截回答不再被当作完整诊断采用**，该次运行以带 `exception`
  字段的 partial 记录结束，可重试。
- 该风险**已实测**，不是推断。对真实 payload 每 5% 切一刀再走 lenient + 契约校验：
  Stage1Diagnosis 19 个切点中 5 个被接受（25/35/45/75/95%），Stage2Decision 中 4 个
  （40/45/80/85%）。被接受的样本恰是**截断处的字符串被原样吞下**——
  `"dispatch_playbooks": ["tren"]`（`trend_follow` 的前 4 个字符）作为合法值进入
  决策，`trend_context` 则被填成 `{}` 默认值。也就是说，抢救路径不仅会采用半截回答，
  还会让**半截字符串伪装成完整枚举值**。
- 这是有意的行为变更。救回半截 JSON 会把「模型只说了一半」伪装成「模型说完了」，
  而 Stage1/Stage2 的结论会被下游当作完整结论消费。代价是这类请求不再「侥幸成功」、
  partial 记录会变多；换来的是落盘记录里的 `exception` 字段如实反映真相。
- 若将来确要保留抢救路径，正确做法是在 partial record 上标记 `truncated=True`
  并要求下游拒绝消费；仅靠 lenient 解析会静默放行被切断的字符串值。
- 探针脚本：`<local-path-redacted>`（非版本控制，结论已抄录于此）。
- 验证更新：141 条测试全绿（含本轮新增的同行 chunk 信号、Ollama
  `done_reason:"length"`、错误帧不产出空片段三例）；变异 18/18 全杀。
### 工作台布局跨设备同步 + 预设随版本重播（2026-10-25）

- 来源：TSP（MIT）`frontend/src/components/dashboard/useDashboardLayout.ts` 与
  `backend/app/services/preferences.py:1132-` （`set_dashboard_layout`）。**概念级吸收，零代码复制。**
- 吸收的契约有三条，逐条对齐：
  1. **本地优先、后端兜底**：本地有改动就用本地，否则用后端保存的，再退内置默认；
  2. **800ms 防抖落盘**，失败静默（布局仍在本次会话生效，刷新后回退上次保存）；
  3. **后端只防脏数据**：条数上限、字段类型与取值范围、整体 JSON 体积；语义校验
     （未知组件剔除）留在前端渲染前。
- 一处**结构性差异**：TSP 是单看板布局（`dashboard_layout` 一个 blob，与内置默认
  完全一致时存 `null` 让内置默认可随版本演进）。selene 是**多布局 + 预设**
  （`signal` / `research` / `trade-watch` 由种子写入 localStorage），所以「改动
  是否等于内置默认」这个判据在多布局下不成立 —— 用户可能只改了其中一个预设。
  selene 改用**内容签名**：写入种子时记下「我最后写进去的是什么」，下次启动时
  签名一致 = 用户没动过 → 可以被新版预设覆盖；签名不一致 = 用户改过 → 保留。
  这正是 TSP「与默认一致时存 null」的等价推广，只是从「与唯一默认比对」变成
  「与上次写入物比对」。
- **未照搬 `null = 恢复默认`**：selene 的删除语义已由墓碑承担（删掉的布局不得
  被同步复活），没有「恢复内置默认」入口，强行引入 `null` 会让「用户删光」与
  「回到默认」两种意图撞车。
- **墓碑是 TSP 没有的**：TSP 的单布局 blob 被覆盖即消失，不存在「从另一台设备
  搬回来」的问题；多布局集合必须显式记录删除时刻，否则合并时远端会把已删布局
  当新增塞回来。
- 落点：`apps/desktop/lib/workspace/workspace-layouts.ts`（签名重播、墓碑、
  `mergeLayouts`）、`apps/desktop/lib/hooks/use-workspace-layout-sync.ts`（启动
  合并 + 防抖同步）、`apps/desktop/components/workspace/workspace-layout-sync.tsx`
  （挂在 `/workspace` 布局层）、`services/local-api/app/core/settings_service.py`
  （`workspace_layouts` 一节 + 清洗）、`services/local-api/app/api/v1/settings.py`
  （`WorkspaceLayoutsUpdate`，形状错误在 422 层拒掉）。
- 复用而非重写：写入沿用仓内既有的 `atomic_write`（与 `_write_settings_file` 同一
  原语），不引入第二套落盘方式。
- 记录：`the merge log` 工作台布局跨设备同步段；`packages/core/PROVENANCE.md`
  同段。

### 概念成分下钻 — 虚拟化成分表 + 显式截断（2026-10-25）

- 来源：TSP（MIT）`frontend/src/components/DimensionMembersDialogContent.tsx` 与
  `backend/app/api/ext_data.py:624-681`（`/{config_id}/dimension-members`）。
  **概念级吸收，零代码复制。**
- **吸收的契约**（逐条对齐，非照搬）：
  1. **成分明细必须能下钻**：看盘台只显示概念成员数，用户看得到「3,888 只」却打不开。
  2. **大表必须虚拟化**：TSP 用 `@tanstack/react-virtual`，`estimateSize` 固定行高、
     `getItemKey` 用 symbol、`overscan` 个位数。
  3. **`total` 与 `rows` 分离**：服务端 `limit` 截断，另报完整成员数，前端明说
     「共 N 只，显示前 M 只」。
- **一处刻意的机制差异**：TSP 的 `/dimension-members` 是**通用扩展字段**成员查询
  （`config_id` + `field` + `value`），前端再拿全市场快照在**客户端** join 行情；
  selene 是概念专用端点，**服务端 join**。理由：selene 已有 `("astock",
  "market_snapshot")` 全市场快照源和 `daily_all` 日线底座，服务端 join 才能让
  「取前 1000」等于**真正的涨幅前 1000**，而不是快照偶然吐出的前 1000 —— 客户端
  join 先截断再排序会把「top N」变成「frame 顺序的 N」。
- **排序口径沿用 TSP**：涨幅降序 / 升序 / 成交额降序 / 名称；无价行在**两个方向都
  排最后**（TSP 用 `-Infinity` / `Infinity`，selene 用显式 `null` 分支，结果相同）。
  无价成员**永不丢弃** —— 丢掉它会让概念看起来比实际小。
- **TSP 有、selene 未吸收**（各有理由，不是遗漏）：
  - **分钟分时图 + 板块/大盘对比**（`IntradayChart`，lightweight-charts）：本仓
    已有 `ashare_deep` 的 fund-flow/margin 面板承担个股维度；在成分表弹窗里再塞一张
    图会把「查这只票在不在这个概念里」的核心任务挤到次要位置。若要，应做成独立
    「板块分时」入口，而不是弹窗第二屏。
  - **点击成分行跳转行情页**（`onStockClick` + `toNavItems`）：selene 的看盘台
    没有与之等价的「跨页列表导航栈」，接一个单跳按钮会得到一个回不去的页面。
    属后续工作，不是本面板的缺失。
- **重复对是真缺陷，不是理论风险**：实测成分表 92,286 行 / 5,580 只 / 1,202 个概念，
  其中 **669 个 `(symbol, concept)` 重复对**。`len(matched)` 或朴素 join 会静默
  重复计数；行在计数前按对去重，`total` 在 join **之后**计算，使无行情的成员仍被
  计入（它存在，只是没价）。
- **数据规模（2026-10-25 实测）**：最大概念 3,888 只，>1,000 的 9 个，>200 的 107 个，
  中位数 20 只。`limit=1000` 时响应体 **138.7 KB**（实测，非估计）。
- **行情口径**：快照优先、日线回落，与 `ashare_overview` 同一规则同一理由；
  `quote_source`（`snapshot`/`daily`/`none`）+ `as_of` 显式渲染，收盘后绝不暗示实时。
- 落点：`services/local-api/app/api/v1/ashare_concept.py`（端点）、
  `apps/desktop/components/watch/parts/concept-members-dialog.tsx`（虚拟化弹窗 +
  懒挂载触发器）、`ashare-board-page.tsx` 概念名点击处。
- **前端依赖新增**：`@tanstack/react-virtual@^3.14.22`（MIT，与 TSP 同库），lockfile
  仅新增 `react-virtual` / `virtual-core` 两个包共 28 行。
- 记录：`the merge log` 概念成分下钻段；`packages/ashare/PROVENANCE.md` 同段。
