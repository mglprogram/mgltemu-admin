@echo off
REM ============================================================
REM  TEMU login-state keepalive (03:30 daily)
REM
REM  User 2026-11-15: "the TEMU scheduled tasks all failed these
REM  two days -- fix it".
REM
REM  Root cause: TEMU's session ticket (seller_temp) expires 24h
REM  after the last authorization. When it expires the server kicks
REM  EVERY page of that store, with nobody touching anything. Then
REM  every task that needs BHY/BEL fails for the rest of the day
REM  (04:00 fulfill, 08:30 abnormal, 08:50 presale, 11:00 overdue
REM  confirm, ...). Both 11-14 and 11-15 failed exactly this way.
REM
REM  Fix: refresh the ticket once a day, before the day's first
REM  task. run_temu_auth.py is idempotent -- if the store is still
REM  logged in it returns instantly and renews.
REM
REM  Runs at 03:30 so the new ticket covers 04:00 fulfill and every
REM  later task, and it finishes before the 04:00 window lock.
REM
REM  Log: D:\AI-workplace\TEMU\_logs\temu-auth-keepalive\keepalive.log
REM  Keep this file ASCII-only (cmd.exe reads it as GBK).
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\temu-auth-keepalive"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\keepalive.log"

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
echo [%date% %time%] keepalive start >> "%LOG%"

"%PY%" -X utf8 "%WORKDIR%\scripts\run_with_window_lock.py" --window "BHY+BEL" --action auth-keepalive -- "%PY%" -X utf8 "%WORKDIR%\scripts\tasks\temu_auth_keepalive.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] keepalive end rc=%RC% >> "%LOG%"

call "%~dp0notify_panel.cmd" temu-auth-keepalive
endlocal & exit /b %RC%
