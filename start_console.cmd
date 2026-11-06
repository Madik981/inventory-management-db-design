@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 inventory_console.py %*
) else (
    python -X utf8 inventory_console.py %*
)
if errorlevel 1 pause
