<div align="center">
  <img src="docs/assets/selenequant-banner.webp" alt="SeleneQuant — Continuous Adaptive Quant Research" width="100%">

  <h3>A local-first AI quant research organization. Questions are persistent, conclusions are versioned, and the past changes the next call.</h3>

  <p>Most research tools store answers. SeleneQuant keeps the <em>question</em>, runs real experiments against it, versions every conclusion, and recalls what was already established when the next question arrives. That loop is run by an AI research organization, on top of a full quant stack, with the statistics to know when a result is real.</p>

  <p>
    <a href="#four-species"><img src="https://img.shields.io/badge/What_it_is-1f6feb?style=for-the-badge" alt="What it is"></a>
    <a href="#the-loop-runs-on-a-real-quant-stack"><img src="https://img.shields.io/badge/Platform-0b1219?style=for-the-badge" alt="Platform"></a>
    <a href="#what-works-today"><img src="https://img.shields.io/badge/Status-early_stage-F7A07D?style=for-the-badge" alt="Status: early stage"></a>
  </p>

  <p>
    <a href="https://github.com/papisoV/selenequant/actions/workflows/ci.yml"><img src="https://github.com/papisoV/selenequant/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg" alt="License: AGPL-3.0-only"></a>
    <img src="https://img.shields.io/badge/Python-3.11%2B-blue.svg" alt="Python 3.11+">
    <img src="https://img.shields.io/badge/Node-20%2B-3c873a.svg" alt="Node 20+">
  </p>

  <p><a href="README.md">English</a> · <a href="README.zh-CN.md">中文</a></p>
</div>

<img src="docs/assets/hero.svg" alt="SeleneQuant research loop: a question, versioned conclusions that cite prior ones and their evidence, and memory recalled into the next call" width="100%">

---

> **Repository posture.** This public repo tracks an internally developed project as a series of curated, verified snapshots rather than its full history. A pull request therefore has no branch here to merge into — a mechanical limit, not a closed door. Issues are welcome and read, and a proposed change can be absorbed into a future snapshot with credit (see [CONTRIBUTING](.github/CONTRIBUTING.md)).

## Four species

Tools in this space look alike until you ask what each one does with time. Four species sit side by side, and after reading them you should see they are not the same kind of thing:

| Species | The deal |
|---|---|
| Trading / quant software | I provide tools, you operate. |
| AI financial chatbot | You ask, I answer. |
| AI quant agent | You assign a task, I execute it. |
| **SeleneQuant** | **You hand over a question; it keeps researching. Reality hands back results; it carries them into the next judgment.** |

The dividing line is not memory. Every modern tool has context, files, or memory; Obsidian plus Git stores structured history too. The line is that stored history is **retrieved automatically and changes the next call**. Save without recall is a research archive; save with recall is a research organization.

Against the chat species, the same line looks like this:

| | A chat assistant | SeleneQuant |
|---|---|---|
| Unit | one conversation | one persistent question |
| History | each question is isolated | every conclusion carries the question's full history |
| Output | a block of text | a versioned conclusion plus what it cited |
| Result | the answer ends the interaction | real outcomes flow back and change the next version |

## Quick start

Requirements: **Python 3.11+** and **Node 20+**. Works on Windows, macOS and Linux.

```bash
git clone https://github.com/papisoV/selenequant
cd selenequant
pip install -r requirements.txt

# recommended: starts the backend and the web app, waits for the health check
python dev.py --prod
```

`python dev.py --prod` sets the workspace `PYTHONPATH`, starts the API, builds the web app once if the build is stale, and serves it. When it prints the URLs:

- backend API and docs: `http://127.0.0.1:8088/docs`
- web app: `http://localhost:3000`

**Use `--prod` to run the app; drop it to edit the frontend.** Without it (`python dev.py`), Next.js compiles each page the first time you open it — with ~142 routes that is what makes a fresh session feel slow. `--prod` builds once (skipped while the build stays fresh) and then serves precompiled pages.

Open the web app through **`localhost`**, not `127.0.0.1`. Next.js dev origin protection rejects the bare IP and the page will hang on "Connecting to runtime".

