@echo off
rem ============================================================
rem  Viewer - one-click launcher
rem    start.bat        production mode: uvicorn serves frontend/dist
rem    start.bat dev    dev mode: vite dev server + uvicorn API
rem ============================================================
setlocal
cd /d "%~dp0"

rem absolute paths so pushd/popd never break them
set "PY=%~dp0backend\.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [setup] creating Python virtual environment ...
    python -m venv backend\.venv || goto :err
)

echo [setup] ensuring backend dependencies ...
"%PY%" -m pip install -q -r backend\requirements.txt
if errorlevel 1 goto :err

if "%~1"=="dev" goto :dev

rem ---------- production mode ----------
if not exist "frontend\dist\index.html" (
    echo [build] frontend\dist not found - building now ...
    call "%~dp0build.bat"
    if errorlevel 1 goto :err
)

echo [start] Viewer running at http://127.0.0.1:8000/  (Ctrl+C to stop)
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000/"
pushd backend
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
popd
goto :eof

rem ---------- dev mode ----------
:dev
where npm >nul 2>nul
if errorlevel 1 (
    echo [error] npm not found in PATH. Install Node.js first.
    goto :err
)
if not exist "frontend\node_modules" (
    echo [setup] installing frontend dependencies ...
    pushd frontend
    call npm install --no-audit --no-fund --cache "%~dp0.cache\npm-cache"
    popd
)
start "viewer-vite" cmd /k "cd /d %~dp0frontend && npm run dev"
echo [start] vite dev server: http://localhost:5173/  ^(proxies /api to :8000^)
start "" cmd /c "timeout /t 4 >nul & start http://localhost:5173/"
pushd backend
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
popd
goto :eof

:err
echo [error] startup failed.
pause
exit /b 1
