@echo off
REM ============================================================
REM  Iterator called INSIDE the window lock by run_temu_bel_abnormal.cmd
REM  (user order 2026-11-24: run the two stores in one task, BEL then KLS).
REM
REM  Why a separate file: run_temu_bel_abnormal.cmd runs the whole body with
REM  ">> %LOG%", and cmd re-reads a .cmd from a byte offset while running it.
REM  A ":iterate" subroutine inside that same file works in cmd.exe only by
REM  luck; splitting it out removes the "batch becomes a 0-byte file" risk
REM  (measured on the DSH-ImagePatch launcher once).
REM
REM  Behaviour: BEL first, then KinglamanSport (KLS). Each store logs its own
REM  start/end + rc, so the log says exactly who failed. A failing BEL does
REM  NOT block KLS. Exit = first non-zero rc (0 only if both succeeded).
REM
REM  Usage: cmd /c run_temu_abnormal_two.cmd "<log file>"
REM  NOTE : ASCII only (cmd.exe reads this in the OEM codepage).
REM ============================================================
setlocal EnableExtensions EnableDelayedExpansion

set "PROJ=D:\AI-workplace\TEMU"
set "LOG=%~1"
if "%LOG%"=="" set "LOG=%PROJ%\_logs\abnormal-daily\unknown.log"

cd /d "%PROJ%"

set "RCALL=0"
for %%S in (BEL KinglamanSport) do (
  echo ------------------------------------------------------------>> "%LOG%"
  echo [STORE] %%S start %TIME%>> "%LOG%"
  python "%PROJ%\scripts\power\run_temu_abnormal.py" --action pipeline --store %%S --confirm >> "%LOG%" 2>&1
  set "RC_ONE=!ERRORLEVEL!"
  echo [STORE] %%S end %TIME% exit=!RC_ONE!>> "%LOG%"
  if not "!RC_ONE!"=="0" if "!RCALL!"=="0" set "RCALL=!RC_ONE!"
)

set "OUT=!RCALL!"
endlocal & exit /b %OUT%