On Windows you can start `dev.bat` instead (same thing, batch launcher). It needs no configuration; set `SELENE_PROXY_URL` in your environment only if you route traffic through a proxy.

<details>
<summary>Manual two-terminal start</summary>

```bash
# Terminal 1: backend
call activate.bat
cd services/local-api
uvicorn app.main:app --host 127.0.0.1 --port 8088

# Terminal 2: web
cd apps/desktop
npm install
npm run dev
```

</details>

> **Do not run `pip install -e .`.** It writes an absolute path into your global `site-packages` that points at whichever copy you installed from. In a multi-copy setup that silently loads the wrong source. Use `pip install -r requirements.txt` and let `activate.bat` set `PYTHONPATH` instead.

## What it does

A question in SeleneQuant is a living object. You open it; Selene runs research against it (experiments, evidence, conclusions); each conclusion is a numbered **revision**. A new revision can cite the one before it and the evidence behind it. Nothing is overwritten, and nothing pretends to know more than it did at the time.

A real research line from the author's library (translated from Chinese):

> **Q: Why did BTC bounce recently? The rate hike on Sep 17 should have been bearish.**
>
> **v1** · cites nothing · *The hike was already priced in; the landing cleared uncertainty.* BTC had already fallen to a monthly low near $75.6K on Sep 15-16, with a single-day ETF outflow of $450M (the largest since June); the market had priced a 25bp hike at ~93% before the decision. The hike landing triggered a bounce to $77K.
>
> **v2** · **cites v1** · *Building on v1, the bounce's quality is doubtful.* All three demand legs are weak: Coinbase premium at a monthly low, corporate treasuries buying ~5,900 BTC over three months, ETH/XRP ETFs flipping to net outflow. If "bounce after hike = clearing" holds, spot inflows should confirm it; if BTC falls back below $75K without premium recovery, v1's clearing thesis is wrong.

And a deeper line, one question answered by four experiments, one of which overturned an earlier verdict:

| # | Experiment | Verdict | Key number |
|---|---|---|---|
| 1 | Funding factor family, 4-bar IC sweep | NO-GO | \|IC\| ≤ 0.02 across the family |
| 2 | Funding multi-horizon IC sweep | **reversal** | 7d clears 0.10 for all symbols |
| 3 | Funding entry-veto, gate 1 | veto fails | 3243 tagged trades, win rate flat across all 7 buckets |
| 4 | Funding carry + reversal sim, gate 2 | INCONCLUSIVE | ETH+SOL pooled n=203, CI95 crosses zero |

The single conclusion that follows cites all four experiments by id.

## Architecture

The whole product is one loop. A question persists; research produces evidence and numbered revisions; each revision can cite the ones before it and their evidence; a stored conclusion is recalled automatically, so the next call starts from what was already established rather than from zero.

<img src="docs/assets/research-loop.svg" alt="The SeleneQuant research loop: a persistent Question feeds Experiments that produce evidence and verdicts; each Experiment feeds a numbered Revision; revisions are stored in Memory with what was concluded and why; Memory is recalled automatically into the Next call, which cites and revises the conclusion." width="100%">

<img src="docs/assets/research-today.png" alt="The SeleneQuant daily desk (/research/today), rendered live: the five-phase research cycle with its running counts (questions 36, missions 36, investigations 33, experiments 2, decisions 1, outcomes 0, memory 21); a LAST HANDOFF panel; a NEEDS YOU panel with one item awaiting a human decision, an ETH/USDT PA heartbeat diagnosis; and a SELENE IS WORKING panel listing 35 items in flight." width="100%">

<img src="docs/assets/research-memory.png" alt="The SeleneQuant memory layer (/research/mem), rendered live: 4 stored knowledge entries and 17 unvalidated leads. Every entry is a hypothesis or a conclusion, stamped with when it was fixed and how many times it changed, and chained to the evidence or question id it came from — including a funding-crowding hypothesis marked refuted, and a four-verdict conclusion recording |IC| <= 0.02." width="100%">

