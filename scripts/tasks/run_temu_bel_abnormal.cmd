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
REM BEL cooperative-warehouse abnormal outbound order - daily
REM entry : run_temu_abnormal.py --action pipeline --store BEL --confirm
REM chain : auth -> open -> list -> convert -> verify
REM output: scripts\power\out\<date>\abnormal\
REM log   : _logs\abnormal-daily\<date>.log
REM NOTE  : ASCII only on purpose (cmd.exe codepage safety)
REM ============================================================
setlocal

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%PROJ%"
if errorlevel 1 exit /b 1

set "LOGDIR=%PROJ%\_logs\abnormal-daily"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM date via PowerShell (locale-independent, ISO)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "D=%%i"
if "%D%"=="" set "D=unknown-date"
set "LOG=%LOGDIR%\%D%.log"

echo ============================================================>> "%LOG%"
echo [START] %D% %TIME% BEL abnormal pipeline>> "%LOG%"

REM Window lock: this task drives the BEL store window. If another task already
REM holds it, exit 5 and do NOT run (no hard insert, no double-run).
python scripts\run_with_window_lock.py --window "BEL" --action bel-abnormal --wait 300 -- python scripts\power\run_temu_abnormal.py --action pipeline --store BEL --confirm >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM NOTE: keep a SPACE between %RC% and ">>" (else RC=0 makes "0>>" a handle
REM       redirect and the exit code is eaten from the log). 2026-10-10.
echo [END] %D% %TIME% exit=%RC% >> "%LOG%"

if not "%RC%"=="0" echo [FAIL] %D% %TIME% exit=%RC% >> "%LOGDIR%\FAILED.log"


REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" bel-abnormal
endlocal & exit /b %RC%
