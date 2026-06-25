<#
.SYNOPSIS
    Launches the OD Aggregator Bot for pre-market Open Drive signal aggregation.
    Runs automatically on weekdays; fires consolidated alerts at 9:05, 9:20, 9:35 ET.
    Hard stop at 9:45 ET. Logs to scripts\logs\od_aggregator_YYYY-MM-DD.log.

.USAGE
    # Run manually:
    & 'C:\Users\sdlr2\Downloads\trading-bot\scripts\run_od_aggregator.ps1'

    # Scheduled via Task Scheduler (see setup_od_aggregator_task.ps1)
#>

$ErrorActionPreference = "Stop"

$RepoRoot = "C:\Users\sdlr2\Downloads\trading-bot"
$Script    = Join-Path $RepoRoot "scripts\od_aggregator.py"
$LogDir    = Join-Path $RepoRoot "scripts\logs"
$LogFile   = Join-Path $LogDir ("od_aggregator_" + (Get-Date -Format "yyyy-MM-dd") + ".log")

# Only run on weekdays
$dow = (Get-Date).DayOfWeek
if ($dow -eq "Saturday" -or $dow -eq "Sunday") {
    Write-Host "Weekend — OD Aggregator skipped." -ForegroundColor Yellow
    exit 0
}

# Create log dir if needed
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

Write-Host "Starting OD Aggregator Bot..." -ForegroundColor Cyan
Write-Host "Log: $LogFile" -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop manually." -ForegroundColor Gray

Set-Location $RepoRoot

# Run with output captured to log + console
python $Script 2>&1 | Tee-Object -FilePath $LogFile -Append