## The loop runs on a real quant stack

The loop is the product. Underneath it sits a platform built through years of daily research use, and most of it is real, runnable code rather than roadmap:

<img src="docs/assets/quant-stack.svg" alt="The SeleneQuant platform: Market data (klines and ticks from 6 sources, derivatives, order flow from 13 aggregators, news / Telegram / macro / funds) feeds Engines (strategy engine with 49 templates, backtest with realistic costs, anti-overfit statistics such as DSR, PBO and purged K-fold), which feed an AI research organization (a three-perspective council, versioned conclusions, and a five-phase daily cycle). The organization is delivered as a web app with 142 pages, a CLI with 8 command groups, and a FastAPI service with 523 endpoints." width="100%">

### The AI research organization

Most "AI quant" products are a single agent answering in a chat window. Selene is organized like a research firm: a council that reviews, a daily cycle that schedules, an IC that deliberates before a decision. One model answering alone is a demo; an organization reviewing evidence from independent angles is the thing that keeps you honest.

- **Three-perspective council**: an empiricist (only evidence-anchored history), a recordist (only unvalidated leads), and a challenger (adversarial) each recall the past separately under strict recall-class isolation, before a chair writes the next revision. The challenger exists to kill your favorite hypothesis, not to agree with it.
- **5-phase daily cycle**: pre-market, risk review, IC decision, execution, post-market; the IC decision step can be driven by a persona committee with explicit deliberation
- **Agent runtime**: a ReAct loop over 30+ tools (market, strategy, paper, grid, risk, sandbox), an MCP client, context budgeting and compaction, and five memory kinds (decision outcomes, preferences, research memory, strategy failures, trading memory)
- **Research loops and personas**: specialized multi-agent loops (factor, model, quant, report) and a Renaissance-style persona library spanning quant researcher to portfolio manager
- **Local LLMs welcome**: an adapter layer for any OpenAI-compatible endpoint plus Ollama, with a priority-chain router and health probes

### Statistics that fight overfitting

A backtest is easy to lie to. Test twenty parameter sets and one will look great by luck alone; most open-source backtesters hand you the Sharpe and let you find that out yourself. This suite ships the machinery that tells you the number is probably luck:

- **Deflated Sharpe ratio** and probabilistic Sharpe ratio, with expected-max-Sharpe and minimum track record length
- **Probability of backtest overfitting** via combinatorially symmetric cross-validation (CSCV)
- **Purged K-fold cross-validation** for overlapping samples
- **Monte Carlo**: bootstrap return distributions, parameter sensitivity sweeps, equity-curve distributions
- **Walk-forward validation**: 5 folds, 70/30 train split, 20 Optuna trials per fold, IS/OOS ratio reported
- The LLM tuner exposes 8 optimization objectives, an out-of-sample 70/30 hard gate that kicks entries out of the ranking, multi-seed variance, and a `--min-trades` filter

Research scripts follow the same discipline: pre-registered hypotheses, bootstrap confidence intervals (2,000 resamples) before a GO/NO-GO verdict, and a bit-for-bit parity harness that verified the grid engine port against the original research engine on a frozen fixture.

### Strategy lab

Templates are commodity; the point here is that a strategy is an artifact with a lifecycle, not a notebook cell: defined in one YAML, versioned, backtestable, and promotable from backtest to paper to live without being rewritten.

| | |
|---|---|
| **49 built-in templates** | trend, momentum, mean reversion, breakout, grid, DCA, calendar effect, volatility-gated, cost-gated; each parameterized and backtest-ready |
| **26 standalone YAML setups** | including ICT structures (fair value gaps, order blocks, liquidity sweeps), funding-rate arbitrage, BTC/ETH pairs, multi-timeframe trend |
| **29 built-in indicators** | from SMA/RSI/MACD to Hurst exponent, half-life, and market-structure detection |
| **Composition layers** | multi-timeframe confluence, fusion ensembles with a recommender, market-making inventory model, a restricted-execution sandbox for untrusted strategy code |
| **One file, three runtimes** | the same YAML drives backtest, paper trading, and live |

