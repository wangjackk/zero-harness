@echo off
REM kshell server launcher - run in GoLand Terminal under D:\shell\kshell\kernel
REM Each command runs in foreground so you see logs. Open multiple terminals.
REM
REM Usage:
REM   start_servers.bat demo     single server on 50051 (for kernel demo)
REM   start_servers.bat srvA    cross server A on 50061 (XsA/XsC)
REM   start_servers.bat srvB    cross server B on 50062 (XsB)
REM   start_servers.bat clean   kill all kshell servers

setlocal
set DEMO_DIR=%~dp0..\demo
set PY=%DEMO_DIR%\.venv\Scripts\python.exe

if "%1"=="demo" (
    echo === demo server on 50051 (Ctrl+C to stop) ===
    cd /d %DEMO_DIR% && "%PY%" server.py
    goto :eof
)

if "%1"=="srvA" (
    echo === cross server A on 50061 (XsA/XsC) (Ctrl+C to stop) ===
    cd /d %DEMO_DIR% && "%PY%" cross_server.py 50061 50062 a
    goto :eof
)

if "%1"=="srvB" (
    echo === cross server B on 50062 (XsB) (Ctrl+C to stop) ===
    cd /d %DEMO_DIR% && "%PY%" cross_server.py 50061 50062 b
    goto :eof
)

if "%1"=="clean" (
    echo === killing kshell servers ===
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr ":50051 :50061 :50062"') do taskkill /F /PID %%P 2>nul
    goto :eof
)

echo Usage: start_servers.bat demo ^| srvA ^| srvB ^| clean
echo   demo   single server on 50051 for kernel demo
echo   srvA   cross server A on 50061 XsA/XsC
echo   srvB   cross server B on 50062 XsB
echo   clean  kill all kshell servers
echo.
echo Open multiple GoLand terminals, one per server, to see each log.
endlocal
