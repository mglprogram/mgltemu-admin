@echo off
REM ============================================================
REM  Shipment tracking (shipment-track)
REM
REM  Four sources merged into one JSON for the admin panel:
REM    1) LingXing OMS OpenAPI v1 inbound orders (LM739 / ZOO US-West)
REM       -> master key = inbound order no + product/size/qty per row
REM    2) hengxe tracking  http://hengxe.nextsls.com/tracking/app?inajax=1
REM       &tracking_number=<inboundNo>  (no login, one number at a time)
REM       -> arrival node (ports) + latest node. LM739 only.
REM    3) PowerWarehouse head-haul (14450), ref no starts with "14450":
REM       LingXing for 14450 is DISABLED, so those rows come from PW.
REM    4) local history xlsx -> calibration of arrival->shelved days
REM
REM      temu-admin/public/data/shipment-track.json   <- front-end page
REM      out/shipment-track/last.json                 <- same, for debugging
REM
REM  Called by Windows Task Scheduler: TEMU-Shipment-Track-Daily
REM  Trigger: 12:05 daily
REM  12:05 is derived from the real shelving clock (LingXing stock-flow
REM  rows with changeAmount > 0 on an inbound order, last 30 days):
REM  median 09:56, P75 11:10, P90 11:31, P95 11:44 -> 12:05 catches the
REM  same-day shelving wave and still leaves room before the 12:30 watch.
REM  Recompute with: python scripts\oms\fetch_stock_flow_shelf.py --days 30
REM  Log: D:\AI-workplace\TEMU\_logs\shipment-track\daily.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd with the OEM
REM  codepage (GBK here), so UTF-8 Chinese comments break parsing.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\shipment-track"
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

echo [%date% %time%] shipment-track start >> "%LOG%"

"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_shipment_track.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] shipment-track end rc=%RC% >> "%LOG%"

REM rc=2 no workbook found (WeChat cache path changed / not received yet)
REM rc=3 parsed zero shipments (header layout changed)
REM rc=4 could not write output. All three are real failures.
REM Individual tracking misses and OMS misses are NOT fatal: they are
REM recorded per row (logistics.ok=false / oms.found=false) and shown
REM on the page, so a single bad number never fails the whole run.

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" shipment-track
endlocal & exit /b %RC%