### Backtest engine with honest costs

A backtest that models costs as zero is a machine for generating false confidence. This engine charges you for reality: event-driven execution with a slippage family that models it instead of assuming it away (fixed, bid-ask spread, composite, **volume-aware**, and a realistic model combining them), with historical funding rates injected bar by bar for perpetual positions so funding actually hits the PnL. Multi-asset portfolio allocation and a paper benchmark are built in.

### Factor and alpha mining

A formula you cannot reproduce is a rumor. The mining stack treats a formula as a serialized artifact that carries its own vocabulary digest, so what you mined is verifiable against what it was mined under:

- **Formula VM**: a 133-token vocabulary (110 features, 23 operators) on a stack-machine executor with a deterministic vocab digest, so a serialized formula artifact verifies against the vocab it was mined under
- **RL mining**: a PyTorch formula policy network, including a REINFORCE variant whose fitness is the backtest Sharpe itself
- **Seeded search**: Alpha101 formulas translated into vocab tokens as warm-start populations, plus genetic search and island-model engines in the factor-research mining line
- **Factor hygiene**: winsorize/standardize/neutralize pipelines, leak checks, look-ahead alignment, cost models, weight-drift backtests
- **Serenity scoring**: an 8-factor, 8-penalty crypto risk model with ablation support
- **EventGuard**: a risk-brake layer that de-risks strategies around macro events, fed by four event sources (macro calendar, credit, Telegram signals, VIX)

### Markets and data

Research is only as good as what it can see; a market the engine cannot read is a blind spot with a price attached. The breadth here serves the research question, not a checklist:

- **7 exchange and broker integrations**: Binance (spot + futures with L2 order-book management), OKX, Alpaca (US stocks), Deribit (options), Polymarket (prediction markets), QMT (China brokerage), Solana (Jupiter aggregator); plus 3 DEX perpetual venues
- **4 asset classes beyond crypto**: US stocks, A-shares, Taiwan stocks, and China futures/FX
- **Order flow**: 13 aggregators (CVD, footprint, heatmap, VPVR, large-order and liquidation maps, merged book) over a tick store with replay
- **Derivatives data**: funding rates, open interest, options chains, liquidation heatmaps, on-chain fetchers
- **Ingest lines**: news (RSS collection, LLM tagging, volume-anomaly confirmation against futures 1m bars), Telegram signal extraction, fund data (convertible bonds, LOF/ETF premiums, REITs), and macro data from 6 providers (FRED, BIS, BLS, two fear-and-greed indices, CoinGecko)
- **A-share research**: 16 factor functions with IC/IR evaluation, point-in-time earnings (public-date aware, no look-ahead), limit-up sentiment breadth, 7 selection strategy templates (turtle, RPS breakout, high-tight-flag, and others), and a parameter-sensitivity lab

### Risk and execution safety

The live path is where unvalidated assumptions become losses, so nothing reaches it on faith:

- **30+ risk rules** in the engine: position and exposure limits, daily loss fuses, drawdown circuit breaker, correlation and crowding checks, liquidity filters, **honeypot detection**, Kelly and volatility-scaled position sizing
- **VaR suite**: historical, parametric, Monte Carlo, and CVaR
- **Execution layer**: algorithmic orders (VWAP, POV, adaptive), brackets, slicers, a shadow execution engine, an append-only order journal, startup reconciliation, disconnect triage, and a kill switch
- **Validated, not assumed**: a 24-hour shadow-run drill with 7 acceptance criteria (journal integrity, shadow-vs-paper equity deviation under 5%, kill-switch evidence, restart recovery) gates the live path

## What works today

An honest map, because a research tool you cannot audit is worthless.

**Runs end to end today:** the loop of open a question → run experiments → write a conclusion that cites its evidence → store that conclusion where a later question can find it. Strategy templates, backtests with realistic costs, the anti-overfit statistics, the tuner with OOS gates, factor and alpha mining, paper trading, risk rules, the grid engine, and the data sync pipelines are all real code in daily use by the author.

