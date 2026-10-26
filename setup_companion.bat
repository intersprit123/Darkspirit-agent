@echo off
setlocal
cd /d "%~dp0"
title Darkspirit Agent - Companion Setup

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Dependencies are missing. Run install.bat first.
  pause
  exit /b 1
)

echo Darkspirit background companion setup
echo Camera and microphone are OFF until you explicitly enable them.
echo Face images and voice processing stay local in this prototype.
echo.
".venv\Scripts\python.exe" -m darkspirit_companion --setup
if errorlevel 1 (
  echo.
  echo Setup failed. Check the error above.
  pause
  exit /b 1
)

echo.
echo To enable quiet startup at your Windows sign-in, run:
echo install_companion_startup.bat
echo.
pause
