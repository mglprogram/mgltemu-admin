@echo off
REM ============================================================
REM  run_temu_fulfill_one.cmd -- launch the TEMU fulfill chain for ONE
REM  store as a FULLY DETACHED process (recommended for production).
REM
REM  WHY (measured 2026-11-27):
REM    Running the chain inside an interactive shell DIES at the auth
REM    step. Four runs in a row stopped right after printing
REM        [auth-guard] about to run: run_temu_auth.py --store X
REM    i.e. the parent got reaped while waiting for its child.
REM    The SAME chain started via Start-Process -WindowStyle Hidden (no
REM    attached console) completed end to end:
REM        us  -> OK rc=0
REM        cc  -> OK rc=0 (cookie landed)
REM        view -> switched back to 'KinglamanSport local'
REM        RESULT ok:true failed:[] stopped_at:""
REM    => never run the long chain in an attached shell; use this.
REM
REM  USAGE:
REM    run_temu_fulfill_one.cmd BHY
REM    run_temu_fulfill_one.cmd BEL --confirm
REM    run_temu_fulfill_one.cmd KinglamanSport --from export_orders --to split_dedupe
REM
REM  Extra args are passed through verbatim.
REM  Log  : _logs\fulfill-one\<store>_<date>_<hhmmss>.log
REM  Exit : 0 = launched. Child exit code is in the log tail.
REM
REM  ASCII only on purpose (cmd.exe reads this as OEM codepage).
REM  Args are written to a temp .ps1 so cmd quoting cannot mangle them.
REM ============================================================
setlocal EnableExtensions DisableDelayedExpansion

if "%~1"=="" (
  echo [ERROR] usage: run_temu_fulfill_one.cmd ^<store^> [extra args...]
  exit /b 90
)

set "STORE=%~1"
shift

set "HTTP_PROXY="
set "HTTPS_PROXY="
set "http_proxy="
set "https_proxy="
set "NO_PROXY=*"
set "no_proxy=*"
set "PW_UNATTENDED=1"
set "PW_HEADED=0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

set "PROJ=D:\AI-workplace\TEMU"
set "ENTRY=%PROJ%\scripts\power\run_temu_fulfill.py"
set "LOGDIR=%PROJ%\_logs\fulfill-one"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [ERROR] python not found
  exit /b 90
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "STAMP=%%i"
if "%STAMP%"=="" set "STAMP=unknown"
set "LOG=%LOGDIR%\%STORE%_%STAMP%.log"
set "PS1=%LOGDIR%\_launch_%STAMP%.ps1"

REM ---- write a tiny launcher .ps1: no cmd/powershell quoting hell
> "%PS1%" echo $ErrorActionPreference = 'Stop'
>> "%PS1%" echo $py = '%PY%'
>> "%PS1%" echo $log = '%LOG%'
>> "%PS1%" echo $proj = '%PROJ%'
>> "%PS1%" echo $argList = @('-u', '%ENTRY%', '--store', '%STORE%')
:more
if "%~1"=="" goto run
>> "%PS1%" echo $argList += '%1'
shift
goto more

:run
>> "%PS1%" echo $p = Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $proj -RedirectStandardOutput $log -RedirectStandardError ($log + '.err') -PassThru -WindowStyle Hidden
>> "%PS1%" echo Write-Output ('PID=' + $p.Id)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo [ERROR] Start-Process failed rc=%RC%
  type "%PS1%" >nul 2>&1
  exit /b 91
)

echo [OK] launched store=%STORE%
echo [OK] log: %LOG%
endlocal & exit /b 0