**Deliberately early:** two pieces. The decision-to-outcome loop has only a handful of recorded decisions so far, and it is not yet visible everywhere in the UI. Automatic recall of past conclusions is wired on some paths but not all. Both are being closed; neither is hidden.

## Define and run a strategy

A strategy is a YAML file. The same file drives backtests, paper trading, and live runs.

```yaml
# demo/strategies/rsi_reversal.yaml
id: template-rsi-reversal
name: RSI Reversal
type: rsi_reversal
symbols: [BTC/USDT]
interval: 1h
parameters:
  rsi_period: 14
  oversold: 30
  overbought: 70
entry_rules:
  long:
    - indicator: rsi
      params: { period: 14 }
      operator: less_than
      comparator: 30.0
exit_rules:
  close_long:
    - indicator: rsi
      params: { period: 14 }
      operator: greater_than
      comparator: 70.0
risk_params:
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
  max_open_positions: 1
```

Run it from the terminal:

```bash
call activate.bat
python scripts/generate_demo_data.py     # writes demo/data/*.parquet (uses live 1h klines, falls back to synthetic)
selenespace backtest run demo/strategies/rsi_reversal.yaml --data demo/data/btc_usdt_1h_90d.parquet
```

```
Backtest complete
  Total return: 12.09%
  Sharpe: 0.6234
  Max DD: -3.92%
  Trades executed: 13
```

> `generate_demo_data.py` needs no API key: it pulls public Binance 1h klines
> and writes deterministic synthetic data if the network is unavailable. The
> generated files land under the git-ignored `demo/data/`.

Or from Python, against live market data:

```python
from selene_space_strategy.compiler import compile
from selene_space_strategy.models import load_strategy
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_market.sources.binance_source import BinanceSource

config = load_strategy("demo/strategies/rsi_reversal.yaml")
runtime = StrategyRuntime(compile(config))

source = BinanceSource(market_type="spot")
await source.start()
klines = await source.fetch_klines_rest(symbol="BTC/USDT", interval="1h", limit=500)
await source.stop()

engine = BacktestEngine(config=BacktestConfig(
    initial_cash=10_000.0, strategy_id=config.id,
    symbol=config.symbols[0], interval=config.interval,
))
result = engine.run(strategy=runtime, data=klines)
print(result.metrics.sharpe_ratio, result.trades_count)
```

A full runnable version is in [`examples/backtest_demo.py`](examples/backtest_demo.py).

## Terminal and CLI

After `call activate.bat`, the `selenespace` command is available. The CLI and the desktop app run the same engine libraries in-process; there is no separate implementation behind either face.

```bash
selenespace setup                       # six-stage first-run wizard; exchange keys go to the OS keyring
selenespace status                      # one-glance environment summary
selenespace doctor                      # 19-check diagnostics (--repair to fix)
selenespace market ticker BTC/USDT      # read the market
selenespace backtest run <strategy.yaml> --data <parquet>
selenespace paper create --help         # paper-trading ledger
selenespace ashare report 600519        # A-share factor report: IC, quantiles, orthogonalization
selenespace ask "BTC 4h volatility over the last 30 days?"
```

## API and web app

The backend exposes **523 endpoints** across 79 routers (plus 8 WebSocket streams for markets, paper trading, the agent conversation, and research), and the Next.js app has **142 pages** across four scenes (watch, research, trade, system).

| API area | Endpoints | What lives there |
|---|---|---|
| Research graph + missions | 42 | questions, revisions, evidence, recall, integrity audit, attribution |
| Alpha and factor mining | 37 | formula lifecycle, validation, artifacts |
| Strategy factory | 25 | templates, versions, deploy, export |
| Prediction markets | 22 | Polymarket scanning and alerts |
| Live trading | 21 | orders, positions, kill switch, latency stats |
| Paper trading | 16 | accounts, orders, fills, PnL |
| Optimization | 16 | auto and manual optimization runs |
| Grid engine | 12 | grid strategies, sentinel, state |
| AI agent | 11 | chat, team, streaming analysis, reasoning traces |
| Everything else | ~310 | markets, derivatives, news, macro, alerts, memory, shadow mode, and more |

