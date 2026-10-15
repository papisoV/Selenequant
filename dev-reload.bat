@echo off
chcp 65001 >nul
title SeleneQuant Development (Reload)

echo ========================================
echo   SeleneQuant Community - Dev Launcher (Reload)
echo ========================================
echo.

:: Proxy: opt-in only. Was hardcoded to a Clash default (127.0.0.1:7897),
:: broke every exchange call without it; set SELENE_PROXY_URL if you need one.
if defined SELENE_PROXY_URL (
  echo [Proxy] Using SELENE_PROXY_URL=%SELENE_PROXY_URL%
) else (
  echo [Proxy] none set ^(set SELENE_PROXY_URL=http://host:port if you need one^)
)

:: Unique window tag: concurrent checkouts (or two runs) must not taskkill each other's
:: server windows; defaults to this folder's name; override with SELENE_WS_ID.
for %%I in ("%~dp0.") do set "_WS_ID=%%~nxI"
if not defined _WS_ID set "_WS_ID=default"
if defined SELENE_WS_ID set "_WS_ID=%SELENE_WS_ID%"
echo [Window] id=%_WS_ID% ^(override with SELENE_WS_ID^)

echo.
echo [Backend] Starting on port 8088 (--reload)...
start "SeleneQuant Backend %_WS_ID%" cmd /k "cd /d %~dp0 && call activate.bat && cd /d %~dp0services\local-api && uvicorn app.main:app --reload --host 127.0.0.1 --port 8088"

:: Wait for backend ready: poll /api/v1/health until 200, max 60s.
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
echo [Frontend] Starting on port 3000...
start "SeleneQuant Frontend %_WS_ID%" cmd /k "cd /d %~dp0apps\desktop && npm run dev"

echo.
echo ========================================
echo   Services started!
echo   - Backend:  http://127.0.0.1:8088/docs
echo   - Frontend: http://localhost:3000
echo ========================================
echo.
echo Press any key to stop all services...
pause >nul

:: Kill our own service window trees only (tagged with %_WS_ID%); other checkouts untouched
taskkill /FI "WINDOWTITLE eq SeleneQuant Backend %_WS_ID%" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq SeleneQuant Frontend %_WS_ID%" /T /F >nul 2>&1

echo Services stopped.
