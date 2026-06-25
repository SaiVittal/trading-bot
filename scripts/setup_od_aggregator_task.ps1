<#
.SYNOPSIS
    Registers a Windows Task Scheduler task that launches the OD Aggregator Bot
    automatically every weekday at 8:55 AM ET (5 min before the first alert at 9:05).

.USAGE
    Run ONCE as Administrator:
    powershell -ExecutionPolicy Bypass -File setup_od_aggregator_task.ps1
#>

$TaskName   = "TradingBot_OD_Aggregator"
$ScriptPath = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_od_aggregator.ps1"
$StartTime  = "08:55"   # 8:55 AM local time (ET)

# Remove existing task if present
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`""

# Trigger: Mon-Fri at 8:55 AM
$Trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $StartTime

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Pre-market OD Aggregator Bot — fires at 9:05/9:20/9:35 ET, stops at 9:45 ET" `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host ""
Write-Host "✅ Task registered: $TaskName" -ForegroundColor Green
Write-Host "   Runs: Mon–Fri at $StartTime (local / ET)" -ForegroundColor Cyan
Write-Host "   Script: $ScriptPath" -ForegroundColor Gray
Write-Host ""
Write-Host "To run now for testing:" -ForegroundColor Yellow
Write-Host "   Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Yellow
Write-Host ""
Write-Host "To remove:" -ForegroundColor Yellow
Write-Host "   Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor Yellow
