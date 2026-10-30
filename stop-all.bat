@echo off
REM Stop all Gunivox processes (backend uvicorn + frontend Next.js).
REM Cloudflared is left running (it's a Windows service).

echo Stopping Gunivox backend + frontend...
taskkill /F /FI "WINDOWTITLE eq GUNIVOX backend*" 2>nul
taskkill /F /FI "WINDOWTITLE eq GUNIVOX frontend*" 2>nul
taskkill /F /FI "WINDOWTITLE eq GUNIVOX backend [prod]*" 2>nul
taskkill /F /FI "WINDOWTITLE eq GUNIVOX frontend [prod]*" 2>nul

REM Kill anything still on the ports
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000.*LISTENING"') do taskkill /F /PID %%a 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000.*LISTENING"') do taskkill /F /PID %%a 2>nul

echo Done.
pause
