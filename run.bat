@echo off
:: SeleneQuant Python Script Runner
::
:: Usage: run.bat <script.py> [args...]
:: Example: run.bat scripts/llm_param_tuner.py --strategies bb_v3
::
:: Auto-activates PYTHONPATH, then runs any python script; no need to call activate.bat first

chcp 65001 >nul

if "%~1"=="" (
    echo Usage: run.bat ^<script.py^> [args...]
    exit /b 1
)

call "%~dp0activate.bat"
python %*
