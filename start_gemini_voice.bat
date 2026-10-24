@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] Project dependencies are missing.
  echo Run install.bat or choose option 1 from start.bat.
  pause
  exit /b 1
)
start "Darkspirit Gemini Voice" ".venv\Scripts\pythonw.exe" -m darkspirit_companion.gemini_voice
exit /b 0
