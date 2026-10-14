@echo off
REM ============================================================
REM  notify_panel.cmd -- "task finished, refresh the panel" hook
REM
REM  Called at the END of every timed task (success OR failure):
REM      call "%~dp0notify_panel.cmd" <reason>
REM
REM  Why a separate .cmd instead of inlining:
REM    * one place to change the refresh policy for all tasks;
REM    * keeps each task's exit code intact (this script always
REM      exits 0 and never blocks -- the publish runs detached);
REM    * ASCII-only, so cmd.exe (GBK codepage) parses it safely.
REM
REM  Coalescing: if a publish is already running, this only drops a
REM  marker. That publish re-checks the marker before finishing and
REM  runs one more round if needed. So N tasks finishing in the same
REM  minute cost at most 2 deploys, not N (they would otherwise
REM  deploy concurrently to the same Pages project and overwrite
REM  each other).
REM
REM  NOT called by the panel-refresh task itself (that would loop).
REM ============================================================
setlocal

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY exit /b 0

set "REASON=%~1"
if "%REASON%"=="" set "REASON=unknown"

REM Fire and forget. Do NOT use `call` here: we must not wait, and we
REM must not let the child's exit code become ours.
"%PY%" "D:\AI-workplace\TEMU\scripts\panel_refresh.py" --request --reason "%REASON%" >nul 2>&1

endlocal & exit /b 0
