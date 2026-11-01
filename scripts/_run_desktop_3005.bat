@echo off
:: Companion launcher for `scripts/_probe_alpha_resources_ui.py` / `_e2e_*.py`.
:: Points the desktop dev server at the probe backend (8090) and pins port 3005,
:: which is what the probe scripts hardcode. Pair with `_run_alpha_8090.bat`.
set "LOCAL_API_BASE=http://127.0.0.1:8090"
cd /d "%~dp0..\apps\desktop"
npx next dev -p 3005
