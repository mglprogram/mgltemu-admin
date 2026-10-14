@echo off
REM ============================================================
REM  TEMU traffic-limit watch + HTML dashboard refresh
REM  Entry point for Windows Task Scheduler (daily 08:00).
REM  NOTE: keep this file ASCII/ANSI only - CMD breaks on UTF-8 CJK.
REM ============================================================
setlocal

set "PY=C:\Users\saing\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "PROJ=D:\AI-workplace\TEMU"
set "SCRIPT=%PROJ%\scripts\bel_goods"
set "LOGDIR=%PROJ%\_logs\temu-watch"

REM force UTF-8 for all python output so CJK lands in the log correctly
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

REM timestamp via PowerShell (wmic is deprecated/removed on newer Windows)
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"`) do set "STAMP=%%I"
if not defined STAMP set "STAMP=run"

set "LOG=%LOGDIR%\watch-%STAMP%.log"

echo ============================== >> "%LOG%"
echo [START] %DATE% %TIME% >> "%LOG%"

REM ---- 1) check focus SPU traffic limits (also writes back to SQLite) ----
"%PY%" "%SCRIPT%\watch_focus.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [WATCH] watch_focus.py exit=%RC% >> "%LOG%"

REM exit codes from watch_focus.py:
REM   0 = all focus SPUs clear        1 = some focus SPU limited
REM   2 = ALL stores failed to scrape 3 = partial scrape failure
if "%RC%"=="2" (
  echo [GATE] ALL stores failed to scrape - skipping sync/trend/dash/push. >> "%LOG%"
  echo [GATE] Check that the Ziniao store windows are open and logged in. >> "%LOG%"
  echo [END] %TIME% watch_exit=%RC% >> "%LOG%"
  exit /b %RC%
)
if "%RC%"=="3" echo [WARN] partial scrape failure - only successful stores are valid >> "%LOG%"

REM ---- 1b) safety net: make sure SQLite matches the latest snapshot ----
REM   watch_focus.py syncs internally; re-running is idempotent and guards
REM   against the dashboard ever reading a stale traffic_limited table.
"%PY%" "%SCRIPT%\sync_limited_db.py" >> "%LOG%" 2>&1
if errorlevel 1 (echo [SYNC] FAILED >> "%LOG%") else (echo [SYNC] limited tables synced >> "%LOG%")

"%PY%" "%SCRIPT%\build_trend.py" >> "%LOG%" 2>&1
set "RC3=%ERRORLEVEL%"
if not "%RC3%"=="0" (echo [TREND] FAILED >> "%LOG%") else (echo [TREND] trend updated >> "%LOG%")

REM ---- 3) refresh HTML dashboard ----
"%PY%" "%SCRIPT%\build_dashboard.py" >> "%LOG%" 2>&1
set "RC2=%ERRORLEVEL%"
if not "%RC2%"=="0" (echo [DASH] FAILED >> "%LOG%") else (echo [DASH] updated %PROJ%\data\temu_dashboard.html >> "%LOG%")

REM ---- 4) push to Feishu (needs FEISHU_WEBHOOK; keyword filter may reject) ----
if defined FEISHU_WEBHOOK (
  "%PY%" "%SCRIPT%\push_feishu.py" --send >> "%LOG%" 2>&1
  if errorlevel 1 (echo [FEISHU] push rejected or failed - see log above >> "%LOG%") else (echo [FEISHU] pushed OK >> "%LOG%")
) else (
  echo [FEISHU] skipped - FEISHU_WEBHOOK not set >> "%LOG%"
)

REM ---- 5) DO NOT publish to Cloudflare from here ----
REM   DISABLED 2026-10-12. The live site is the Vue admin (temu-admin), built and
REM   published by the "AutomationPanel-Refresh" task via publish_admin.py.
REM   This task used to run publish_cf.py (the old single-file HTML dashboard),
REM   which deployed to the SAME Pages project and OVERWROTE the admin site
REM   every morning (admin publishes ~08:00:46, this task ~08:02:19 -> last wins).
REM
REM   This task's job now ends at producing data:
REM     - SQLite traffic_limited* tables
REM     - focus_watch_latest.json / focus_watch_history.jsonl
REM     - trend_data.json
REM   build_api_data.py (run by AutomationPanel-Refresh) consumes all of the above.
REM
REM   The local single-file dashboard (data\temu_dashboard.html) is still built
REM   in step 3 for offline viewing; it is simply no longer uploaded.

echo [CF] skipped by design - admin panel owns the live site >> "%LOG%"

echo [END] %TIME% watch_exit=%RC% >> "%LOG%"
echo [NOTE] exit=1 means a focus SPU is traffic-limited (an ALERT, not a failure). >> "%LOG%"
echo [NOTE] exit=0 means all focus SPUs are clear. >> "%LOG%"
exit /b %RC%