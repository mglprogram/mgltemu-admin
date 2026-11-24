@echo off
REM 2026-11-24: scheduled runs must never pop a Chrome window
set "PW_UNATTENDED=1"
set "PW_HEADED=0"
REM ============================================================
REM  TEMU-Shein-Egress-Guard - daily 03:50, BEFORE the 04:00 fulfill run
REM
REM  User 2026-11-16: "add a 03:50 shein_egress_guard.py --fix (whitelist the
REM  current egress IP before the six stores start) plus a capacity alert to
REM  the TEMU Feishu group."
REM
REM  What it does:
REM    1) probe 4 SHEIN subjects (TALALEO/BD/BAN/BE) with a real signed call
REM    2) any of them rejected with openapi00002 -> shein_whitelist.py add the
REM       CURRENT egress IP, then re-probe
REM    3) read the portal whitelist to compute covered-IP count and headroom
REM       (platform limit: covered IPs < 300, a /24 eats 256 of them)
REM    4) push to the TEMU Feishu group ONLY when something happened:
REM       still blocked / self-healed / headroom <= threshold (default 40)
REM
REM  Exit: 0 all subjects callable | 2 still blocked | 3 env/config problem
REM  Log : _logs\shein-egress\<date>.log
REM  NOTE: ASCII only on purpose (cmd.exe reads this file as OEM codepage).
REM ============================================================
setlocal EnableExtensions

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%PROJ%"
if errorlevel 1 exit /b 1

set "LOGDIR=%PROJ%\_logs\shein-egress"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "D=%%i"
if "%D%"=="" set "D=unknown-date"
set "LOG=%LOGDIR%\%D%.log"

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 90
)

echo ============================================================>> "%LOG%"
echo [START] %D% %TIME% shein egress guard --fix --push>> "%LOG%"

"%PY%" -u "%PROJ%\scripts\tasks\shein_egress_guard.py" --fix --push >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM Keep the SPACE before ">>" (RC=0 would otherwise become a handle redirect).
echo [END] %D% %TIME% exit=%RC% >> "%LOG%"
REM rc=2 = a subject is still not whitelisted -> the 04:00 chain will stop at
REM split for that store. Record it for the health check, do not retry blindly.
if not "%RC%"=="0" echo [FAIL] %D% %TIME% exit=%RC% >> "%LOGDIR%\FAILED.log"

endlocal & exit /b %RC%
