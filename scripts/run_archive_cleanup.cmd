@echo off
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
endlocal & exit /b %ERRORLEVEL%
