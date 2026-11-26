@echo off
REM ============================================================
REM  TEMU PRESALE PANEL SYNC  --  READ-ONLY
REM
REM  Fetches the LIVE stock / presale state of the three stores
REM  (BHY / BEL / KLS) from the TEMU seller backend, then
REM  recomputes the plan and PUBLISHES the "modify stock - presale"
REM  panel.
REM
REM  ** THIS TASK NEVER WRITES STOCK TO THE TEMU BACKEND. **
REM  No api_hx_ca3 / api_presale / apply step is called here.
REM  The user reviews the panel and changes stock / opens presale
REM  / clears stock by themselves in the store backend.
REM
REM  User request 2026-11-26:
REM    "read the three stores' stock and presale on a schedule,
REM     do not modify stock in the front end, but sync it to my
REM     panel; I will do the stock change / presale / clearing
REM     myself"
REM
REM  Trigger: 07:00 / 12:00 / 18:00 daily
REM  Log: _logs\presale-sync\sync.log
REM  NOTE: ASCII only (cmd.exe reads this file with the OEM codepage).
REM ============================================================
setlocal

set "PROJ=D:\AI-workplace\TEMU"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

set "LOG=%PROJ%\_logs\presale-sync\sync.log"
if not exist "%PROJ%\_logs\presale-sync" mkdir "%PROJ%\_logs\presale-sync"

echo [START] %date% %time% >> "%LOG%"
pushd "%PROJ%"

REM ---- 1) probe the three store CDP ports ----
REM  Ports change every time a store window is reopened, so never
REM  hardcode them. The helper prints one JSON line per store.
"%PY%" -X utf8 "%PROJ%\scripts\goods\presale_sync_readonly.py" --probe > "%PROJ%\_logs\presale-sync\ports.json" 2>> "%LOG%"

REM ---- 2) read-only sync: fetch live state -> plan -> panel -> publish ----
REM  Exit codes:
REM    0  every store read OK and the panel was rebuilt
REM    1  panel rebuilt but at least one store read failed (stale for
REM       that store; the panel shows it via live.staleHours)
REM    2  nothing could be read (all three stores unreachable)
"%PY%" -X utf8 "%PROJ%\scripts\goods\presale_sync_readonly.py" --all --publish >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [END] %date% %time% exit=%RC% >> "%LOG%"

popd
endlocal & exit /b %RC%
