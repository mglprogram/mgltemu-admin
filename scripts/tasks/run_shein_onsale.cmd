@echo off
REM ============================================================
REM  SHEIN on-sale detail (BS / BD / BAN) - daily 07:30
REM
REM  Pulls, for each store, via SHEIN OpenAPI (read-only):
REM    product/query        -> on-sale SKC list
REM    spu-info             -> style code (supplierCode), title, sizes
REM    query-sku-sales      -> 7d / 30d / yesterday sales per SKU
REM    stock-query          -> online usable stock per SKU
REM  Writes: temu-admin/public/data/shein-goods.json
REM  Then requests a panel refresh (async, coalesced by panel_refresh).
REM
REM  Called by Windows Task Scheduler: TEMU-SHEIN-OnSale-Daily
REM  Trigger: 07:30 daily
REM  Log: D:\AI-workplace\TEMU\_logs\shein-onsale\daily.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM
REM  codepage (GBK here), so UTF-8 Chinese comments break it.
REM  NOTE: spu-info results are cached in data\shein\spu-cache-<store>.json
REM  (gitignored) so a normal day only fetches newly listed SPUs.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\shein-onsale"
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

echo [%date% %time%] shein-onsale start >> "%LOG%"

"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_shein_sales.py" --no-publish >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] shein-onsale end rc=%RC% >> "%LOG%"

REM ---- panel refresh (always rc=0, never blocks; see scripts\tasks\notify_panel.cmd) ----
call "%~dp0notify_panel.cmd" shein-onsale

endlocal & exit /b %RC%
