@echo off
setlocal
cd /d "%~dp0"
if not defined PORT set "PORT=8000"

echo ============================================
echo  TCMS x AI Test Platform  - one-click start
echo ============================================
echo.

set "PYV=%CD%\.venv\Scripts\python.exe"

where python >nul 2>&1
if errorlevel 1 goto NOPYTHON

if exist "%PYV%" goto DEPS

echo [1/4] First run: creating virtual env ...
python -m venv .venv
if errorlevel 1 goto VENVFAIL

:DEPS
echo [2/4] Checking dependencies ...
"%PYV%" -c "import tcms_ai_platform, fastapi, uvicorn, cantools, yaml, numpy" >nul 2>&1
if errorlevel 1 goto INSTALLDEPS

echo [2/4] Dependencies ready
goto ENGINE

:INSTALLDEPS
echo [2/4] Installing dependencies (first time, may take a while)...
"%PYV%" -m pip install --upgrade pip -q
"%PYV%" -m pip install -e . -q
if errorlevel 1 goto DEPSFAIL
echo [2/4] Dependencies installed
goto ENGINE

:ENGINE
echo [3/4] Checking TCMS engine ...
"%PYV%" -c "import tcms" >nul 2>&1
if errorlevel 1 goto NOENGINE
echo [3/4] TCMS engine ready
goto RUN

:NOENGINE
if defined TCMS_UPSTREAM_DIR (
  echo [3/4] Using TCMS_UPSTREAM_DIR=%TCMS_UPSTREAM_DIR%
  goto RUN
)
if exist "..\tcms-can-test\tcms" (
  echo [3/4] Found sibling tcms-can-test; will use it as engine
  goto RUN
)
echo [3/4] TCMS engine not found. Asset browsing and knowledge graph
echo       still work without it. To enable scenario-run / Agent:
echo       pip install -e ".[upstream]"   (or set TCMS_UPSTREAM_DIR)
goto RUN

:RUN
echo [4/4] Starting server -^> http://127.0.0.1:%PORT%
echo.
echo Your browser will open automatically in ~2 seconds.
echo Close this window to stop the server.
echo If the port is busy: set PORT=8001 and run again.
echo.
"%PYV%" -m uvicorn tcms_ai_platform.server.app:app --host 127.0.0.1 --port %PORT%
goto END

:NOPYTHON
echo [X] python not found. Install Python 3.11+ from:
echo     https://www.python.org/downloads/
echo     (check "Add Python to PATH" during install)
goto END

:VENVFAIL
echo [X] venv creation failed
goto END

:DEPSFAIL
echo [X] dependency install failed
goto END

:END
pause
