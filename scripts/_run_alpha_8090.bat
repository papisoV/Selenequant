@echo off
call "%~dp0..\activate.bat"
cd /d "%~dp0..\services\local-api"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --log-level warning
