<div align="center">
  <img src="docs/assets/selenequant-banner.webp" alt="SeleneQuant — Continuous Adaptive Quant Research" width="100%">

  <h3>A local-first AI quant research organization. Questions are persistent, conclusions are versioned, and the past changes the next call.</h3>

  <p>Most research tools store answers. SeleneQuant keeps the <em>question</em>, runs real experiments against it, versions every conclusion, and recalls what was already established when the next question arrives — on top of a full quant stack, with the statistics to know when a result is real.</p>

  <p>
    <a href="#quick-start"><img src="https://img.shields.io/badge/Quick_start-1f6feb?style=for-the-badge" alt="Quick start"></a>
    <a href="#the-platform-underneath"><img src="https://img.shields.io/badge/Platform-0b1219?style=for-the-badge" alt="Platform"></a>
    <a href="#what-works-today"><img src="https://img.shields.io/badge/Status-early_stage-F7A07D?style=for-the-badge" alt="Status: early stage"></a>
  </p>

  <p>
    <a href="https://github.com/papisoV/selenequant/actions/workflows/ci.yml"><img src="https://github.com/papisoV/selenequant/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg" alt="License: AGPL-3.0-only"></a>
    <img src="https://img.shields.io/badge/Python-3.11%2B-blue.svg" alt="Python 3.11+">
    <img src="https://img.shields.io/badge/Node-20%2B-3c873a.svg" alt="Node 20+">
  </p>

  <p><a href="README.md">English</a> · <a href="README.zh-CN.md">中文</a></p>

  <p>
    <a href="https://x.com/Kairos_Selene"><img src="https://img.shields.io/badge/X-%40Kairos__Selene-000000?logo=x&logoColor=white" alt="X: @Kairos_Selene"></a>
    <a href="https://space.bilibili.com/110740322"><img src="https://img.shields.io/badge/Bilibili-Selene-FB7299?logo=bilibili&logoColor=white" alt="Bilibili"></a>
  </p>

  <img src="docs/assets/research-today.png" alt="The SeleneQuant daily desk (/research/today), rendered live: the five-phase research cycle with its running counts (questions 36, missions 36, investigations 33, experiments 2, decisions 1, outcomes 0, memory 21); a LAST HANDOFF panel; a NEEDS YOU panel with one item awaiting a human decision, an ETH/USDT PA heartbeat diagnosis; and a SELENE IS WORKING panel listing 35 items in flight." width="100%">

  <sub>A running instance, not a mockup: the five-phase cycle with its live counts, a handoff, one decision waiting on a human, and 35 items in flight.</sub>
</div>

---

> **Repository posture.** This public repo tracks an internally developed project as a series of curated, verified snapshots rather than its full history. A pull request therefore has no branch here to merge into — a mechanical limit, not a closed door. Issues are welcome and read, and a proposed change can be absorbed into a future snapshot with credit (see [CONTRIBUTING](.github/CONTRIBUTING.md)).

## Quick start

**Python 3.11+** and **Node 20+**. Windows, macOS and Linux.

```bash
git clone https://github.com/papisoV/selenequant
cd selenequant
pip install -r requirements.txt

# the web app needs its own dependencies; `ci` installs exactly what package-lock pins
cd apps/desktop && npm ci && cd ../..

# starts the backend and the web app, waits for the health check
python dev.py --prod
```

It prints two URLs: the API and its docs at `http://127.0.0.1:8088/api/v1/docs`, the web app at `http://localhost:3000`. The first start creates an empty local database and begins syncing market data in the background, so the app opens as a shell plus whatever has synced so far — not a broken install.

**Use `--prod` to run the app; drop it to edit the frontend.** Without it, Next.js compiles each page the first time you open it, which across ~145 routes is what makes a fresh session feel slow. `--prod` builds once and serves precompiled pages.

Open the app through **`localhost`**, not `127.0.0.1` — Next.js dev origin protection rejects the bare IP and the page hangs on "Connecting to runtime". On Windows, `dev.bat` does the same thing; set `SELENE_PROXY_URL` only if you route traffic through a proxy.

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

