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
REM 2026-11-24: scheduled runs must never pop a Chrome window
set "PW_UNATTENDED=1"
set "PW_HEADED=0"
set "no_proxy=*"
REM ============================================================
REM Cooperative-warehouse abnormal outbound orders - daily
REM Stores: BEL then KLS (KinglamanSport local) in ONE run
REM entry : run_temu_abnormal.py --action pipeline --store <store> --confirm
REM chain : auth -> open -> list -> convert -> verify
REM output: scripts\power\out\<date>\abnormal\  (+ abnormal_kls\)
REM log   : _logs\abnormal-daily\<date>.log
REM NOTE  : ASCII only on purpose (cmd.exe codepage safety)
REM
REM 2026-11-24 user order: run BOTH stores in this one task, BEL then KLS.
REM The lock is taken ONCE for both windows (BHY+BEL+KinglamanSport is not
REM needed: these two stores only) so one run owns both stores end to end.
REM Runs are sequential and independent: BEL failing does NOT block KLS, and
REM the exit code is non-zero if EITHER failed.
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
echo [START] %D% %TIME% abnormal pipeline BEL+KLS>> "%LOG%"

REM 2026-11-24: one lock over BOTH windows for the whole run; BEL then KLS run
REM sequentially inside it. Called with `call` so this file stays loaded.
call :iterate
set "RC=%ERRORLEVEL%"

REM NOTE: keep a SPACE between %RC% and ">>" (else RC=0 makes "0>>" a handle
REM       redirect and the exit code is eaten from the log). 2026-10-10.
echo [END] %D% %TIME% exit=%RC% >> "%LOG%"

if not "%RC%"=="0" echo [FAIL] %D% %TIME% exit=%RC% >> "%LOGDIR%\FAILED.log"


REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" bel-abnormal
endlocal & exit /b %RC%

REM ============================================================
REM  :iterate  -- run each store in turn, INSIDE the window lock.
REM  Each store gets its own RC line so the log says exactly who failed.
REM  Returns the first non-zero RC (0 only if every store succeeded).
REM ============================================================
:iterate
REM Window lock covers BOTH store windows for the whole run: if another task
REM already holds either window, run_with_window_lock exits 5 and we skip
REM (no hard insert, no double run). Inside the lock: BEL then KLS, in order.
python scripts\run_with_window_lock.py --window "BEL+KinglamanSport" --action bel-abnormal --wait 300 -- cmd /c "%~dp0run_temu_abnormal_two.cmd" "%LOG%"
exit /b %ERRORLEVEL%echo ============================================================>> %LOG%
echo [START] %D% %TIME% abnormal pipeline BEL+KinglamanSport>> %LOG%

REM 2026-11-24 user order: run BOTH stores in this one task, BEL then KLS.
REM One window lock over both windows for the whole run; if another task
REM already holds either window, run_with_window_lock exits 5 and we skip
REM (no hard insert, no double run). The per-store loop lives in
REM run_temu_abnormal_two.cmd (a separate file on purpose: this whole body
REM is redirected into %LOG%, and cmd re-reads a .cmd it is executing).
python scripts\run_with_window_lock.py --window "BEL+KinglamanSport" --action bel-abnormal --wait 300 -- cmd /c "%~dp0run_temu_abnormal_two.cmd" "%LOG%"
set "RC=%ERRORLEVEL%"

REM NOTE: keep a SPACE between %RC% and ">>" (else RC=0 makes "0>>" a handle
REM       redirect and the exit code is eaten from the log). 2026-10-10.
echo [END] %D% %TIME% exit=%RC% >> "%LOG%"

if not "%RC%"=="0" echo [FAIL] %D% %TIME% exit=%RC% >> "%LOGDIR%\FAILED.log"


REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" bel-abnormal
endlocal & exit /b %RC%
