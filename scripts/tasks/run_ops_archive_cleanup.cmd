@echo off
REM ---- DIRECT NETWORK (2026-11-08) ----
REM Proxy is optional here: this box is on a domestic IP. A stale
REM HTTP(S)_PROXY pointing at a dead 127.0.0.1:7897 made every child
REM fail with WinError 10061. Clear it so children go direct.
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "http_proxy="
set "https_proxy="
set "NO_PROXY=*"
set "no_proxy=*"
REM ============================================================
REM  Archive retention cleanup (box labels + invoice sets, 7 days)
REM
REM  Called by Windows Task Scheduler: Archive-Cleanup-Weekly
REM
REM  This wrapper is intentionally ASCII-only and does nothing but
REM  hand off to archive_cleanup_runner.ps1. The target script lives
REM  under a Chinese-named directory, and cmd.exe parses .cmd files
REM  using the OEM codepage (GBK) -- a non-ASCII path in here breaks
REM  parsing outright (verified 2026-10-10). PowerShell resolves the
REM  path correctly instead.
REM
REM  NOTE: keep this whole file ASCII-only.
REM ============================================================
setlocal

set "RUNNER=D:\AI-workplace\TEMU\scripts\archive_cleanup_runner.ps1"

if not exist "%RUNNER%" (
  echo [%date% %time%] ERROR runner not found: %RUNNER% >> "%TEMP%\archive_cleanup_err.log"
  exit /b 1
)

powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%RUNNER%"
set "RC=%ERRORLEVEL%"
echo [%date% %time%] archive-cleanup end rc=%RC% >> "%TEMP%\archive_cleanup_err.log"

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
REM
REM *** MUST capture RC BEFORE the call *** (fixed 2026-10-29)
REM   notify_panel.cmd ends with `exit /b 0`, so `exit /b %ERRORLEVEL%` placed
REM   AFTER it always returned 0 -- every archive-cleanup failure was silently
REM   reported as success. Measured: call a stub that exits 3, then notify_panel,
REM   then %ERRORLEVEL% => 0.
call "%~dp0notify_panel.cmd" archive-cleanup
endlocal & exit /b %RC%
