@echo off
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

python scripts\power\run_temu_abnormal.py --action pipeline --store BEL --confirm >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM NOTE: keep a SPACE between %RC% and ">>" (else RC=0 makes "0>>" a handle
REM       redirect and the exit code is eaten from the log). 2026-10-10.
echo [END] %D% %TIME% exit=%RC% >> "%LOG%"

if not "%RC%"=="0" echo [FAIL] %D% %TIME% exit=%RC% >> "%LOGDIR%\FAILED.log"

endlocal & exit /b %RC%
