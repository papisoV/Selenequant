@echo off
:: SeleneQuant Development Environment Activator
::
:: Purpose: run in any replica dir, sets PYTHONPATH to current replica packages
:: Multi-replica scenario: activate.bat sets up the local replica environment
::
:: How it works:
::   - %~dp0 = directory of activate.bat (current replica root)
::   - PYTHONPATH uses paths relative to %~dp0, zero-config after copy
::   - no global site-packages pollution, no editable install needed

chcp 65001 >nul

set "SELENE_ROOT=%~dp0"
set "SELENE_ROOT=%SELENE_ROOT:~0,-1%"

set "PYTHONPATH=%SELENE_ROOT%;%SELENE_ROOT%\packages\shared-types;%SELENE_ROOT%\packages\core;%SELENE_ROOT%\packages\cli;%SELENE_ROOT%\packages\strategy-engine;%SELENE_ROOT%\packages\backtest-engine;%SELENE_ROOT%\packages\market-data;%SELENE_ROOT%\packages\agent-runtime;%SELENE_ROOT%\packages\exchange-sdk;%SELENE_ROOT%\packages\llm-adapter;%SELENE_ROOT%\packages\paper-engine;%SELENE_ROOT%\packages\risk-engine;%SELENE_ROOT%\packages\security-gateway;%SELENE_ROOT%\packages\storage;%SELENE_ROOT%\packages\compat-layer;%SELENE_ROOT%\packages\alpha-core;%SELENE_ROOT%\packages\stock;%SELENE_ROOT%\packages\ashare;%SELENE_ROOT%\packages\ashare-select;%SELENE_ROOT%\packages\pulse;%SELENE_ROOT%\packages\tg-ingest;%SELENE_ROOT%\packages\macrodata;%SELENE_ROOT%\packages\decision-workflow;%SELENE_ROOT%\packages\optimize;%SELENE_ROOT%\packages\agentstudio;%SELENE_ROOT%\packages\daily-cycle;%SELENE_ROOT%\packages\renintel;%SELENE_ROOT%\packages\market-analysis;%SELENE_ROOT%\packages\commodities-fx;%SELENE_ROOT%\packages\factor-research;%SELENE_ROOT%\packages\grid-engine;%SELENE_ROOT%\packages\news-ingest;%SELENE_ROOT%\packages\fund-ingest;%SELENE_ROOT%\services\local-api"

:: LITELLM: skip remote model cost map fetch (raw.githubusercontent.com unreachable
:: on CN network; used to block backend startup 30-40s). Local bundled map is used.
:: Mirrors the same var in services/local-api/.env.
set "LITELLM_LOCAL_MODEL_COST_MAP=True"

:: klines.db: the code path is unified via selene_space_core.kline_paths.default_klines_db_path()
:: (env takes priority; else derived from __file__ to repo-root data/klines.db, CWD-independent).
:: Default pins to THIS replica's data/klines.db (the synced real DB); but an explicit
:: SELENE_KLINES_DB is NOT overridden -- so multiple replicas can share one canonical
:: DB (set SELENE_KLINES_DB once at machine level). Matches dev.py's setdefault semantics.
if not defined SELENE_KLINES_DB set "SELENE_KLINES_DB=%SELENE_ROOT%\data\klines.db"

echo [SeleneQuant] PYTHONPATH set to current replica: %SELENE_ROOT%

:: selenespace CLI alias (replaces console_script removed when editable install was uninstalled)
doskey selenespace=python -m selene_space_cli.main $*
