@echo off
rem zero-harness one-click start: kernel(8889) + zero server(7781) + frontend(vite)
rem All paths are relative to this script. Works from any checkout location.
setlocal EnableExtensions
set "ROOT=%~dp0"

rem -- check required tools (bun or npm, either is fine) --
set "MISSING="
where go >nul 2>nul || set "MISSING=%MISSING% go"
where uv >nul 2>nul || set "MISSING=%MISSING% uv"
set "PKG="
where bun >nul 2>nul && set "PKG=bun"
if not defined PKG where npm >nul 2>nul && set "PKG=npm"
if not defined PKG set "MISSING=%MISSING% bun-or-npm"

if defined MISSING (
    echo [error] missing tools:%MISSING%
    echo.
    echo install them, then re-run start.bat:
    echo   go  : https://golang.google.cn/dl/
    echo   uv  : powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo         or https://docs.astral.sh/uv/getting-started/installation/
    echo   bun : powershell -c "irm https://bun.sh/install.ps1 ^| iex"
    echo         or install nodejs ^(npm included^): https://nodejs.org/
    echo.
    pause
    exit /b 1
)

rem -- first run: install frontend deps --
if not exist "%ROOT%zero\frontend\web\node_modules" (
    echo [init] first run, installing frontend deps with %PKG% ...
    pushd "%ROOT%zero\frontend\web"
    call %PKG% install
    popd
)

echo [start] kernel...
start "zero-kernel" /D "%ROOT%kernel" cmd /k "go run ."
timeout /t 3 /nobreak >nul

echo [start] zero server...
start "zero-server" /D "%ROOT%zero" cmd /k "uv run python main.py"
timeout /t 2 /nobreak >nul

echo [start] frontend (%PKG%)...
start "zero-web" /D "%ROOT%zero\frontend\web" cmd /k "%PKG% run dev"

echo [done] kernel=8889  server=7781 (routines.yaml)  web=http://localhost:5173
endlocal
