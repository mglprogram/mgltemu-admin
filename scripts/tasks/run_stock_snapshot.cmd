@echo off
REM ============================================================
REM  TEMU daily stock snapshot for BHY (and later BEL).
REM  06:30 every day.
REM  Read-only: it only reads linxing-stock.json / goods.json /
REM  presale-live.json and writes JSON snapshots for the panel.
REM  It NEVER opens a browser and NEVER changes stock or presale.
REM  Presale candidates are written to data\stock-snapshot\pending-presale.json
REM  and wait for a human "confirm" before anything is executed.
REM  Log: _logs\stock-snapshot\snapshot.log
REM  NOTE: ASCII only (cmd.exe reads this file with the local codepage).
REM ============================================================
setlocal

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

set "LOG=%PROJ%\_logs\stock-snapshot\snapshot.log"
if not exist "%PROJ%\_logs\stock-snapshot" mkdir "%PROJ%\_logs\stock-snapshot"

echo [START] %date% %time% >> "%LOG%"
pushd "%PROJ%"
"%PY%" "%PROJ%\scripts\goods\daily_stock_snapshot.py" %* >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
popd
echo [END] %date% %time% exit=%RC% >> "%LOG%"

endlocal & exit /b %RC%
