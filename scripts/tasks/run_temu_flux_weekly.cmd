@echo off
REM ============================================================
REM  TEMU flux-accelerator weekly refresh (both stores)
REM  Entry point for Windows Task Scheduler - every Thursday 06:00.
REM  NOTE: keep this file ASCII/ANSI only - CMD breaks on UTF-8 CJK.
REM
REM  Does: open store windows -> auto login/auth if needed ->
REM        scrape activated / pending-activation for BEL+BHY -> merge JSON -> deploy to CF Pages
REM  Read-only scraping: never clicks the enable / signup / one-click-boost buttons.
REM ============================================================
setlocal

REM ---- locate python ----
REM 2026-10-29 changed: this used the DSH runtime python
REM   (C:\Users\saing\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe)
REM which is a DSH-INTERNAL interpreter: it has NO `requests` and NO `playwright`.
REM That silently broke the "refresh focus SPU list" step (cdp_transport imports
REM requests at module load) on EVERY run, and the first attempt died with rc=3.
REM Business scripts must use the user-level Python 3.13 (has requests 2.33 /
REM websockets 16 / playwright). Same interpreter the other task launchers use.
REM NOTE: the "not found" check below runs AFTER %LOG% is built (it logs there).
set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
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

REM Fail loudly if no python: a silent "0" here would hide a whole week's loss.
if not exist "%PY%" (
  echo [FATAL] python not found: %PY% >> "%LOG%"
  echo [END] %TIME% exit=1 >> "%LOG%"
  endlocal & exit /b 1
)

REM DASH_PASSWORD / CF_PROJECT_NAME / CLOUDFLARE_API_TOKEN come from user env vars.
REM 2026-10-29: publish_admin.py now falls back to the USER-level env var itself
REM (see its dash_password()), so a missing process env no longer breaks deploy.
if not defined DASH_PASSWORD echo [INFO] DASH_PASSWORD not in process env - publish_admin will read the user-level one >> "%LOG%"

REM Window lock: this is the longest window-holding task (both stores, full scrape).
REM It is a WEEKLY task with only ONE chance, so queue behind the holder instead of
REM skipping: --wait 600 (10 min). Better to start 10 minutes late than to lose the
REM whole week. Belongs here and ONLY here - do NOT also lock inside run_flux_weekly.py
REM (that made the task deadlock against itself, exit 4, on 2026-10-16 06:00).
"%PY%" "%PROJ%\scripts\run_with_window_lock.py" --window "BHY+BEL" --action flux-weekly --wait 600 -- "%PY%" "%SCRIPT%\run_flux_weekly.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo [END] %TIME% exit=%RC% >> "%LOG%"
echo [NOTE] exit=0 ok / 3 all scrapes failed (kept previous data) / 4 merge failed / 5 window busy >> "%LOG%"

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" flux-weekly
exit /b %RC%
