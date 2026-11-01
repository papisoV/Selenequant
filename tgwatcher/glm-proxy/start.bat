@echo off
title GLM Refusal Proxy

:: Kill existing node process on port 3827
echo Checking port 3827...
for /f %%p in ('powershell -Command "(Get-NetTCPConnection -LocalPort 3827 -ErrorAction SilentlyContinue | Where-Object {$_.State -eq 'Listen'}).OwningProcess"') do (
    echo Killing old proxy PID %%p...
    taskkill /PID %%p /F >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

echo Starting GLM Refusal Proxy...
node "<local-path-redacted>"
if errorlevel 1 (
    echo.
    echo Proxy exited with error. Press any key to close...
    pause >nul
)
