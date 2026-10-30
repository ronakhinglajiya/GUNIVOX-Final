@echo off
REM Usage:
REM   dial.bat                      -> dials default test number 7990267016
REM   dial.bat 9876543210           -> dials that 10-digit Indian number
REM   dial.bat +447700900123        -> dials full E.164 number as-is

setlocal
set "NUM=%~1"
if "%NUM%"=="" set "NUM=7990267016"

REM Auto-prefix +91 if user gave a 10-digit Indian number
echo %NUM% | findstr /R "^[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]$" >nul && set "NUM=+91%NUM%"

echo Dialing %NUM% via http://localhost:8000/api/calls/originate
curl -sS -X POST http://localhost:8000/api/calls/originate ^
  -H "Content-Type: application/json" ^
  -d "{\"to\":\"%NUM%\"}"
echo.
endlocal
