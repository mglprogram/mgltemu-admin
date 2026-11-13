@echo off
REM TEMU-SHEIN-Stock-Sync : adjust SHEIN stock (BD + BAN + BS) three times a day.
REM Pipeline: refresh -> plan -> apply -> report. Logs under _logs\shein-stock-sync\.
REM BS 店（帕沃尔 8540141066）密钥走 TELALEO 主体，见 shein_stock_api.STORE_KEY_ALIAS。
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

set "RC=0"

echo ---- BD ---- >> "%LOG%"
%PY% "%WORKDIR%\scripts\goods\run_shein_stock.py" --store BD --policy v2 --store-daily auto --apply --push >> "%LOG%" 2>&1
set "RC_BD=%ERRORLEVEL%"

echo ---- BAN ---- >> "%LOG%"
%PY% "%WORKDIR%\scripts\goods\run_shein_stock.py" --store BAN --policy v2 --store-daily auto --apply --push >> "%LOG%" 2>&1
set "RC_BAN=%ERRORLEVEL%"

REM ---- BS ----  复用上面刚刷的帕沃尔数据（--skip-pw-refresh，只刷领星）；
REM 没导出过「商家库存导出」就跳过、不算失败（--skip-if-no-export）
echo ---- BS ---- >> "%LOG%"
%PY% "%WORKDIR%\scripts\goods\run_shein_stock.py" --store BS --policy v2 --store-daily auto --apply --push --skip-pw-refresh --skip-if-no-export >> "%LOG%" 2>&1
set "RC_BS=%ERRORLEVEL%"

if not "%RC_BD%"=="0" set "RC=%RC_BD%"
if not "%RC%"=="0" goto :done
if not "%RC_BAN%"=="0" set "RC=%RC_BAN%"
if not "%RC%"=="0" goto :done
if not "%RC_BS%"=="0" set "RC=%RC_BS%"

:done
echo [END] %DATE% %TIME% exit=%RC% (BD=%RC_BD% BAN=%RC_BAN% BS=%RC_BS%) >> "%LOG%"
call "%WORKDIR%\scripts\tasks\notify_panel.cmd" shein-stock-sync
endlocal & exit /b %RC%
