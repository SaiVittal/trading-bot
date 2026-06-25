$TaskName   = "TradingBot_OD_Aggregator"
$ScriptPath = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_od_aggregator.ps1"
$StartTime  = "08:55"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`""

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
    -Description "Pre-market OD Aggregator Bot - fires at 9:05/9:20/9:35 ET, stops at 9:45 ET" `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host ""
Write-Host "Task registered: $TaskName" -ForegroundColor Green
Write-Host "Runs: Mon-Fri at $StartTime ET" -ForegroundColor Cyan
Write-Host "Script: $ScriptPath" -ForegroundColor Gray
Write-Host ""
Write-Host "To test now: Start-ScheduledTask -TaskName $TaskName" -ForegroundColor Yellow
