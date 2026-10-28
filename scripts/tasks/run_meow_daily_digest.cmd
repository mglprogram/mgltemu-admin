@echo off
REM ============================================================
REM  Daily digest -> phone (MeoW). 20:00 every day.
REM  Summarises today's scheduled tasks: ok / alert / failed / not-run.
REM  Read-only: it never starts, stops or edits any task.
REM  Log: _logs\meow-digest\digest.log
REM  NOTE: ASCII only (cmd.exe reads this file with the local codepage;
REM        non-ASCII in a REM line gets executed as garbage commands).
REM  NOTE: MeoW must be reached DIRECTLY - the box has HTTP(S)_PROXY set to
REM        127.0.0.1:10808 and the proxy exit IP gets "429 IP limit" back.
REM        meow_notify.py clears proxies itself; do NOT add proxy env here.
REM ============================================================
setlocal

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

set "LOGDIR=%PROJ%\_logs\meow-digest"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set "DD=%DATE:~-10%"
set "DD=%DD:/=-%"
set "LOG=%LOGDIR%\digest.log"

echo ============================================================>> "%LOG%"
echo [RUN] %DD% %TIME%>> "%LOG%"

cd /d "%PROJ%"
"%PY%" -u "%PROJ%\scripts\meow_daily_digest.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM keep a SPACE between %RC% and ">>" (else RC=0 makes "0>>" a handle redirect)
echo [END] %DD% %TIME% exit=%RC% >> "%LOG%"

endlocal & exit /b %RC%
