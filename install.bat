@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Darkspirit Agent - Dependency Setup

echo ========================================
echo   Darkspirit Agent - Dependency Setup
echo ========================================
echo.

set "UV_EXE=uv"
where uv >nul 2>&1
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
  ) else (
    echo [INFO] uv was not found in PATH.
    where winget >nul 2>&1
    if errorlevel 1 (
      echo [ERROR] Install uv from https://docs.astral.sh/uv/getting-started/installation/
      echo Then reopen PowerShell and run install.bat again.
      pause
      exit /b 1
    )
    echo [INFO] Installing uv with Windows Package Manager...
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
      echo [ERROR] uv installation failed. Install it manually and retry.
      pause
      exit /b 1
    )
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
      set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
    ) else (
      set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Programs\uv;%PATH%"
      where uv >nul 2>&1
      if errorlevel 1 (
        echo [ERROR] uv was installed but is not visible to this shell.
        echo Close this window, open a new one, and run install.bat again.
        pause
        exit /b 1
      )
    )
  )
)

echo [1/3] Synchronizing dependencies, tests, face recognition, and offline voice packages...
"%UV_EXE%" sync --extra dev
if errorlevel 1 (
  echo [ERROR] Dependency synchronization failed.
  pause
  exit /b 1
)

echo.
echo [2/3] Checking Python imports and bridge syntax...
"%UV_EXE%" run python -m compileall -q darkspirit_bridge
if errorlevel 1 (
  echo [ERROR] Python compilation check failed.
  pause
  exit /b 1
)
"%UV_EXE%" run python -c "import websockets; import windows_use; print('websockets', websockets.__version__); print('Windows-Use import OK')"
if errorlevel 1 (
  echo [ERROR] A required Python import failed.
  pause
  exit /b 1
)

echo.
echo [3/3] Checking the optional local Ollama installation...
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
  "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" --version
) else (
  where ollama >nul 2>&1
  if errorlevel 1 (
    echo [INFO] Ollama was not found. Browser-based AI does not require it.
  ) else (
    ollama --version
  )
)

echo.
echo [SUCCESS] Project dependencies are installed and basic checks passed.
echo Load browser_extension as an unpacked extension in Edge or Chrome.
echo See docs\browser-ai.md for the pairing and first-task steps.
echo.
pause
exit /b 0
