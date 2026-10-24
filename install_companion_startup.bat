@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] Dependencies are missing. Run install.bat first.
  pause
  exit /b 1
)
if not exist "%LOCALAPPDATA%\DarkspiritAgent\companion.json" (
  echo [ERROR] Run setup_companion.bat first.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $shell=New-Object -ComObject WScript.Shell; $startup=[Environment]::GetFolderPath('Startup'); $lnk=$shell.CreateShortcut((Join-Path $startup 'Darkspirit Agent.lnk')); $lnk.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $lnk.Arguments='-m darkspirit_companion --background'; $lnk.WorkingDirectory='%~dp0'; $lnk.Description='Darkspirit Agent background companion'; $lnk.Save()"
if errorlevel 1 (
  echo [ERROR] Could not create the startup shortcut.
  pause
  exit /b 1
)
echo Quiet startup shortcut installed for this Windows user.
echo Remove it via the Startup folder if you want to disable automatic launch.
pause
