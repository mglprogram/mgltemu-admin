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

REM sync grabs the LAST COMPLETED US DAY (plus one more for self-healing) and
REM converts it to the China-time window the API actually uses.
REM The task fires at 15:00 China = 00:00 US Pacific, when the whole of the US
REM has finished the previous day, so the window is exactly "yesterday in the US".
"%PY%" -u "%WORKDIR%\scripts\oms\run_oms_orders.py" sync --days 2 >> "%LOG%" 2>&1
set "RC_SYNC=%ERRORLEVEL%"
echo [%date% %time%] sync rc=%RC_SYNC% >> "%LOG%"

set "RC_BUILD=0"
if not "%RC_SYNC%"=="0" goto :skip_build
"%PY%" -u "%WORKDIR%\scripts\oms\run_oms_orders.py" build >> "%LOG%" 2>&1
set "RC_BUILD=%ERRORLEVEL%"
echo [%date% %time%] build rc=%RC_BUILD% >> "%LOG%"

REM Merge into the "order distribution" dataset: POWER history + OMS increment,
REM deduped by the platform order number (PO-211-...). This is what feeds the
REM /#/temu/order-geo page and the dashboard US map.
set "RC_GEO=0"
if not "%RC_BUILD%"=="0" goto :skip_build
"%PY%" -u "%WORKDIR%\scripts\pw_orders\run_pw_order_geo.py" build --out "%WORKDIR%\out\pw-orders" --admin "%WORKDIR%\temu-admin" >> "%LOG%" 2>&1
set "RC_GEO=%ERRORLEVEL%"
echo [%date% %time%] order-geo build rc=%RC_GEO% >> "%LOG%"
goto :after_build

:skip_build
echo [%date% %time%] skip build (sync rc=%RC_SYNC%) >> "%LOG%"
set "RC_GEO=0"

:after_build
REM Publish data only when the dataset rebuilt cleanly, so a failed sync can
REM never push a half-refreshed panel.
set "RC_PUB=0"
if not "%RC_GEO%"=="0" goto :skip_pub
if not defined DASH_PASSWORD (
  for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('DASH_PASSWORD','User')"`) do set "DASH_PASSWORD=%%A"
)
if not defined DASH_PASSWORD goto :skip_pub
"%PY%" -u "%WORKDIR%\scripts\bel_goods\publish_admin.py" --build --production --skip-data >> "%LOG%" 2>&1
set "RC_PUB=%ERRORLEVEL%"
echo [%date% %time%] publish rc=%RC_PUB% >> "%LOG%"
goto :after_pub

:skip_pub
echo [%date% %time%] skip publish >> "%LOG%"

:after_pub
set "RC=0"
if not "%RC_SYNC%"=="0" set "RC=%RC_SYNC%"
if not "%RC_BUILD%"=="0" set "RC=%RC_BUILD%"
if not "%RC_GEO%"=="0" set "RC=%RC_GEO%"
if not "%RC_PUB%"=="0" set "RC=%RC_PUB%"

echo [%date% %time%] oms end rc=%RC% >> "%LOG%"

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
call "%~dp0notify_panel.cmd" oms-orders
endlocal & exit /b %RC%
