@echo off
REM ============================================================
REM  LingXing OMS (xlwms) order sync -> data source.
REM
REM  Pulls BHY + BEL outbound orders from the LingXing OMS OpenAPI
REM  (v1 outboundOrder/pageList + /detail), stores them incrementally
REM  in out\oms\oms_orders.jsonl, then rebuilds the panel data file
REM  temu-admin\public\data\oms-orders.json.
REM
REM  Why the OpenAPI and not web scraping: the same data, but the
REM  detail endpoint returns province/city/zip + product list + tracking
REM  + label PDF in BATCHES of 50, instead of one request per order.
REM  (The PowerShell UI shows the same fields but has no API.)
REM
REM  Called by Windows Task Scheduler: TEMU-OMS-Orders-Daily
REM  Trigger: 22:00 daily
REM  Log: D:\AI-workplace\TEMU\_logs\oms\daily.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM
REM  codepage (GBK here), so UTF-8 Chinese comments break it.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\oms"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\daily.log"

REM ---- locate python ----
set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo [%date% %time%] oms start >> "%LOG%"

REM Default lookback is 2 days (yesterday + today): the task runs at 22:00,
REM so orders created late in the day are covered, and a missed run self-heals
REM on the next day. --days 3 would widen that window.
"%PY%" -u "%WORKDIR%\scripts\oms\run_oms_orders.py" sync --days 2 >> "%LOG%" 2>&1
set "RC_SYNC=%ERRORLEVEL%"
echo [%date% %time%] sync rc=%RC_SYNC% >> "%LOG%"

set "RC_BUILD=0"
if not "%RC_SYNC%"=="0" goto :skip_build
"%PY%" -u "%WORKDIR%\scripts\oms\run_oms_orders.py" build >> "%LOG%" 2>&1
set "RC_BUILD=%ERRORLEVEL%"
echo [%date% %time%] build rc=%RC_BUILD% >> "%LOG%"
goto :after_build

:skip_build
echo [%date% %time%] skip build (sync rc=%RC_SYNC%) >> "%LOG%"

:after_build
set "RC=0"
if not "%RC_SYNC%"=="0" set "RC=%RC_SYNC%"
if not "%RC_BUILD%"=="0" set "RC=%RC_BUILD%"

echo [%date% %time%] oms end rc=%RC% >> "%LOG%"

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
call "%~dp0notify_panel.cmd" oms-orders
endlocal & exit /b %RC%
