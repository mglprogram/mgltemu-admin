@echo off
REM ============================================================
REM  Generic entry: run ONE existing run_*.cmd through a DSH headless
REM  agent session, while keeping a REAL exit code.
REM
REM  Why the indirection: `dsh --profile headless` ALWAYS exits 0, so
REM  handing a task to the agent would paint every failure green in
REM  manage.py health / watch_tasks.py / the panel. scripts\dsh_task.py
REM  re-derives the truth from a sentinel written by scripts\dsh_run.py
REM  (code, not the model) and returns it here.
REM
REM  The INNER .cmd is unchanged: it still does "locate python + cd +
REM  redirect log + notify_panel hook". Nothing about the business
REM  logic moves into the agent.
REM
REM  Usage (from the registry action):
REM    cmd /c "...\scripts\tasks\run_via_dsh.cmd" "TASK-NAME" "scripts\tasks\run_x.cmd" [timeout_min]
REM
REM  Log: _logs\dsh-runner\<TASK-NAME>.log
REM  NOTE: keep this file ASCII-only (cmd.exe reads it as the OEM codepage).
REM ============================================================
setlocal EnableExtensions

set "PROJ=D:\AI-workplace\TEMU"
set "TASK=%~1"
set "INNER=%~2"
set "TMO=%~3"

if not defined TASK (
  echo [run_via_dsh] ERROR missing task name
  exit /b 90
)
REM INNER / TMO are optional: when omitted, dsh_task.py reads `dsh.inner` and
REM derives the timeout from the task's `timeout_minutes` in tasks.json. That
REM keeps the registry action string to ONE plain argument, so it never has to
REM nest three levels of quotes (which cmd.exe mangles).

set "LOGDIR=%PROJ%\_logs\dsh-runner"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\%TASK%.log"

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo [%date% %time%] ERROR python not found >> "%LOG%"
  exit /b 90
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

REM The headless profile cannot resolve ~/.dsh/.credentials.yaml refs by itself.
REM dsh_task.py reads the key from there when it is not already in the env, so
REM nothing is needed here. (Extracting it here would need nested quotes inside
REM a `for /f` backquote, which cmd.exe mangles - measured 2026-10-26.)

REM ---- KILL SWITCH ----
REM If DSH_AGENT_ENABLED=0 (or a file named dsh-off.txt exists next to this
REM script), bypass the agent and run the INNER .cmd directly. This restores
REM the old deterministic behaviour in one move, without touching the registry
REM or any scheduled task. Use it when dsh itself is broken/slow/rate-limited.
if /i "%DSH_AGENT_ENABLED%"=="0" goto :direct
if exist "%~dp0dsh-off.txt" goto :direct

REM workspace-write cannot provision a sandbox on this workspace
REM (SetNamedSecurityInfoW failed Win32 5, measured 2026-10-26).
if not defined DSH_PERMISSION_MODE set "DSH_PERMISSION_MODE=danger-full-access"

cd /d "%PROJ%"
echo. >> "%LOG%"
echo ===== dsh run start %DATE% %TIME% task=%TASK% inner=%INNER% ===== >> "%LOG%"

if defined TMO (
  "%PY%" -u "%PROJ%\scripts\dsh_task.py" --task "%TASK%" --inner "%INNER%" --timeout "%TMO%" >> "%LOG%" 2>&1
) else (
  "%PY%" -u "%PROJ%\scripts\dsh_task.py" --task "%TASK%" --inner "%INNER%" >> "%LOG%" 2>&1
)
set "RC=%ERRORLEVEL%"

REM Keep a SPACE before ">>" (RC=0 would otherwise become a handle redirect).
echo ===== dsh run end %DATE% %TIME% rc=%RC% ===== >> "%LOG%"

REM Do NOT call notify_panel here: the INNER run_*.cmd already does it, and
REM the panel-refresh task itself must never hook itself (self-excitation).
endlocal & exit /b %RC%

:direct
REM Kill switch path: no agent, no sentinel, exit code is the INNER one.
REM If INNER was not passed, resolve it from the registry (same source the
REM agent path uses) so the fallback still works with a bare task name.
REM
REM The lookup goes through _inner_of.py on purpose. Inlining `python -c "..."`
REM inside the for/f backquotes makes the command start with a quote AND hold
REM more than two quotes, so cmd /c strips the outer pair and the whole thing
REM fails with `'...python.exe" -c "import' is not recognized` -> INNER stays
REM empty -> exit /b 90. That bug was live from day one (measured 2026-10-28);
REM the agent path never touches this code, which is why nobody noticed.
cd /d "%PROJ%"
if not defined INNER (
  for /f "usebackq delims=" %%A in (`%PY% "%~dp0_inner_of.py" "%TASK%"`) do set "INNER=%%A"
)
if not defined INNER (
  echo ===== DIRECT FAILED %DATE% %TIME% task=%TASK% : no inner command ===== >> "%LOG%"
  echo [run_via_dsh] cannot resolve inner for %TASK%
  endlocal & exit /b 90
)
echo. >> "%LOG%"
echo ===== DIRECT (agent bypassed) start %DATE% %TIME% task=%TASK% inner=%INNER% ===== >> "%LOG%"
call "%PROJ%\%INNER%"
set "RC=%ERRORLEVEL%"
echo ===== DIRECT (agent bypassed) end %DATE% %TIME% rc=%RC% ===== >> "%LOG%"
endlocal & exit /b %RC%
