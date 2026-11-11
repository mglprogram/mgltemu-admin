@echo off
REM ============================================================
REM  Stock-Track Lingxing refresh (evening, 20:30)
REM
REM  User 2026-11-11: refresh the Lingxing total-stock numbers of
REM  the stock-track page once more in the evening.
REM
REM  Why a separate task instead of a third run of
REM  run_temu_stock_track.cmd:
REM    * that pipeline takes ~31 min (Powerwarehouse 58 pages,
REM      ~28 min of reference numbers), so a 20:30 start would
REM      finish around 21:00 -- too late for an evening look;
REM    * the evening only needs the Lingxing side: LM739
REM      available amounts -> lxTotal = Hengxin available +
REM      Powerwarehouse CA3 + Kansas.
REM
REM  So this task runs exactly two steps (~30 s):
REM    1) fetch_linxing_stock.py  -- LM739 detail -> linxing-stock.json
REM    2) fetch_stock_track.py --source all --no-wait
REM       (re-attaches lxTotal/lxAvail onto the page rows, reusing
REM        the pw-stock.json already fetched at 17:30)
REM
REM  NOT run here on purpose: fetch_pw_stock.py (58 pages, ~2.5 min).
REM  CA3 / Kansas keep their 17:30 numbers until tomorrow -- they are
REM  a separate warehouse source, not "Lingxing total stock".
REM
REM  Log: D:\AI-workplace\TEMU\_logs\stock-lx-evening\evening.log
REM  Keep this file ASCII-only (cmd.exe reads it as GBK).
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\stock-lx-evening"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\evening.log"

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo. >> "%LOG%"
echo [%date% %time%] evening-lingxing start >> "%LOG%"

REM ---- 1) Lingxing LM739 detail (feeds the "linxing" tab) ----
echo [%date% %time%] linxing-stock start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_linxing_stock.py" >> "%LOG%" 2>&1
set "RC_LX=%ERRORLEVEL%"
echo [%date% %time%] linxing-stock end rc=%RC_LX% >> "%LOG%"

REM ---- 2) re-attach lxTotal to the stock-track rows (pw-stock untouched) ----
REM No --wait-fresh: without it the script takes the boards' current version
REM immediately instead of polling up to 8 minutes for a recompute.
REM --source all : both departments, same rows as the 17:30 run
echo [%date% %time%] stock-track reattach start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_stock_track.py" --source all >> "%LOG%" 2>&1
set "RC_ST=%ERRORLEVEL%"
echo [%date% %time%] stock-track reattach end rc=%RC_ST% >> "%LOG%"

REM Both steps must succeed: a stale Lingxing number on the panel is
REM exactly what this task exists to prevent.
set "RC=%RC_LX%"
if not "%RC_ST%"=="0" set "RC=%RC_ST%"
if not "%RC%"=="0" (
  echo [%date% %time%] evening-lingxing FAILED rc=%RC% >> "%LOG%"
) else (
  echo [%date% %time%] evening-lingxing ok >> "%LOG%"
)

REM store RC before calling the hook: notify_panel always returns 0
call "%~dp0notify_panel.cmd" stock-lx-evening
endlocal & exit /b %RC%
