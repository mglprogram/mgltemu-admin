@echo off
REM ============================================================
REM  Task watchdog - scan business scheduled tasks, alert Feishu on failure.
REM  Read-only: never starts/stops/modifies any task.
REM  Log: _logs\task-watch\watch.log
REM  NOTE: ASCII only (cmd.exe reads this file as the local codepage;
REM        non-ASCII in a REM line gets executed as garbage commands).
REM  NOTE: exactly ONE child process below, with full arguments, and it must
REM        never block on stdin - see SKILL.md "bat red lines / 2026-10-11".
REM ============================================================
setlocal

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

set "LOGDIR=%PROJ%\_logs\task-watch"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set "DD=%DATE:~-10%"
set "DD=%DD:/=-%"
set "LOG=%LOGDIR%\watch.log"

echo ============================================================>> "%LOG%"
echo [RUN] %DD% %TIME%>> "%LOG%"

cd /d "%PROJ%"
"%PY%" -u "%PROJ%\scripts\watch_tasks.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM NOTE: keep a SPACE between %RC% and ">>" (else RC=0 makes "0>>" a handle
REM       redirect and the exit code is eaten from the log). 2026-10-16.
echo [END] %DD% %TIME% exit=%RC% >> "%LOG%"

REM *** NO notify_panel hook here - deliberately removed 2026-10-16 ***
REM   watch_tasks.py already refreshes the panel ITSELF, and it does it correctly:
REM   only when there are NEW anomalies, with a cooldown (maybe_refresh_panel()
REM   + task_watch.json's panel_refresh / panel_refresh_cooldown_minutes).
REM   An unconditional hook here publishes on EVERY 30-minute run instead.
REM   Measured over 2 days: 87 of 135 publish rounds (64%) came from "task-watch"
REM   => ~48 pointless deploys/day, each one another chance to hit a flaky
REM   Cloudflare upload and turn a network blip into a "task failed" alert.
endlocal & exit /b %RC%
