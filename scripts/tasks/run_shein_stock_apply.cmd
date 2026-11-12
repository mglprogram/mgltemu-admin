@echo off
REM TEMU-SHEIN-Stock-Sync : adjust SHEIN stock (BD + BAN) three times a day.
REM Pipeline: refresh -> plan -> apply -> report. Logs under _logs\shein-stock-sync\.
setlocal EnableExtensions

set "WORKDIR=D:\AI-workplace\TEMU"
set "LOGDIR=%WORKDIR%\_logs\shein-stock-sync"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set "D=%%a%%b%%c"
set "LOG=%LOGDIR%\run_%D%.log"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo [FATAL] python not found >> "%LOG%"
  exit /b 90
)

cd /d "%WORKDIR%"
set "PYTHONIOENCODING=utf-8"
echo [START] %DATE% %TIME% >> "%LOG%"

set "RC=0"

echo ---- BD ---- >> "%LOG%"
%PY% "%WORKDIR%\scripts\goods\run_shein_stock.py" --store BD --policy v2 --store-daily auto --apply --push >> "%LOG%" 2>&1
set "RC_BD=%ERRORLEVEL%"

echo ---- BAN ---- >> "%LOG%"
%PY% "%WORKDIR%\scripts\goods\run_shein_stock.py" --store BAN --policy v2 --store-daily auto --apply --push >> "%LOG%" 2>&1
set "RC_BAN=%ERRORLEVEL%"

if not "%RC_BD%"=="0" set "RC=%RC_BD%"
if not "%RC%"=="0" goto :done
if not "%RC_BAN%"=="0" set "RC=%RC_BAN%"

:done
echo [END] %DATE% %TIME% exit=%RC% (BD=%RC_BD% BAN=%RC_BAN%) >> "%LOG%"
call "%WORKDIR%\scripts\tasks\notify_panel.cmd" shein-stock-sync
endlocal & exit /b %RC%
