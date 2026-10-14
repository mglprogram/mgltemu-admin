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

echo [END] %DD% %TIME% exit=%RC%>> "%LOG%"


REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" task-watch
endlocal & exit /b %RC%