> **Do not run `pip install -e .`.** It writes an absolute path into your global `site-packages` that points at whichever copy you installed from, so in a multi-copy setup it silently loads the wrong source. Use `pip install -r requirements.txt` and let `activate.bat` set `PYTHONPATH`.

## What it does

A question is a living object. You open it; Selene runs research against it — experiments, evidence, conclusions — and each conclusion is a numbered **revision** that can cite the one before it and the evidence behind it. Nothing is overwritten, and nothing pretends to know more than it did at the time.

A real research line from the author's library:

> **Q: Why did BTC bounce recently? The rate hike on Sep 17 should have been bearish.**
>
> **v1** · cites nothing · *The hike was already priced in; the landing cleared uncertainty.* BTC had already fallen to a monthly low near $75.6K on Sep 15-16, with a single-day ETF outflow of $450M (the largest since June); the market had priced a 25bp hike at ~93% before the decision. The hike landing triggered a bounce to $77K.
>
> **v2** · **cites v1** · *Building on v1, the bounce's quality is doubtful.* All three demand legs are weak: Coinbase premium at a monthly low, corporate treasuries buying ~5,900 BTC over three months, ETH/XRP ETFs flipping to net outflow. If "bounce after hike = clearing" holds, spot inflows should confirm it; if BTC falls back below $75K without premium recovery, v1's clearing thesis is wrong.

One question, four experiments, one of which overturned an earlier verdict:

| # | Experiment | Verdict | Key number |
|---|---|---|---|
| 1 | Funding factor family, 4-bar IC sweep | NO-GO | \|IC\| ≤ 0.02 across the family |
| 2 | Funding multi-horizon IC sweep | **reversal** | 7d clears 0.10 for all symbols |
| 3 | Funding entry-veto, gate 1 | veto fails | 3243 tagged trades, win rate flat across all 7 buckets |
| 4 | Funding carry + reversal sim, gate 2 | INCONCLUSIVE | ETH+SOL pooled n=203, CI95 crosses zero |

The single conclusion that follows cites all four experiments by id.

## The loop

A question persists; research produces evidence and numbered revisions; each revision can cite the ones before it and their evidence; a stored conclusion is recalled automatically, so the next call starts from what was already established rather than from zero.

<img src="docs/assets/research-loop.svg" alt="The SeleneQuant research loop: a persistent Question feeds Experiments that produce evidence and verdicts; each Experiment feeds a numbered Revision; revisions are stored in Memory with what was concluded and why; Memory is recalled automatically into the Next call, which cites and revises the conclusion." width="100%">

<img src="docs/assets/research-memory.png" alt="The SeleneQuant memory layer (/research/mem), rendered live: 4 stored knowledge entries and 17 unvalidated leads. Every entry is a hypothesis or a conclusion, stamped with when it was fixed and how many times it changed, and chained to the evidence or question id it came from — including a funding-crowding hypothesis marked refuted, and a four-verdict conclusion recording |IC| <= 0.02." width="100%">

## The platform underneath

The loop is the product. Underneath it sits a platform built through years of daily research use, and most of it is real, runnable code rather than roadmap.

<img src="docs/assets/quant-stack.svg" alt="The SeleneQuant platform: Market data (klines and ticks from 6 sources, derivatives, order flow from 13 aggregators, news / Telegram / macro / funds) feeds Engines (strategy engine with 49 templates, backtest with realistic costs, anti-overfit statistics such as DSR, PBO and purged K-fold), which feed an AI research organization (a three-perspective council, versioned conclusions, and a five-phase daily cycle). The organization is delivered as a web app with 145 pages, a CLI with 8 command groups, and a FastAPI service with 522 API paths." width="100%">

**A research organization, not a chat window.** Three recall perspectives — an empiricist (evidence-anchored history only), a recordist (unvalidated leads only), and an adversarial challenger — read the past separately under strict class isolation before a chair writes the next revision. A five-phase daily cycle (pre-market, risk review, IC decision, execution, post-market) schedules the work, and the IC step can be driven by a persona committee with explicit deliberation. Underneath sits a ReAct agent runtime with 30+ tools, an MCP client, context budgeting, and five memory kinds.

