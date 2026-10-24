@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Darkspirit Agent

if /I "%~1"=="--install" goto install
if /I "%~1"=="--browser-bridge" goto browser_bridge
if /I "%~1"=="--local-ollama" goto local_ollama
if /I "%~1"=="--companion" goto companion

:menu
cls
echo ========================================
echo        DARKSPIRIT AGENT
echo ========================================
echo.
echo  1. Install / update all project dependencies
echo  2. Start Browser AI bridge (ChatGPT / Gemini)
echo  3. Start local Ollama agent (computer control)
echo  4. Start background companion (no UI window)
echo  5. Exit
echo.
choice /c 12345 /n /m "Select 1-5: "
if errorlevel 5 exit /b 0
if errorlevel 4 goto companion
if errorlevel 3 goto local_ollama
if errorlevel 2 goto browser_bridge
if errorlevel 1 goto install

:install
call "%~dp0install.bat"
if errorlevel 1 (
  echo.
  echo Installation failed. Read the error above.
  pause
)
goto menu

:browser_bridge
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Dependencies are not installed yet.
  echo Please choose option 1 first.
  pause
  goto menu
)
echo.
echo Starting local bridge. Keep this window open.
echo Paste the pairing token below into the Browser AI extension popup.
echo The bridge starts with computer actions disabled.
echo.
".venv\Scripts\python.exe" -m darkspirit_bridge
set "RC=%ERRORLEVEL%"
echo.
echo Bridge stopped with exit code %RC%.
pause
goto menu

:local_ollama
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Dependencies are not installed yet.
  echo Please choose option 1 first.
  pause
  goto menu
)
echo.
echo WARNING: Local agent tasks can click, type, and change Windows UI.
echo This is separate from the browser extension and can act on your PC.
choice /c YN /n /m "Launch Ollama agent with a 10-step limit? [Y/N] "
if errorlevel 2 goto menu
".venv\Scripts\python.exe" main.py --provider ollama --model qwen3:4b --max-steps 10
set "RC=%ERRORLEVEL%"
echo.
echo Ollama agent exited with code %RC%.
pause
goto menu

:companion
if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] Dependencies are not installed yet. Choose option 1 first.
  pause
  goto menu
)
start "" ".venv\Scripts\pythonw.exe" -m darkspirit_companion --background
echo Background companion launch requested. Logs: %LOCALAPPDATA%\DarkspiritAgent\companion.log
timeout /t 2 >nul
goto menu
