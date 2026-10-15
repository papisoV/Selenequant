@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ========================================
echo   TGWatcher - Telegram Group Crawler
echo ========================================
echo.
echo   http://localhost:5800
echo.
python -m tgwatcher.web.app --port 5800
pause
