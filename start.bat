@echo off
rem ============================================================
rem  TCMS x AI  monorepo  - one-click start (Web UI)
rem  Usage: start.bat   (optional: set PORT=8001 first)
rem ============================================================
setlocal
cd /d "%~dp0"
if not defined PORT set "PORT=8000"

rem Upstream engine lives INSIDE this repo now (monorepo) - no sibling clone needed.
set "TCMS_UPSTREAM_DIR=%CD%\packages\engine"
set "TCMS_UPSTREAM_ROOT=%CD%\packages\engine"

echo.
echo  ============================================
echo   TCMS x AI  monorepo  -  one-click start
echo  ============================================
echo.

where uv >nul 2>&1
if errorlevel 1 goto NOUV

echo  [1/2] Syncing workspace (uv sync) ...
uv sync --quiet
if errorlevel 1 goto SYNCFAIL

echo  [2/2] Starting server -^> http://127.0.0.1:%PORT%
echo.
echo  Browser will open automatically in ~2 seconds.
echo  Close this window to stop the server.
echo.
start "" /b cmd /c "timeout /t 2 >nul & start http://127.0.0.1:%PORT%"
uv run python -m uvicorn tcms_ai_platform.server.app:app --host 127.0.0.1 --port %PORT%
goto END

:NOUV
echo  [X] uv not found. Install it first:
echo      powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
echo      (or: pip install uv)
goto END

:SYNCFAIL
echo  [X] uv sync failed. Run "uv sync" manually to see the error.
goto END

:END
pause
