@echo off
rem ============================================================
rem  Viewer - build script (uv + Python 3.12)
rem    1. ensures backend venv (Python 3.12) + requirements installed
rem    2. npm install (first run) + vite build -> frontend\dist
rem ============================================================
setlocal
cd /d "%~dp0"

rem project-local uv cache so no dependency on %LOCALAPPDATA%\uv
set "UV_CACHE_DIR=%~dp0.cache\uv"
set "UV_PYTHON_INSTALL_DIR=%UV_CACHE_DIR%\python"

rem absolute paths so pushd/popd never break them
set "PY=%~dp0backend\.venv\Scripts\python.exe"

where uv >nul 2>nul
if errorlevel 1 (
    echo [error] uv not found in PATH. Install uv first:  pip install uv   ^(https://astral.sh/uv^)
    goto :err
)

if not exist "%PY%" (
    echo [setup] creating Python 3.12 venv with uv ...
    uv venv --python 3.12 backend\.venv || goto :err
)

echo [setup] installing backend dependencies ...
uv pip install --python "%PY%" -r backend\requirements.txt
if errorlevel 1 goto :err

where npm >nul 2>nul
if errorlevel 1 (
    echo [error] npm not found in PATH. Install Node.js first.
    goto :err
)

if not exist "frontend\node_modules" (
    echo [setup] installing frontend dependencies ...
    pushd frontend
    call npm install --no-audit --no-fund --cache "%~dp0.cache\npm-cache"
    if errorlevel 1 (popd & goto :err)
    popd
)

echo [build] building frontend with vite ...
pushd frontend
call npm run build
if errorlevel 1 (popd & goto :err)
popd

echo.
echo [done] build complete: frontend\dist
echo        run start.bat to launch the site at http://127.0.0.1:8000/
pause
goto :eof

:err
echo [error] build failed.
pause
exit /b 1