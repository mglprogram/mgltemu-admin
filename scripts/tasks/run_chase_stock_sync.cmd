@echo off
REM ============================================================
REM  Chase-followup sheet (TTSA cui huo gen jin biao) stock sync.
REM
REM  User 2026-11-20: "every day 10:00 and 15:00 update the chase sheet".
REM
REM  Two sheets inside https://www.kdocs.cn/l/clmBVSbSdpaL :
REM    worksheet 13  Hengxin stock  <- Lingxing LM739 (available / in-transit)
REM    worksheet 19  Powerware stock <- Powerware 14450 (Bobbingtonghao2) CA3 + Kansas
REM
REM  Long-term naming rules baked into scripts\goods\sync_chase_stock.py :
REM    - strip the shop prefix (BS2- / BH- / BLA- ...), including a leading dash
REM      (-BH-H103903-2B-XXL -> H103903-2B-XXL)
REM    - alias 203804R -> 203804 in BOTH sheets
REM
REM  Log: D:\AI-workplace\TEMU\_logs\chase-stock\daily.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM codepage
REM  (GBK here), so UTF-8 Chinese comments break it.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\chase-stock"
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
set PYTHONUTF8=1

echo [%date% %time%] chase-stock start >> "%LOG%"

REM ---- Powerware stock first: the PW sheet reads pw-stock.json ----
REM ~60 pages, ~60s. Non-blocking: a failure leaves the PW sheet on the
REM previous snapshot (sync_chase_stock still writes what it has).
echo [%date% %time%] pw-stock start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\fetch_pw_stock.py" >> "%LOG%" 2>&1
set "RC_PW=%ERRORLEVEL%"
echo [%date% %time%] pw-stock end rc=%RC_PW% >> "%LOG%"
if not "%RC_PW%"=="0" (
  echo [%date% %time%] WARN pw-stock failed rc=%RC_PW% ^(PW sheet keeps previous snapshot^) >> "%LOG%"
)

REM ---- write both sheets ----
echo [%date% %time%] sync start >> "%LOG%"
"%PY%" -X utf8 "%WORKDIR%\scripts\goods\sync_chase_stock.py" --apply >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] sync end rc=%RC% >> "%LOG%"

REM rc=0 ok / rc=1 python or kdocs error / rc=2 login lost or sheet missing
endlocal & exit /b %RC%
