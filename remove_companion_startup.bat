@echo off
setlocal
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Darkspirit Agent.lnk"
if exist "%STARTUP%" (
  del "%STARTUP%"
  echo Darkspirit Agent automatic startup disabled.
) else (
  echo No Darkspirit Agent startup shortcut was found.
)
pause
