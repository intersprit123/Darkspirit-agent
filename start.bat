@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Darkspirit Agent

echo ========================================
echo       Darkspirit Agent - Safe Launcher
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Project virtual environment was not found.
  echo Run: uv sync
  pause
  exit /b 1
)

if /I "%~1"=="--browser-bridge" goto browser_bridge
if /I "%~1"=="--local-ollama" goto local_ollama

echo Choose a mode:
echo   1. Check setup only
echo   2. Start browser bridge (observe-only scaffold)
echo   3. Start local Ollama agent
echo   4. Exit
echo.
choice /c 1234 /n /m "Select 1-4: "
if errorlevel 4 exit /b 0
if errorlevel 3 goto local_ollama
if errorlevel 2 goto browser_bridge

echo [OK] Virtual environment exists.
".venv\Scripts\python.exe" --version
echo [INFO] GUI and computer-control agent were not started.
pause
exit /b 0

:browser_bridge
echo [INFO] Starting localhost browser bridge in OBSERVE-ONLY mode.
echo [INFO] This scaffold does not execute computer actions.
".venv\Scripts\python.exe" -m darkspirit_bridge
set "RC=%ERRORLEVEL%"
echo.
echo Bridge exited with code %RC%.
pause
exit /b %RC%

:local_ollama
echo [WARNING] This mode can enable Windows computer control through the existing agent.
echo Only continue if you understand that tasks may click, type, and run commands.
choice /c YN /n /m "Continue? [Y/N] "
if errorlevel 2 exit /b 0
".venv\Scripts\python.exe" main.py --provider ollama --model qwen3:4b --max-steps 10
set "RC=%ERRORLEVEL%"
echo.
echo Agent exited with code %RC%.
pause
exit /b %RC%
