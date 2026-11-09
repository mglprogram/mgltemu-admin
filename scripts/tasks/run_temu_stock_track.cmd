@echo off
REM ---- DIRECT NETWORK (2026-11-08) ----
REM Proxy is optional here: this box is on a domestic IP. A stale
REM HTTP(S)_PROXY pointing at a dead 127.0.0.1:7897 made every child
REM fail with WinError 10061. Clear it so children go direct.
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "http_proxy="
set "https_proxy="
set "NO_PROXY=*"
set "no_proxy=*"
REM ============================================================
REM  Inventory tracking (stock-track) - 6 styles x every size,
REM  for BOTH departments: 9th (TTSA-9) and 10th (warehouse-probe).
REM
REM  Fetches two boards and writes one merged JSON:
REM    https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/  (const INV)
REM    https://warehouse-probe.app.workbuddy.host/                    (const SNAP)
REM
REM      temu-admin/public/data/stock-track.json   <- front-end page
REM      temu-admin/public/data/linxing-stock.json <- Lingxing full stock (LM739)
REM      temu-admin/public/data/pw-stock.json      <- Powerwarehouse CA3 + Kansas
REM      data/stock-track/history.json             <- daily snapshots (both)
REM
REM  Styles: 204203 / 204207 / 204004 / 103903 / 103902 / 103901
REM
REM  Called by Windows Task Scheduler: TEMU-Stock-Track-Daily
REM  Trigger: 17:30 daily  (2026-10-29 changed from 17:00 by user request)
REM  Log: D:\AI-workplace\TEMU\_logs\stock-track\daily.log
REM
REM  --wait-fresh : both boards recompute around 17:00. Since 2026-10-29 this
REM  task runs at 17:30, i.e. AFTER that recompute, so the snapshot is normally
REM  already today's and the wait is a no-op. It is kept purely as a safety net
REM  for the case where a board lags: wait up to 8 minutes, then keep the old
REM  numbers and let the page show a yellow "not today" banner instead of
REM  silently pretending the data is new.
REM  (The script itself also gates this: before 16:50 it does NOT wait.)
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM codepage
REM  (GBK here), so UTF-8 Chinese comments break it.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\stock-track"
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

echo [%date% %time%] stock-track start >> "%LOG%"

REM ---- Powerwarehouse stock (CA3 + Kansas warehouses) ----
REM MUST run BEFORE fetch_stock_track: since 2026-11-02 the Lingxing account
REM 14450 (powerwarehouse) is DISABLED, so CA3 inventory now comes from
REM powerwarehouse.net/stock.php instead of Lingxing. fetch_stock_track reads
REM pw-stock.json to build the "total stock" column, so it has to be fresh.
REM 58 pages x 50 rows, throttled + retried (~60s). Non-blocking.
echo [%date% %time%] pw-stock start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_pw_stock.py" >> "%LOG%" 2>&1
set "RC_PW=%ERRORLEVEL%"
echo [%date% %time%] pw-stock end rc=%RC_PW% >> "%LOG%"
if not "%RC_PW%"=="0" (
  echo [%date% %time%] WARN pw-stock failed rc=%RC_PW% ^(CA3/Kansas will be stale^) >> "%LOG%"
)

"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_stock_track.py" --source all --wait-fresh --max-wait 480 >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] stock-track end rc=%RC% >> "%LOG%"

REM ---- Lingxing OMS full stock detail (LM739 only) for the "linxing" view ----
REM 286 rows / ~2s. Non-blocking: if it fails the stock-track page still works
REM (the linxing tab shows an error), so it does NOT change RC.
echo [%date% %time%] linxing-stock start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_linxing_stock.py" >> "%LOG%" 2>&1
set "RC_LX=%ERRORLEVEL%"
echo [%date% %time%] linxing-stock end rc=%RC_LX% >> "%LOG%"
if not "%RC_LX%"=="0" (
  echo [%date% %time%] WARN linxing-stock failed rc=%RC_LX% ^(page stock-track/linxing will be stale^) >> "%LOG%"
)

REM ---- Lingxing inbound orders (in-transit) map for the in-transit column tooltip ----
REM 346 orders x 50 per batch, ~9s per batch => ~70s. SLOW on purpose:
REM it feeds the hover text "which inbound order holds this size".
REM Non-blocking; a failure only makes the tooltip say "not generated yet".
echo [%date% %time%] linxing-inbound start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_linxing_inbound.py" >> "%LOG%" 2>&1
set "RC_IB=%ERRORLEVEL%"
echo [%date% %time%] linxing-inbound end rc=%RC_IB% >> "%LOG%"
if not "%RC_IB%"=="0" (
  echo [%date% %time%] WARN linxing-inbound failed rc=%RC_IB% ^(in-transit order list will be stale^) >> "%LOG%"
)

REM ---- Powerwarehouse reference numbers (the popup behind each stock number) ----
REM ~1,400 paged requests, throttled 0.12s => ~12 min. MUST run after pw-stock:
REM it reads pw-stock.json for the Fnsku list, then hits
REM stock.php?act=huojia_log&ck_sku=<fnsku>&mdck=<whId> for each.
REM Non-blocking; a failure only makes the popup say "not generated yet".
echo [%date% %time%] pw-refs start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_pw_refs.py" >> "%LOG%" 2>&1
set "RC_RF=%ERRORLEVEL%"
echo [%date% %time%] pw-refs end rc=%RC_RF% >> "%LOG%"
if not "%RC_RF%"=="0" (
  echo [%date% %time%] WARN pw-refs failed rc=%RC_RF% ^(reference numbers will be stale^) >> "%LOG%"
)

REM ---- Kdocs shipment-application sheet: ref number -> sea channel (Matson/ZIM/Yantian) ----
REM ~30 range reads over 3 monthly BH sheets (~2 min). Non-blocking; a failure
REM only leaves the channel column empty in the transit tooltip.
echo [%date% %time%] ref-channel start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_ref_channel.py" >> "%LOG%" 2>&1
set "RC_CH=%ERRORLEVEL%"
echo [%date% %time%] ref-channel end rc=%RC_CH% >> "%LOG%"
if not "%RC_CH%"=="0" (
  echo [%date% %time%] WARN ref-channel failed rc=%RC_CH% ^(channel column will be empty^) >> "%LOG%"
)

REM ---- Stock-days alert: sizes whose total stock / 7-day average is <= 7 days ----
REM User 2026-11-09: push to a SEPARATE Feishu bot group (FEISHU_WEBHOOK_STOCK),
REM not the business group. Total stock = Lingxing available (Hengxin)
REM + Powerwarehouse CA3 + Kansas, i.e. the same lxTotal the page shows.
REM Sends only when something is actually at risk; exits 0 either way.
echo [%date% %time%] stock-days start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\notify_stock_days.py" --days 7 >> "%LOG%" 2>&1
set "RC_SD=%ERRORLEVEL%"
echo [%date% %time%] stock-days end rc=%RC_SD% >> "%LOG%"
if not "%RC_SD%"=="0" (
  echo [%date% %time%] WARN stock-days alert failed rc=%RC_SD% ^(check FEISHU_WEBHOOK_STOCK^) >> "%LOG%"
)

REM rc=2 both boards unreachable / rc=3 zero rows parsed (board layout changed)
REM / rc=4 could not write output. All three are real failures.

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" stock-track
endlocal & exit /b %RC%
