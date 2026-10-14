@echo off
REM ============================================================
REM  TEMU presale / restock reminder -> Feishu.
REM
REM  Lightweight: refresh presale.json (TEMU goods-data + warehouse
REM  probe + Feishu Lingxing) and send the reminder message.
REM  It does NOT build or deploy the panel - so a stalled wrangler
REM  upload can never swallow the reminder.
REM
REM  Called by Windows Task Scheduler: TEMU-Presale-Notify
REM  Triggers: 08:50 / 13:50 / 17:40 daily
REM  Log: D:\AI-workplace\TEMU\_logs\presale-notify\notify.log
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd as the OEM
REM  codepage (GBK here), so UTF-8 Chinese comments break it.
REM ============================================================
setlocal

set "WORKDIR=D:\AI-workplace\TEMU"
cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

set "LOGDIR=%WORKDIR%\_logs\presale-notify"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\notify.log"

REM ---- locate python ----
set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 1
)

set PYTHONIOENCODING=utf-8

REM ---- entry: the temu-presale skill (thin wrapper) ----
REM      fallback: call the engine directly, so the reminder still works if the skill is gone
set "SKILL_PY=%USERPROFILE%\.agents\skills\temu-presale\scripts\presale.py"

echo [%date% %time%] presale notify start >> "%LOG%"
if exist "%SKILL_PY%" (
  "%PY%" -u "%SKILL_PY%" run >> "%LOG%" 2>&1
) else (
  echo [warn] skill not found, calling engine directly: %SKILL_PY% >> "%LOG%"
  "%PY%" -u "%WORKDIR%\scripts\bel_goods\build_presale_data.py" >> "%LOG%" 2>&1
)
set "RC=%ERRORLEVEL%"

echo [%date% %time%] presale notify end rc=%RC% >> "%LOG%"
endlocal & exit /b %RC%
