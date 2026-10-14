# Archive retention cleanup runner (scheduler entry point).
#
# Why this exists as a separate .ps1:
#   The real script lives under a Chinese-named directory. cmd.exe parses
#   .cmd files with the OEM codepage (GBK), so any non-ASCII path inside a
#   .cmd file breaks its parsing entirely (verified 2026-10-10).
#   PowerShell handles UTF-8 paths correctly, so the .cmd only needs to
#   locate THIS file, and everything Chinese lives in here.
#
# The directory name is built from code points on purpose, so this file
# stays pure ASCII and cannot itself be corrupted.

$ErrorActionPreference = 'Continue'

# 'AI-workplace\<ShangHuo-FaPiao>'
$dir = 'D:\AI-workplace\' +
       [char]0x4E0A + [char]0x8D27 + '-' + [char]0x53D1 + [char]0x7968

$script  = Join-Path $dir 'scripts\cleanup_archives.py'
$logdir  = Join-Path $dir '_logs\cleanup'
$log     = Join-Path $logdir 'scheduler.log'

if (-not (Test-Path $logdir)) {
    New-Item -ItemType Directory -Path $logdir -Force | Out-Null
}

function Write-Log($msg) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $log -Value "[$stamp] $msg" -Encoding UTF8
}

if (-not (Test-Path $script)) {
    Write-Log "ERROR script not found: $script"
    exit 1
}

# Locate python (Task Scheduler PATH may not include it).
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) {
    $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
}
if (-not (Test-Path $py)) {
    Write-Log "ERROR python not found"
    exit 1
}

$env:PYTHONIOENCODING = 'utf-8'

Write-Log "cleanup start (py=$py)"
& $py $script --apply 2>&1 | ForEach-Object { Write-Log $_ }
$rc = $LASTEXITCODE
Write-Log "cleanup end rc=$rc"

exit $rc
