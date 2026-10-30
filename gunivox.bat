@echo off
REM gunivox.bat - one file, runs everything, supervised with auto-restart.
REM
REM Usage:
REM   Double-click             -> launches backend + frontend (dev) in two cmd windows
REM   gunivox.bat prod         -> frontend runs `npm run start` instead of `npm run dev`
REM                              (requires `cd frontend && npm run build` first)
REM
REM This single file dispatches three roles based on its first arg:
REM   no arg        - master: seeds .env.local, spawns the two supervisor windows
REM   backend       - backend supervisor loop (auto-restart uvicorn on crash)
REM   frontend      - frontend supervisor loop (auto-restart npm on crash)

setlocal ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

if /I "%~1"=="backend"  goto :run_backend
if /I "%~1"=="frontend" goto :run_frontend

REM ====================================================================
REM  MASTER mode
REM ====================================================================

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: no venv at %PY%
    echo Create it first:
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r backend\requirements.txt
    pause
    exit /b 1
)

REM Seed frontend\.env.local with the LAN IP only if it's not already present.
REM Preserves a hand-edited tunnel URL across restarts.
if not exist "frontend\.env.local" (
    for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4 Address"') do (
        set "LAN_IP=%%a"
        set "LAN_IP=!LAN_IP:~1!"
        echo NEXT_PUBLIC_API_URL=http://!LAN_IP!:8000 > frontend\.env.local
        goto :seeded
    )
)
:seeded

if not exist "logs" mkdir "logs"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=dev"

echo ================================================================
echo  GUNIVOX :: launching backend + frontend (supervised)
echo ================================================================
echo  Backend  : http://localhost:8000
echo  Frontend : http://localhost:3000   (mode: %MODE%)
echo  Logs     : %~dp0logs\
echo  Tunnel   : api.gunivox.online / app.gunivox.online (if cloudflared up)
echo.
echo  Close the spawned windows to stop the services.
echo ================================================================

start "GUNIVOX backend"  cmd /k ""%~f0" backend"
timeout /t 2 /nobreak > nul
start "GUNIVOX frontend" cmd /k ""%~f0" frontend" %MODE%

echo.
echo Both supervisor windows spawned. This window can be closed.
pause
exit /b 0

REM ====================================================================
REM  BACKEND supervisor
REM ====================================================================
:run_backend
title GUNIVOX backend [supervised]
set "PY=%~dp0venv\Scripts\python.exe"
set "LOG=%~dp0logs\backend.log"
echo ================================================================
echo  GUNIVOX backend supervisor (auto-restart on crash, 3s backoff)
echo  log: %LOG%
echo ================================================================

:loop_backend
echo [%date% %time%] starting backend >> "%LOG%"
pushd backend
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info >> "%LOG%" 2>&1
set "RC=%errorlevel%"
popd
echo [%date% %time%] backend exited rc=%RC%, restarting in 3s >> "%LOG%"
timeout /t 3 /nobreak > nul
goto :loop_backend

REM ====================================================================
REM  FRONTEND supervisor
REM ====================================================================
:run_frontend
title GUNIVOX frontend [supervised]
set "LOG=%~dp0logs\frontend.log"
set "NPM_CMD=run dev"
if /I "%~2"=="prod" set "NPM_CMD=run start"
echo ================================================================
echo  GUNIVOX frontend supervisor (auto-restart on crash, 3s backoff)
echo  mode: npm %NPM_CMD%
echo  log:  %LOG%
echo ================================================================

:loop_frontend
echo [%date% %time%] starting frontend (npm %NPM_CMD%) >> "%LOG%"
pushd frontend
call npm %NPM_CMD% >> "%LOG%" 2>&1
set "RC=%errorlevel%"
popd
echo [%date% %time%] frontend exited rc=%RC%, restarting in 3s >> "%LOG%"
timeout /t 3 /nobreak > nul
goto :loop_frontend
