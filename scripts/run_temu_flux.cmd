@echo off
REM ============================================================
REM  TEMU flux-accelerator weekly refresh (both stores)
REM  Entry point for Windows Task Scheduler - every Friday 06:00.
REM  NOTE: keep this file ASCII/ANSI only - CMD breaks on UTF-8 CJK.
REM
REM  Does: open store windows -> auto login/auth if needed ->
REM        scrape activated / pending-activation for BEL+BHY -> merge JSON -> deploy to CF Pages
REM  Read-only scraping: never clicks the enable / signup / one-click-boost buttons.
REM ============================================================
setlocal

set "PY=C:\Users\saing\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "PROJ=D:\AI-workplace\TEMU"
set "SCRIPT=%PROJ%\scripts\bel_goods"
set "LOGDIR=%PROJ%\_logs\temu-flux"

REM force UTF-8 so CJK lands correctly in the log
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"`) do set "STAMP=%%I"
if not defined STAMP set "STAMP=run"
set "LOG=%LOGDIR%\weekly-%STAMP%.log"

echo ============================== >> "%LOG%"
echo [START] %DATE% %TIME% >> "%LOG%"

REM DASH_PASSWORD / CF_PROJECT_NAME / CLOUDFLARE_API_TOKEN come from user env vars.
if not defined DASH_PASSWORD echo [WARN] DASH_PASSWORD not set - deploy will be skipped >> "%LOG%"

"%PY%" "%SCRIPT%\run_flux_weekly.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo [END] %TIME% exit=%RC% >> "%LOG%"
echo [NOTE] exit=0 ok / 3 all scrapes failed (kept previous data) / 4 merge failed >> "%LOG%"
exit /b %RC%
