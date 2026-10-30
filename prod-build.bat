@echo off
REM Build the frontend for production. Run this whenever you change frontend code.

setlocal
cd /d "%~dp0"

REM Detect LAN IP and bake it into the frontend build via .env.production
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4 Address"') do (
    set "LAN_IP=%%a"
    set "LAN_IP=!LAN_IP:~1!"
    goto :found_ip
)
:found_ip
if "%LAN_IP%"=="" set "LAN_IP=localhost"
echo NEXT_PUBLIC_API_URL=http://%LAN_IP%:8000 > frontend\.env.production

echo.
echo Building frontend for production. Backend will be reachable at http://%LAN_IP%:8000
echo.

pushd frontend
call npm run build
popd

if %errorlevel% neq 0 (
    echo Build FAILED. See errors above.
    pause
    exit /b 1
)

echo.
echo Build complete. Run prod-start.bat to serve the production build.
pause
