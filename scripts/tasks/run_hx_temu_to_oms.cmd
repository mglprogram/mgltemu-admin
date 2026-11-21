@echo off
REM ============================================================
REM  HX smart ship: TEMU warehouse-filtered export -> Linxing OMS
REM  (allocate Temu auto-warehouse -> approve -> report failed orders)
REM  Entry point for Windows Task Scheduler (daily 18:30 / 21:00).
REM  NOTE: keep this file ASCII/ANSI only - CMD breaks on UTF-8 CJK.
REM ============================================================
setlocal

set "PY=C:\Users\saing\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
set "PROJ=D:\AI-workplace\TEMU"
set "LOGDIR=%PROJ%\_logs\hx-smart-ship"

REM Keep proxy env (GitHub/Feishu need it); only clear for the local browser CDP hops
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"`) do set "STAMP=%%I"
if not defined STAMP set "STAMP=run"
set "LOG=%LOGDIR%\hx-ship-%STAMP%.log"

echo ============================== >> "%LOG%"
echo [START] %DATE% %TIME% >> "%LOG%"

cd /d "%PROJ%"

set "RC=0"
for %%S in (BHY BEL) do (
  echo ----- store %%~S ----- >> "%LOG%"
  "%PY%" "%PROJ%\scripts\power\hx_temu_to_oms.py" --store %%~S --confirm >> "%LOG%" 2>&1
  if errorlevel 1 set "RC=1"
)

echo [END] %DATE% %TIME% exit=%RC% >> "%LOG%"

call "%PROJ%\scripts\tasks\notify_panel.cmd" hx-smart-ship
endlocal & exit /b %RC%
