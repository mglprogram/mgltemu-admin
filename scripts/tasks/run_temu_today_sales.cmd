@echo off
REM ============================================================
REM  TEMU "today sales" refresh -> rebuild goods.json -> data-only deploy.
REM
REM  Scrapes the TEMU sales-analytics page twice per store (last 7 days +
REM  today), joins both into goods.json, then publishes ONLY the data files
REM  (no frontend rebuild) so the live dashboard's ranking can sort by today.
REM
REM  Called by Windows Task Scheduler: TEMU-Today-Sales
REM  Triggers: 08:15 / 14:00 / 18:45 daily
REM  Log: D:\AI-workplace\TEMU\_logs\today-sales\refresh.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM codepage
REM  (GBK here), so UTF-8 Chinese comments break it.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\today-sales"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\refresh.log"

REM ---- locate python ----
set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM NOTE: do NOT fetch DASH_PASSWORD here with for /f + powershell - that would spawn a
REM second child process (bat red line). refresh_today_sales.py reads it from the user
REM environment itself (winreg) and hands it to publish_admin via the environment.

echo [%date% %time%] today-sales start >> "%LOG%"
REM Window lock: this task scrapes BOTH store windows (BHY+BEL). Without the lock it
REM collided with the other morning tasks after a cold boot and reported
REM "no CDP port found for window TM-BHY-YJF" (2026-10-15). --wait 240 = if another
REM task holds the window, queue behind it instead of failing outright.
"%PY%" -u "%WORKDIR%\scripts\run_with_window_lock.py" --window "BHY+BEL" --action today-sales --wait 240 -- "%PY%" -u "%WORKDIR%\scripts\goods\refresh_today_sales.py" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo [%date% %time%] today-sales end rc=%RC% >> "%LOG%"
echo [NOTE] rc=5 means the ziniao window was busy (another task had it); not a data failure >> "%LOG%"
REM ---- refresh the admin panel now that this task finished (success OR failure) ----
call "%~dp0notify_panel.cmd" today-sales
endlocal & exit /b %RC%
