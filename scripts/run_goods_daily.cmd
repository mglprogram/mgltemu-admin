@echo off
REM ============================================================
REM  TEMU on-sale goods list: daily re-export + panel rebuild.
REM
REM  Steps:
REM   1. run_goods_export.py --store all  -> official "download query
REM      result" export (xlsx) for BEL + BHY  [on-sale rows only]
REM   2. build_goods_data.py             -> public/data/goods.json
REM      (adds the low-stock list: SKU stock < 10)
REM   3. publish_admin.py --build --production -> rebuild + deploy
REM      mgltemu.us.ci (so the 08:00 snapshot is what the panel shows)
REM
REM  Called by Windows Task Scheduler: TEMU-Goods-OnSale-Daily
REM  Trigger: 08:00 daily
REM  Log: D:\AI-workplace\TEMU\_logs\goods-onsale\daily.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM
REM  codepage (GBK here), so UTF-8 Chinese comments break it.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\goods-onsale"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\daily.log"

REM ---- locate python ----
set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 1
)

set PYTHONIOENCODING=utf-8

REM ---- get DASH_PASSWORD from user-level env (process env may lack it) ----
if not defined DASH_PASSWORD (
  for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('DASH_PASSWORD','User')"`) do set "DASH_PASSWORD=%%A"
)
if not defined DASH_PASSWORD (
  echo [%date% %time%] ERROR DASH_PASSWORD not set; will export data but skip publish >> "%LOG%"
)

echo [%date% %time%] goods-onsale start >> "%LOG%"

"%PY%" "%WORKDIR%\scripts\goods\run_goods_export.py" --store all --timeout 420 >> "%LOG%" 2>&1
set "RC_EXPORT=%ERRORLEVEL%"
echo [%date% %time%] export rc=%RC_EXPORT% >> "%LOG%"

"%PY%" "%WORKDIR%\scripts\goods\build_goods_data.py" >> "%LOG%" 2>&1
set "RC_BUILD=%ERRORLEVEL%"
echo [%date% %time%] build rc=%RC_BUILD% >> "%LOG%"

REM Enrich: judge "can we add stock" for every low-stock style-size
REM using the overseas-warehouse board + Feishu Lingxing arrival tables.
REM Non-blocking: if the board/Feishu are unreachable the panel still
REM publishes with the plain low-stock list.
"%PY%" "%WORKDIR%\scripts\goods\enrich_restock.py" >> "%LOG%" 2>&1
set "RC_ENRICH=%ERRORLEVEL%"
echo [%date% %time%] enrich rc=%RC_ENRICH% >> "%LOG%"

REM Publish only when the data layer is good: a failed export must not
REM push a half-refreshed panel. build_goods_data keeps the previous
REM goods.json when no channel has data, so rc=0 there is meaningful.
set "RC_PUB=0"
if not "%RC_BUILD%"=="0" goto :skip_pub
if not defined DASH_PASSWORD goto :skip_pub
"%PY%" "%WORKDIR%\scripts\bel_goods\publish_admin.py" --build --production >> "%LOG%" 2>&1
set "RC_PUB=%ERRORLEVEL%"
goto :after_pub

:skip_pub
echo [%date% %time%] skip publish (build rc=%RC_BUILD%, password defined=%DASH_PASSWORD%) >> "%LOG%"

:after_pub
echo [%date% %time%] publish rc=%RC_PUB% >> "%LOG%"

set "RC=0"
if not "%RC_BUILD%"=="0" set "RC=%RC_BUILD%"
if not "%RC_PUB%"=="0" set "RC=%RC_PUB%"

echo [%date% %time%] goods-onsale end rc=%RC% >> "%LOG%"
endlocal & exit /b %RC%
