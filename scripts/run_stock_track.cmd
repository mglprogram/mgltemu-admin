@echo off
REM ============================================================
REM  Inventory tracking (stock-track) - 6 styles x every size,
REM  for BOTH departments: 9th (TTSA-9) and 10th (warehouse-probe).
REM
REM  Fetches two boards and writes one merged JSON:
REM    https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/  (const INV)
REM    https://warehouse-probe.app.workbuddy.host/                    (const SNAP)
REM
REM      temu-admin/public/data/stock-track.json   <- front-end page
REM      data/stock-track/history.json             <- daily snapshots (both)
REM
REM  Styles: 204203 / 204207 / 204004 / 103903 / 103902 / 103901
REM
REM  Called by Windows Task Scheduler: TEMU-Stock-Track-Daily
REM  Trigger: 17:00 daily (same hour both boards recompute)
REM  Log: D:\AI-workplace\TEMU\_logs\stock-track\daily.log
REM
REM  --wait-fresh : both boards recompute around 17:00, so on a 17:00 run
REM  the snapshot may still be yesterday's. Wait up to 8 minutes; if they
REM  never roll over, keep the old numbers and let the page show a yellow
REM  "not today" banner instead of silently pretending it is new.
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

"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_stock_track.py" --source all --wait-fresh --max-wait 480 >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] stock-track end rc=%RC% >> "%LOG%"

REM rc=2 both boards unreachable / rc=3 zero rows parsed (board layout changed)
REM / rc=4 could not write output. All three are real failures.

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" stock-track
endlocal & exit /b %RC%
