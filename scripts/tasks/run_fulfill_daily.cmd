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
REM  TEMU fulfillment - DAILY 06:30  (BHY + BEL, two stores)
REM
REM  User 2026-11-06: "set a daily 06:30 shipping task; it must be
REM  activated by a DSH session, and that session must appear in the
REM  shipper workspace (CJK folder name; see WORKSPACE in add_fulfill_daily_task.py)."
REM
REM  So this script does NOT run the business logic itself. It hands
REM  control to scripts\dsh_task.py, which boots a DSH headless session
REM  with cwd = the shipper workspace (see WORKSPACE in add_fulfill_daily_task.py). The agent
REM  then calls D:\AI-workplace\TEMU\scripts\power\run_temu_fulfill.py.
REM
REM  Full chain per store (13 steps):
REM    refresh_stock -> split -> label_pw -> label_hx -> pw_receive
REM    -> pw_fail_scan -> pw_fail_fix -> pw_track -> hx_ship
REM    -> capture_pw -> hx_oms_check -> temu_confirm -> report
REM
REM  ORDER OF STORES IS FIXED: BEL first (small, ~40 orders), then BHY.
REM  Reason: BEL finishes fast, so a broken BHY run cannot stop BEL from
REM  shipping. Both stores share the same two ziniao windows, so they
REM  MUST NOT run in parallel.
REM
REM  Log : _logs\fulfill-daily\<date>.log
REM  NOTE: ASCII only on purpose (cmd.exe reads this file as OEM codepage).
REM ============================================================
setlocal EnableExtensions

set "PROJ=D:\AI-workplace\TEMU"
set "WS=" & rem Not used by this script. The DSH session cwd is fixed in
rem  scripts\tasks\add_fulfill_daily_task.py (WORKSPACE / dsh_task.py --cwd).
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%PROJ%"
if errorlevel 1 exit /b 1

set "LOGDIR=%PROJ%\_logs\fulfill-daily"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "D=%%i"
if "%D%"=="" set "D=unknown-date"
set "LOG=%LOGDIR%\%D%.log"

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 90
)

echo ============================================================>> "%LOG%"
echo [START] %D% %TIME% TEMU fulfill daily (BHY+BEL)>> "%LOG%"

REM ---- window lock: this task owns BHY+BEL for its whole run ----------
REM Without it, any of the other tasks that drive the same two ziniao
REM windows (presale / limited-watch / today-sales / pending-confirm)
REM would fight over the same pages.
REM 5 = lock busy (by design, NOT a failure).
python scripts\run_with_window_lock.py --window "BHY+BEL" --action fulfill-daily --wait 600 -- "%PY%" "%PROJ%\scripts\tasks\run_fulfill_daily_inner.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM Keep the SPACE before ">>" (RC=0 would otherwise become a redirect).
echo [END] %D% %TIME% exit=%RC% >> "%LOG%"
if not "%RC%"=="0" echo [FAIL] %D% %TIME% exit=%RC% >> "%LOGDIR%\FAILED.log"

call "%~dp0notify_panel.cmd" fulfill-daily
endlocal & exit /b %RC%
