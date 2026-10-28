@echo off
REM ============================================================
REM  Nine-team (TTSA-9) inventory tracking - 6 styles x every size.
REM
REM  Fetches https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/
REM  (the "TTSA-9 inventory board"), extracts the embedded `const INV`
REM  snapshot, keeps only these 6 style codes and writes:
REM
REM      temu-admin/public/data/nine-inv.json   <- front-end page
REM      data/nine/history.json                 <- daily snapshots
REM
REM  Styles: 204203 / 204207 / 204004 / 103903 / 103902 / 103901
REM
REM  Called by Windows Task Scheduler: TEMU-Nine-Inventory-Daily
REM  Trigger: 17:00 daily (same hour the board itself recomputes)
REM  Log: D:\AI-workplace\TEMU\_logs\nine-inv\daily.log
REM
REM  --wait-fresh : the board recomputes at 17:00 too, so on a 17:00 run
REM  the snapshot may still be yesterday's. Wait up to 8 minutes for the
REM  board to roll over; if it never does, keep the old numbers and mark
REM  the page yellow (stale flag) instead of silently pretending it is new.
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM codepage
REM  (GBK here), so UTF-8 Chinese comments break it.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\nine-inv"
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

echo [%date% %time%] nine-inv start >> "%LOG%"

"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_nine_inv.py" --wait-fresh --max-wait 480 >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] nine-inv end rc=%RC% >> "%LOG%"

REM rc=2 fetch failed / rc=3 zero rows parsed (board structure changed)
REM / rc=4 could not write output. All three are real failures.

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" nine-inv
endlocal & exit /b %RC%
