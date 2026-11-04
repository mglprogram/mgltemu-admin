@echo off
REM ============================================================
REM  Push the BH-2 stock snapshot to the TEMU Feishu group.
REM  One-shot task: 2026-12-23 10:00 (registry TEMU-BH2-Stock-Notice).
REM  Data lives in scripts\payloads\bh2_stock_snapshot.json - edit THERE,
REM  not here, when the numbers change.
REM  Log: _logs\bh2-notice\push.log
REM  NOTE: ASCII only (cmd.exe reads this file as the local codepage).
REM  NOTE: exactly ONE child process, never blocks on stdin.
REM ============================================================
setlocal

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

set "LOGDIR=%PROJ%\_logs\bh2-notice"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\push.log"

echo ===========================================================>> "%LOG%"
echo [RUN] %DATE% %TIME%>> "%LOG%"

cd /d "%PROJ%"
"%PY%" -u "%PROJ%\scripts\push_bh2_snapshot.py" %* >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM NOTE: keep a SPACE between %RC% and ">>" (else RC=0 makes "0>>" a
REM       handle redirect and the exit code is swallowed). 2026-10-16.
echo [END] %DATE% %TIME% exit=%RC% >> "%LOG%"

exit /b %RC%
