@echo off
REM ============================================================
REM  TEMU inventory sync chain (BHY) -- runs at 06:30 / 12:30 / 18:30.
REM  1) refresh linxing (LM739) + powerwarehouse stock
REM  2) rebuild stock snapshot + pending presale list
REM  3) update HENGXIN warehouse stock on TEMU (only with --apply)
REM  4) verify against the ledger
REM  Clearance / presale-open are NEVER executed here: they only
REM  produce a list that a human must review first.
REM  Log: _logs\hx-sync\sync.log
REM  NOTE: ASCII only (cmd.exe reads this file with the local codepage).
REM ============================================================
setlocal

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

set "LOG=%PROJ%\_logs\hx-sync\sync.log"
if not exist "%PROJ%\_logs\hx-sync" mkdir "%PROJ%\_logs\hx-sync"

echo [START] %date% %time% >> "%LOG%"
pushd "%PROJ%"
"%PY%" "%PROJ%\scripts\goods\run_hx_sync.py" %* >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
popd
echo [END] %date% %time% exit=%RC% >> "%LOG%"

endlocal & exit /b %RC%
