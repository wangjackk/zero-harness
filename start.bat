@echo off
rem zero-harness one-click start: zero server (7781, kernel embedded) + frontend (vite)
rem All paths are relative to this script. Works from any checkout location.
setlocal EnableExtensions
set "ROOT=%~dp0"

rem -- check required tools (bun or npm, either is fine) --
set "MISSING="
where uv >nul 2>nul || set "MISSING=%MISSING% uv"
set "PKG="
where bun >nul 2>nul && set "PKG=bun"
if not defined PKG where npm >nul 2>nul && set "PKG=npm"
if not defined PKG set "MISSING=%MISSING% bun-or-npm"

if defined MISSING (
    echo [error] missing tools:%MISSING%
    echo.
    echo install them, then re-run start.bat:
    echo   uv  : powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo         or https://docs.astral.sh/uv/getting-started/installation/
    echo   bun : powershell -c "irm https://bun.sh/install.ps1 ^| iex"
    echo         or install nodejs ^(npm included^): https://nodejs.org/
    echo.
    pause
    exit /b 1
)

rem -- first run: install frontend deps --
if not exist "%ROOT%frontend\web\node_modules" (
    echo [init] first run, installing frontend deps with %PKG% ...
    pushd "%ROOT%frontend\web"
    call %PKG% install
    popd
)

rem -- kill previous instances (repeat-run = restart) --
rem 1) command-line kill: each launcher cmd below carries a "[zero-harness xxx]"
rem    marker; matching cmd.exe + taskkill /T takes down the whole tree
rem    (cmd -> uv/bun -> python/node) without touching the WT host
rem 2) port 7781/5173: clean up orphan listeners whose window is gone
echo [cleanup] stopping previous instances...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match '\[zero-harness (server|web)\]' } | ForEach-Object { taskkill /T /F /PID $_.ProcessId }" >nul 2>&1
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 7781,5173 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [start] zero server (kernel embedded)...
start "zero-server" /D "%ROOT%zero-py" cmd /k "echo [zero-harness server] && uv run python -m app.main"
timeout /t 3 /nobreak >nul

echo [start] frontend (%PKG%)...
start "zero-web" /D "%ROOT%frontend\web" cmd /k "echo [zero-harness web] && %PKG% run dev"

echo [done] server=7781 (kernel embedded, routines.yaml)  web=http://localhost:5173
endlocal
