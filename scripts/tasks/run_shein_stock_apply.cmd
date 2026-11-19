@echo off
REM TEMU-SHEIN-Stock-Sync : SHEIN stock pipeline, runs 10:30 / 14:30 / 17:30 daily.
REM
REM 2026-11-14 : switched to the POOL version
REM   scripts\goods\run_shein_pool.py = refresh -> plan(per store) -> alloc(pool) -> apply -> report
REM Rules (user 2026-11-14):
REM   - pool domain by style prefix: "3" (kids) = BE only; "1"/"2" = BD/BAN/BS by sales share
REM   - days-left <= 1 no longer wipes every store:
REM       inbound covers >= 10 days -> keep-alive spread (never 0)
REM       stock 0 but inbound on the way -> hold, wait for the goods
REM       only a real stockout (0 stock + no inbound) clears to 0
REM   - scarce pool may TRIM a store down to its quota (user approved 2026-11-14); never to 0
REM Single-store script run_shein_stock.py is kept for manual debugging only.
REM BS store (powerwarehouse 8540141066) reuses TELALEO keys, see shein_stock_api.STORE_KEY_ALIAS.
REM BE store needs a merchant-stock-export xlsx; without one the store is skipped automatically.
REM NOTE: keep this file ASCII-only (cmd.exe reads it as ANSI; Chinese REM garbles to commands).
setlocal EnableExtensions

set "WORKDIR=D:\AI-workplace\TEMU"
set "LOGDIR=%WORKDIR%\_logs\shein-stock-sync"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo [FATAL] python not found >> "%LOGDIR%\run_fatal.log"
  exit /b 90
)

REM Date for the log name. Do NOT parse %DATE%: it is locale/format dependent and
REM when launched from PowerShell it comes as "Thu 2026/11/12" (weekday first),
REM which turned the log into run_<weekday><yyyymm>.log (hit 2026-11-12).
for /f %%i in ('%PY% -c "import datetime;print(datetime.date.today().strftime('%%Y%%m%%d'))"') do set "D=%%i"
if not defined D set "D=unknown"
set "LOG=%LOGDIR%\run_%D%.log"

cd /d "%WORKDIR%"
set "PYTHONIOENCODING=utf-8"
echo [START] %DATE% %TIME% >> "%LOG%"
echo ---- pool alloc (BD+BAN+BS) ---- >> "%LOG%"

%PY% "%WORKDIR%\scripts\goods\run_shein_pool.py" --stores BD,BAN,BS --policy v2 --store-daily auto --apply --push >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo [END] %DATE% %TIME% exit=%RC% >> "%LOG%"
call "%WORKDIR%\scripts\tasks\notify_panel.cmd" shein-stock-sync
endlocal & exit /b %RC%
