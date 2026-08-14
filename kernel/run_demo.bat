@echo off
REM run_demo.bat - run the go kernel demo (16-step scenario).
REM
REM Usage:
REM   run_demo.bat                  demo against 127.0.0.1:50051 (default)
REM   run_demo.bat 127.0.0.1:50052  demo against a custom addr
REM
REM Prerequisite: the python routine server must already be running on
REM the addr (run demo/server.py in another terminal first). If the venv has
REM not installed the routine package yet, run demo/update_routine.bat first.
cd /d "%~dp0"

go run . demo %*
if errorlevel 1 (
    echo go run FAILED
    pause
    exit /b 1
)
pause
