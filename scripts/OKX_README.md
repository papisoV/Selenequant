# OKX Scripts Overlay (SeleneQuant source)

Provenance: `SeleneQuant-overlay` — copied verbatim from `<local-path-redacted>` on 2026-08-02.
Plan ref: `internal-plan` §5.4 (Workstream C).

## Scripts (7 files)

| Script | Purpose | Data deps | OKX API? |
|--------|---------|-----------|----------|
| `import_okx_klines.py` | Import OKX parquet K-lines into SQLite `klines.db`. Reads `data/OKX_K线数据.zip` (or extracted dir), maps 19 symbols × 4 intervals (D1/H1/M15/M5) to SeleneQuant `KlineEvent` schema, bulk-inserts via `KlineDB` (BATCH_SIZE=10000). Supports `--interval`, `--dry-run`. | `data/OKX_K线数据.zip` → `data/klines.db` | No (offline parquet ingestion) |
| `backtest_okx_all.py` | Systematic backtest runner. Evaluates DCA Grid, Keltner V2, MA Extended Runs, EMA Triple Cross, P-Signal across all symbols × intervals in `klines.db`. Optional walk-forward (3 folds, 60/40 IS/OOS). Outputs JSON. | reads `data/klines.db` → writes `data/okx_backtest_results/` | No |
| `okx_backtest_summary.py` | Aggregates `backtest_okx_all.py` JSON output into ranked leaderboard + cross-symbol summary (Sharpe stats, walk-forward OOS). | reads `data/okx_backtest_results/okx_backtest_*.json` | No |
| `run_v3_formula_okx.py` | Runs Optuna V3 best formula (`LOG_VOL NEG VOL_CLUSTER ADD`, OOS Sharpe 0.34 on BTC 4h) across all 19 OKX symbols on D1. | reads `data/klines.db` → writes `data/okx_backtest_results/v3_formula_d1.json` | No |
| `run_d1_formula_okx.py` | Runs D1-tuned Optuna V3 formulas (`LOG_VOL RET TS_MEAN ADD` OOS 0.63, `LOG_VOL ABS TS_MEAN RANK` OOS 0.61) across all 19 OKX symbols on D1. | reads `data/klines.db` → writes `data/okx_backtest_results/` | No |
| `tune_keltner_d1.py` | Per-symbol Optuna Bayesian tuning of Keltner V2 on D1 data. 50 trials default, 7-param space (ma/atr/mult/adx/stop/tp). | reads `data/klines.db` → writes `data/keltner_d1_tuning/` | No |
| `tune_keltner_wf.py` | Walk-forward (3-fold, OOS Sharpe optimized) grid-search Keltner V2 tuning on D1. More robust than single-shot Optuna. | reads `data/klines.db` → writes `data/keltner_d1_wf_tuning/` | No |

## Data directories (5 items, copied to `union/data/`)

| Path | Source | Purpose |
|------|--------|---------|
| `data/OKX_K线数据.zip` | SeleneQuant | Source parquet K-lines for 19 symbols × 4 intervals (input to `import_okx_klines.py`) |
| `data/okx_backtest_results/` | SeleneQuant | Output of `backtest_okx_all.py` / `run_v3_formula_okx.py` / `run_d1_formula_okx.py` |
| `data/keltner_d1_tuning/` | SeleneQuant | Output of `tune_keltner_d1.py` |
| `data/keltner_d1_wf_tuning/` | SeleneQuant | Output of `tune_keltner_wf.py` (note: `wf` suffix, plan v2 mentioned `keltner_d1_wf_tuning` — confirmed present in SeleneQuant) |
| `data/optuna_formula_v3_d1/` | SeleneQuant | D1-discovered Optuna V3 formulas (consumed by `run_d1_formula_okx.py` via `result.json`) |

## OKX API keys

**None required.** All 7 scripts operate on local SQLite (`data/klines.db`) populated offline from the parquet zip. No `os.environ` lookups for OKX credentials, no HTTP calls to OKX REST/WS endpoints. Verified via grep across `scripts/*.py` for `os.environ`, `getenv`, `OKX_API`, `API_KEY`, `passphrase`, `requests.get`, `http[s]?://`.

## Run order (typical pipeline)

1. **Ingest** (one-time): `python scripts/import_okx_klines.py` — populates `data/klines.db` from `data/OKX_K线数据.zip`.
2. **Backtest** (any order, independent):
   - `python scripts/backtest_okx_all.py` — multi-strategy sweep.
   - `python scripts/run_v3_formula_okx.py` — V3 formula on D1.
   - `python scripts/run_d1_formula_okx.py` — D1-tuned formulas on D1.
3. **Tune** (independent):
   - `python scripts/tune_keltner_d1.py` — Optuna Bayesian (single-shot).
   - `python scripts/tune_keltner_wf.py` — grid + walk-forward (more robust).
4. **Summarize**: `python scripts/okx_backtest_summary.py data/okx_backtest_results/okx_backtest_XXX.json` — leaderboard from step 2 output.

All scripts assume CWD = project root (`selene-merged-union/`) and use relative paths like `data/klines.db`. Activate venv first (`call activate.bat` per docstrings).