## What's inside

A local-first monorepo. Python packages under `packages/`, the FastAPI service under `services/local-api/`, and the Next.js app under `apps/desktop/`.

| Area | Where | What's there |
|---|---|---|
| Strategy engine | `packages/strategy-engine/` | 49 templates, 26 YAML setups, 29 indicators, compiler, optimizer, MTF confluence, fusion ensembles, restricted sandbox |
| Backtest engine | `packages/backtest-engine/` | event-driven runs, 6 slippage models, funding injection, multi-asset portfolios, DSR / PBO / purged K-fold, Monte Carlo |
| Exchange SDK | `packages/exchange-sdk/` | 7 exchanges + 3 DEX venues, algo orders, shadow engine, order journal, startup reconciliation |
| Market data | `packages/market-data/` | 6 sources, tick store with replay, derivatives fetchers, 13 order-flow aggregators, regime state engine |
| Factor research | `packages/factor-research/` | preprocessing pipelines, genetic search, leak checks, cost models |
| Alpha core | `packages/alpha-core/` | formula VM (133 tokens), RL mining, Serenity scoring, EventGuard, portfolio construction |
| Risk engine | `packages/risk-engine/` | 30+ rules, honeypot detection, VaR suite, kill switch |
| Paper engine | `packages/paper-engine/` | accounts, orders, fills, PnL, audit ledger, tiered trailing stops |
| Grid engine | `packages/grid-engine/` | neutral two-leg grids, parity-verified port, gate calibration |
| Agent runtime | `packages/agent-runtime/` | ReAct loop, 30+ tools, MCP client, skills, 5 memory kinds |
| Multi-agent layer | `packages/agentstudio/`, `packages/renintel/`, `packages/daily-cycle/` | research loops, persona library with IC deliberation, 5-phase daily cycle |
| Ingest | `packages/news-ingest/`, `packages/tg-ingest/`, `packages/fund-ingest/`, `packages/macrodata/` | news pipeline, Telegram signals, fund data, 6 macro providers |
| Multi-market | `packages/ashare/`, `packages/ashare-select/`, `packages/stock/`, `packages/commodities-fx/` | A-share factors and selection, US stock scoring, Taiwan connector, futures and FX |
| LLM adapter | `packages/llm-adapter/` | OpenAI-compatible + Ollama, priority-chain router |
| Web app | `apps/desktop/` | Next.js, 142 pages, 4 scenes |
| Backend | `services/local-api/` | FastAPI, 523 endpoints, 8 WebSocket streams |
| CLI | `packages/cli/` | 8 command groups, 19-check doctor |
| Root launchers | `activate.bat`, `dev.bat`, `dev-reload.bat`, `prod.bat`, `run.bat` | Windows entry points: `activate.bat` sets `PYTHONPATH` for the current checkout (run it before any bare `python`), `dev.bat` starts everything, `run.bat` runs a script already activated. See the Quick start above. |

## Documentation

- [`docs/PRODUCT.md`](docs/PRODUCT.md): what SeleneQuant is and is not
- [`docs/DESIGN.md`](docs/DESIGN.md): the design canon
- [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md): how to report issues and run a local copy
- [`ATTRIBUTIONS.md`](ATTRIBUTIONS.md): vendored and referenced projects

## License

AGPL-3.0-only. See [`LICENSE`](LICENSE).

## No-copy declaration

`packages/market-analysis`, a two-stage market-analysis workflow, is an independent reimplementation in this project:

- the workflow concept is derived independently from this project's own `packages/decision-workflow` and from public literature (Al Brooks price-action publications and others);
- all code and prompt text is written independently;
- it shares no copied material with independent projects in the same field, and no prompt text from them was read;
- it is not a derivative work of any external work.

See [`ATTRIBUTIONS.md`](ATTRIBUTIONS.md) and [`packages/factor-research/THIRD_PARTY_NOTICES.md`](packages/factor-research/THIRD_PARTY_NOTICES.md).
