@echo off
REM ============================================================
REM  LingXing OMS abnormal outbound orders -> Excel, one file per account.
REM
REM  For EVERY LingXing account: pull the outbound orders created in the
REM  last N days, keep only status = 5 (abnormal / the UI "abnormal(N)"
REM  tab), enrich them from /outboundOrder/detail (that is the ONLY place
REM  the store name lives), then keep the BHY / BEL rows only and write:
REM
REM      D:\together\Last_mile\<ChineseFolder>\<YYYY-MM-DD>\<account>-<YYYY-MM-DD>.xlsx
REM
REM  The output root is the user's "failed orders" folder under
REM  D:\together\Last_mile; the script carries that path as its default
REM  (DEFAULT_OUT) and creates the date sub-folder itself, so THIS file
REM  stays ASCII-only. Pass --flat to drop the date sub-folder.
REM
REM  One table per account, as the user asked on 2026-10-29.
REM  Date sub-folders: user asked 2026-10-29 "files all go in a date folder".
REM
REM  Why status=5 and NOT "exceptionTime is not empty": exceptionTime also
REM  keeps orders that RECOVERED later (measured 2026-10-29: 43 with
REM  exceptionTime vs only 8 still abnormal on LM739 -- the rest were
REM  cancelled / back in warehouse). Reporting those is wrong.
REM
REM  Pure OpenAPI over HTTPS: no ZiNiao window, no window lock. It does not
REM  collide with TEMU-Stock-Track-Daily (also 17:30) which only talks HTTP
REM  to two static boards.
REM
REM  Exit codes: 0 all OK (a header-only file is still written when there
REM  are 0 abnormal orders) / 1 some account failed (other files usable)
REM  / 2 all failed, no credentials, or write error / 3 all written but a
REM  target file was locked by Excel -> saved as <account>-<date>-<HHMM>.xlsx
REM
REM  Called by Windows Task Scheduler: TEMU-OMS-Abnormal-Export-Daily
REM  Trigger: 17:30 daily
REM  Log: D:\AI-workplace\TEMU\_logs\abnormal-export\daily.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM
REM  codepage (GBK here), so UTF-8 Chinese comments break the parser.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\abnormal-export"
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

echo. >> "%LOG%"
echo ================================================== >> "%LOG%"
echo [%date% %time%] abnormal-export start >> "%LOG%"

REM 7-day window. The task runs daily but the window is wider than one day
REM on purpose: an order can turn abnormal days after it was created, and a
REM same-day-only window silently misses those. status=5 is a CURRENT state,
REM so recovered orders drop out by themselves -- the window does not pile up
REM stale rows. The file name is stable, so re-running is idempotent.
REM
REM Narrow it later with --days 1. To see why another store spiked, run the
REM script by hand once with --all-stores.
"%PY%" -X utf8 -u "%WORKDIR%\scripts\oms\export_abnormal_orders.py" --days 7 --stores BHY,BEL --json "%LOGDIR%\last.json" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo [%date% %time%] abnormal-export end rc=%RC% >> "%LOG%"

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" abnormal-export
endlocal & exit /b %RC%
