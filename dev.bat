@echo off
chcp 65001 >nul
title SeleneQuant Development

echo ========================================
echo   SeleneQuant Community - Dev Launcher
echo ========================================
echo.

:: Proxy: opt-in only. Was hardcoded to a Clash default (127.0.0.1:7897),
:: which broke every exchange call on machines without that proxy running.
:: Set SELENE_PROXY_URL in your environment if you need one.
if defined SELENE_PROXY_URL (
  echo [Proxy] Using SELENE_PROXY_URL=%SELENE_PROXY_URL%
) else (
  echo [Proxy] none set ^(export SELENE_PROXY_URL=http://host:port if you need one^)
)

:: Unique window tag so concurrent checkouts (or two runs in one checkout) never
:: taskkill each other's server windows. Defaults to the checkout folder name
:: (e.g. "SeleneQuant"; a worktree shows its own name); override with SELENE_WS_ID.
for %%I in ("%~dp0.") do set "_WS_ID=%%~nxI"
if not defined _WS_ID set "_WS_ID=default"
if defined SELENE_WS_ID set "_WS_ID=%SELENE_WS_ID%"
echo [Window] id=%_WS_ID% ^(override with SELENE_WS_ID^)

:: Clean residual port holders so a re-run is not blocked by stale processes
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8088" ^| findstr "LISTENING"') do taskkill /PID %%p /T /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3000" ^| findstr "LISTENING"') do taskkill /PID %%p /T /F >nul 2>&1

echo.
echo [Backend] Starting on port 8088...
:: No --reload: this repo has 50k+ files, reload scans everything per request -> 6-10s lag.
:: Use dev-reload.bat when you need hot reload.
start "SeleneQuant Backend %_WS_ID%" cmd /k "cd /d %~dp0 && call activate.bat && cd /d %~dp0services\local-api && uvicorn app.main:app --host 127.0.0.1 --port 8088"

:: Start frontend in parallel (does not wait for backend health).
:: Backend startup takes 25s+ (208 strategies + network subscribes); if the frontend
:: waited on health it would never appear when the backend is slow on CN networks.
echo.
echo [Frontend] Starting on port 3000...
start "SeleneQuant Frontend %_WS_ID%" cmd /k "cd /d %~dp0apps\desktop && npm run dev"

:: Wait for backend ready: poll /api/v1/health until 200, max 60s.
echo.
echo [Backend] Waiting for startup (polling /api/v1/health)...
set /a _attempts=0
:wait_health
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:8088/api/v1/health 2>nul | findstr "200" >nul
if not errorlevel 1 goto health_ok
set /a _attempts+=1
if %_attempts% geq 60 (
  echo [WARN] Backend not ready in 60s, still starting in the background...
  goto health_timeout
)
timeout /t 1 /nobreak >nul
goto wait_health
:health_ok
echo [Backend] Ready.
:health_timeout

echo.
echo ========================================
echo   Services started!
echo   - Backend:  http://127.0.0.1:8088/api/v1/docs
echo   - Frontend: http://localhost:3000
echo ========================================
echo.
echo Press any key to stop all services...
pause >nul

:: Kill our own service window trees only (tagged with %_WS_ID%); other checkouts untouched
taskkill /FI "WINDOWTITLE eq SeleneQuant Backend %_WS_ID%" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq SeleneQuant Frontend %_WS_ID%" /T /F >nul 2>&1

echo Services stopped.
