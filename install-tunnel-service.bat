@echo off
REM Run this script with: Right-click -> "Run as administrator"
REM
REM Installs cloudflared as a Windows service so the tunnel survives:
REM   - logging out of Windows
REM   - rebooting
REM   - crashes (service auto-restarts)
REM
REM After running, friends can hit api.gunivox.online + app.gunivox.online
REM from anywhere, anytime, even when this machine is sitting idle.

setlocal

REM Verify we're elevated.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: This script must be run as Administrator.
    echo  Right-click the file and choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo ================================================================
echo  GUNIVOX :: install cloudflared as Windows service
echo ================================================================
echo.

REM 1) Kill any foreground cloudflared so the service can take the tunnel.
echo Stopping any foreground cloudflared...
taskkill /f /im cloudflared.exe >nul 2>&1
timeout /t 2 /nobreak >nul

REM 2) Delete an existing service (no-op if it doesn't exist).
echo Removing any previous service registration...
sc.exe stop cloudflared >nul 2>&1
sc.exe delete cloudflared >nul 2>&1
timeout /t 2 /nobreak >nul

REM 3) Copy config + credentials to ProgramData so LocalSystem can read them
REM    from a stable, system-owned path (no permission issues with user folder).
set "SRC=%USERPROFILE%\.cloudflared"
set "DST=%PROGRAMDATA%\Cloudflare\cloudflared"

echo Creating %DST%...
if not exist "%DST%" mkdir "%DST%"

echo Copying config + credentials...
copy /Y "%SRC%\config.yml" "%DST%\config.yml" >nul
copy /Y "%SRC%\cert.pem" "%DST%\cert.pem" >nul
for %%F in ("%SRC%\*.json") do copy /Y "%%F" "%DST%\%%~nxF" >nul

REM 4) Create the service. binPath uses the ProgramData config path.
echo Creating Windows service "cloudflared"...
sc.exe create cloudflared ^
    binPath= "\"C:\Program Files (x86)\cloudflared\cloudflared.exe\" --config \"%DST%\config.yml\" tunnel run gunivox-tunnel" ^
    DisplayName= "Cloudflared agent" ^
    start= auto

if %errorlevel% neq 0 (
    echo  ERROR: sc create failed.
    pause
    exit /b 1
)

sc.exe description cloudflared "Cloudflare tunnel for api.gunivox.online + app.gunivox.online" >nul

REM 5) Configure auto-restart on failure (1s/5s/30s backoff).
echo Configuring auto-restart on failure...
sc.exe failure cloudflared reset= 86400 actions= restart/1000/restart/5000/restart/30000 >nul

REM 6) Start it.
echo Starting service...
sc.exe start cloudflared
timeout /t 5 /nobreak >nul

echo.
echo ================================================================
echo  Final state
echo ================================================================
sc.exe query cloudflared

echo.
echo Tunnel should now be live at:
echo   https://api.gunivox.online
echo   https://app.gunivox.online
echo.
echo To check: sc.exe query cloudflared    (or services.msc)
echo To stop : sc.exe stop cloudflared
echo To start: sc.exe start cloudflared
echo To remove: sc.exe delete cloudflared
echo.
pause
