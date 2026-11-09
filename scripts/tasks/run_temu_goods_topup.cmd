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
REM TEMU-Goods-DailyList  (ASCII only: cmd reads this file as GBK)
REM Runs right after the 08:00 on-sale refresh and PRINTS THE DAILY LIST.
REM NO WRITE OPERATIONS AT ALL (user decision 2026-10-13).
setlocal
cd /d "D:\AI-workplace\TEMU"
set PYTHONIOENCODING=utf-8
set LOG=D:\AI-workplace\TEMU\_logs\goods-topup\daily.log
if not exist "D:\AI-workplace\TEMU\_logs\goods-topup" mkdir "D:\AI-workplace\TEMU\_logs\goods-topup"
echo. >> "%LOG%"
echo ===== daily list start %DATE% %TIME% ===== >> "%LOG%"
python -u scripts\goods\daily_list.py %* >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo ===== daily list end %DATE% %TIME% rc=%RC% ===== >> "%LOG%"

REM ---- refresh the admin panel now that this task finished (success OR failure) ----
REM Coalesced + lock-guarded; always exits 0 and never blocks. See scripts\notify_panel.cmd
call "%~dp0notify_panel.cmd" goods-topup
exit /b %RC%
