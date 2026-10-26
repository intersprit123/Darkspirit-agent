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
  "$ErrorActionPreference='Stop'; $shell=New-Object -ComObject WScript.Shell; $startup=[Environment]::GetFolderPath('Startup'); $base='%~dp0'; $py=Join-Path $base '.venv\Scripts\pythonw.exe'; $tasks=@(@{Name='Darkspirit Agent Companion';Args='-m darkspirit_companion --background';Desc='Darkspirit background companion'},@{Name='Darkspirit Agent Bridge';Args='-m darkspirit_bridge';Desc='Darkspirit local browser AI bridge'}); foreach($task in $tasks){$lnk=$shell.CreateShortcut((Join-Path $startup ($task.Name+'.lnk'))); $lnk.TargetPath=$py; $lnk.Arguments=$task.Args; $lnk.WorkingDirectory=$base; $lnk.Description=$task.Desc; $lnk.Save()}"
if errorlevel 1 (
  echo [ERROR] Could not create the startup shortcuts.
  pause
  exit /b 1
)
echo Quiet startup shortcuts installed for the companion and local bridge.
echo The browser extension still needs to be installed in Chrome or Edge.
echo Remove the shortcuts from the Windows Startup folder to disable automatic launch.
pause
