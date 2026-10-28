@echo off
REM ============================================================
REM  Refresh the TEMU admin panel automation data and publish.
REM
REM  Why: automation.json is a static file bundled into the site.
REM  Only running publish_admin.py re-collects task status.
REM  Without this task the panel always shows a stale snapshot.
REM
REM  Called by Windows Task Scheduler: AutomationPanel-Refresh
REM  Log: D:\AI-workplace\TEMU\_logs\panel-refresh\refresh.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the
REM  OEM codepage (GBK here), so UTF-8 Chinese comments break it.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\panel-refresh"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\refresh.log"

REM ---- locate python ----
set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 1
)

REM ---- get DASH_PASSWORD from user-level env (process env may lack it) ----
if not defined DASH_PASSWORD (
  for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('DASH_PASSWORD','User')"`) do set "DASH_PASSWORD=%%A"
)
if not defined DASH_PASSWORD (
  echo [%date% %time%] ERROR DASH_PASSWORD not set; refusing to publish >> "%LOG%"
  exit /b 3
)

set PYTHONIOENCODING=utf-8

echo [%date% %time%] refresh start >> "%LOG%"
REM Go through panel_refresh.py's publish LOCK instead of calling publish_admin.py directly.
REM Why (measured 2026-10-15): several paths publish (each task's notify_panel hook, this
REM task, run_goods_daily). Calling publish_admin directly lets them deploy CONCURRENTLY to
REM the same Pages project: after a cold boot the morning tasks all fired at once, three
REM publishes overlapped, two wrangler uploads failed ("Failed to upload files") and one
REM spurious "not logged in to Cloudflare" came from a flaky `wrangler whoami`.
REM panel_refresh.py serialises publishes with a cross-process lock and also drains the
REM pending-refresh markers the tasks leave behind. --wait = publish here if we get the
REM lock; if someone else is publishing, just leave the marker for them (their build
REM produces the same data, so nothing is lost).
"%PY%" "%WORKDIR%\scripts\panel_refresh.py" --request --wait --reason "timer-panel-refresh" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo [%date% %time%] refresh end rc=%RC% >> "%LOG%"
endlocal & exit /b %RC%