**Statistics that fight overfitting.** A backtest is easy to lie to: test twenty parameter sets and one wins by luck. This suite ships the machinery that says so — deflated Sharpe with expected-max and minimum track record length, probability of backtest overfitting via CSCV, purged K-fold for overlapping samples, and Monte Carlo resampling (trade-order permutation, bootstrap Sharpe intervals, equity percentile bands) on every backtest by default. Walk-forward validation runs 5 folds at 70/30 with 20 Optuna trials each, and the tuner exposes 8 objectives with an out-of-sample hard gate.

**A strategy is an artifact, not a notebook cell.** 49 built-in templates and 28 standalone YAML setups — including ICT structures, funding-rate arbitrage and pairs — defined in one file that drives backtest, paper and live without rewriting. 29 built-in indicators, multi-timeframe confluence, fusion ensembles, and a restricted sandbox for untrusted strategy code.

**Costs are charged, not assumed.** Event-driven execution with six slippage models (fixed, spread, composite, volume-aware, and combinations), historical funding rates injected bar by bar for perpetuals, and multi-asset portfolio allocation.

**Formulas you can reproduce.** A formula VM over a 133-token vocabulary (110 features, 23 operators) with a deterministic digest, so a serialized artifact verifies against the vocab it was mined under — plus RL mining, Alpha101 warm starts, genetic and island-model search, leak checks, and EventGuard, a risk brake fed by macro calendar, credit, Telegram signals and VIX.

**Breadth that serves the question.** 7 exchange and broker integrations plus 3 DEX venues, 4 asset classes beyond crypto, 13 order-flow aggregators over a replayable tick store, and A-share research with point-in-time earnings that cannot look ahead.

**Nothing reaches the live path on faith.** 30+ risk rules including honeypot detection, a VaR/CVaR suite, an execution layer with a shadow engine and append-only order journal, and a 24-hour shadow-run drill whose 7 acceptance criteria must pass before the live path opens.

## What works today

An honest map, because a research tool you cannot audit is worthless.

**Runs end to end today:** open a question → run experiments → write a conclusion that cites its evidence → store it where a later question can find it. Strategy templates, backtests with realistic costs, the anti-overfit statistics, the tuner with OOS gates, factor and alpha mining, paper trading, risk rules, the grid engine, and data sync are all real code in daily use.

**Deliberately early:** two pieces. The decision-to-outcome loop has only a handful of recorded decisions and is not yet visible everywhere in the UI; automatic recall of past conclusions is wired on some paths but not all. Both are being closed, neither is hidden.

## Define and run a strategy

A strategy is a YAML file, and the same file drives backtests, paper trading and live runs.

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

```bash
call activate.bat
python scripts/generate_demo_data.py     # writes demo/data/*.parquet (live 1h klines, synthetic fallback)
selenespace backtest run demo/strategies/rsi_reversal.yaml --data demo/data/btc_usdt_1h_90d.parquet
```

`generate_demo_data.py` needs no API key: it pulls public Binance 1h klines and writes deterministic synthetic data if the network is unavailable. To drive the engine from Python instead, see [`examples/backtest_demo.py`](examples/backtest_demo.py).

## Terminal

After `call activate.bat`, the `selenespace` command is available. The CLI and the desktop app run the same engine libraries in-process; there is no separate implementation behind either face.

```bash
selenespace setup                       # six-stage first-run wizard; exchange keys go to the OS keyring
selenespace doctor                      # 19-check diagnostics (--repair to fix)
selenespace market ticker BTC/USDT      # read the market
selenespace backtest run <strategy.yaml> --data <parquet>
selenespace paper create --help         # paper-trading ledger
selenespace ashare report 600519        # A-share factor report: IC, quantiles, orthogonalization
selenespace ask "BTC 4h volatility over the last 30 days?"
```

## Repository layout

A local-first monorepo: engine libraries under `packages/`, the FastAPI service under `services/local-api/` (**522 API paths**, 568 operations, 88 routers, 8 WebSocket streams), and the Next.js app under `apps/desktop/` (**145 pages** across watch, research, trade and system). Root launchers — `activate.bat`, `dev.bat`, `prod.bat`, `run.bat` — are the Windows entry points.

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
