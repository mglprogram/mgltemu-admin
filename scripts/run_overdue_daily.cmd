@echo off
REM ============================================================
REM TEMU overdue "confirm shipping" - daily for BHY + BEL
REM entry : run_temu_buylabel.py --action overdue --store <store>
REM NOTE  : WITHOUT --with-label => confirm-only, NO new orders,
REM         NO cost. Do NOT add --with-label here (that would
REM         place real paid orders).
REM         => SAFE TO RETRY: re-running only re-confirms, never
REM            buys a label or places an order.
REM log   : _logs/overdue-daily/<date>.log
REM NOTE  : ASCII only on purpose (cmd.exe codepage safety)
REM
REM RETRY (added 2026-10-14, user asked: "failed => re-run"):
REM   Each store is retried independently, up to OVERDUE_RETRY times
REM   with OVERDUE_RETRY_WAIT seconds between attempts.
REM   WHY NOT Task Scheduler's native RestartCount:
REM     measured 2026-10-14 -- RestartCount=2 / RestartInterval=1min
REM     did NOT retry a payload that exits 1 (payload ran exactly once
REM     in 3.5 min). The engine's "restart on failure" does not cover a
REM     plain non-zero exit code, so the retry must live here.
REM   Retrying per store matters: a BHY failure must not re-run a BEL
REM   attempt that already succeeded.
REM ============================================================
setlocal enabledelayedexpansion

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

REM retry policy (override by setting these before calling)
if not defined OVERDUE_RETRY set "OVERDUE_RETRY=2"
if not defined OVERDUE_RETRY_WAIT set "OVERDUE_RETRY_WAIT=60"

cd /d "%PROJ%"
if errorlevel 1 exit /b 1

set "LOGDIR=%PROJ%\_logs\overdue-daily"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "D=%%i"
if "%D%"=="" set "D=unknown-date"
set "LOG=%LOGDIR%\%D%.log"

echo ============================================================>> "%LOG%"
echo [START] %D% %TIME% overdue confirm BHY+BEL (retry=%OVERDUE_RETRY% wait=%OVERDUE_RETRY_WAIT%s)>> "%LOG%"

set "RCALL=0"

REM ---------- BHY ----------
call :run_store BHY RC1
REM ---------- BEL ----------
call :run_store BEL RC2

echo [END] %D% %TIME% BHY=%RC1% BEL=%RC2% >> "%LOG%"

if not "%RCALL%"=="0" echo [FAIL] %D% %TIME% BHY=%RC1% BEL=%RC2% >> "%LOGDIR%\FAILED.log"

endlocal & exit /b %RCALL%

REM ============================================================
REM :run_store <STORE> <OUT_VAR>
REM   Runs one store, retrying on failure. Sets <OUT_VAR> to the
REM   final exit code. Also bumps RCALL when the final code != 0.
REM ============================================================
:run_store
set "STORE=%~1"
set "OUTV=%~2"
set "TRY=0"

:retry_loop
set /a TRY+=1
echo ---- %STORE% (attempt !TRY!/%OVERDUE_RETRY%) ---- >> "%LOG%"
python scripts\power\run_temu_buylabel.py --action overdue --store %STORE% >> "%LOG%" 2>&1
set "RC=!ERRORLEVEL!"

if "!RC!"=="0" goto :store_done

if !TRY! LSS %OVERDUE_RETRY% (
  echo [RETRY] %STORE% exit=!RC! -> wait %OVERDUE_RETRY_WAIT%s then retry >> "%LOG%"
  REM `timeout` is not usable here: with redirected stdio it errors out
  REM ("ERROR: Input redirection is not supported"). ping is the portable
  REM sleep that works under Task Scheduler with no console.
  REM ping -n N sleeps N-1 seconds, so use WAIT+1 to actually wait WAIT.
set /a SLEEPN=%OVERDUE_RETRY_WAIT%+1
ping -n !SLEEPN! 127.0.0.1 >nul 2>&1
  goto :retry_loop
)

echo [RETRY] %STORE% still failing after %OVERDUE_RETRY% attempts (exit=!RC!) >> "%LOG%"

:store_done
echo [%STORE%] exit=!RC! (attempts=!TRY!) >> "%LOG%"
set "%OUTV%=!RC!"
if not "!RC!"=="0" set "RCALL=1"
exit /b 0
