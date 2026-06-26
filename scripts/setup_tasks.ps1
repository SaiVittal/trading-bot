# Run once as Administrator to register all trading bot scheduled tasks

$CrossScript   = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_cross_alerts_now.ps1"
$SignalScript  = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_signals_now.ps1"
$PS            = "powershell.exe"
$PSArgs        = "-WindowStyle Hidden -ExecutionPolicy Bypass -File"

function Register-TradingTask {
    param($Name, $Script, $Triggers)
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
    $Action   = New-ScheduledTaskAction -Execute $PS -Argument "$PSArgs `"$Script`""
    $Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Triggers -Settings $Settings -RunLevel Highest -Force | Out-Null
    Write-Host "Registered: $Name" -ForegroundColor Green
}

# ── Cross Alerts: every 5 min, 9:30 AM - 4:00 PM, Mon-Fri ───────────────────
$crossTriggers = @()
$startTime = [datetime]::Today.AddHours(9).AddMinutes(30)
$endTime   = [datetime]::Today.AddHours(16)
while ($startTime -le $endTime) {
    $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $startTime
    $crossTriggers += $t
    $startTime = $startTime.AddMinutes(5)
}
Register-TradingTask -Name "TradingBot_CrossAlerts" -Script $CrossScript -Triggers $crossTriggers

# ── Intraday Signals: every 15 min, 10:00 AM - 3:45 PM, Mon-Fri ─────────────
$signalTriggers = @()
$startTime = [datetime]::Today.AddHours(10)
$endTime   = [datetime]::Today.AddHours(15).AddMinutes(45)
while ($startTime -le $endTime) {
    $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $startTime
    $signalTriggers += $t
    $startTime = $startTime.AddMinutes(15)
}
Register-TradingTask -Name "TradingBot_IntradaySignals" -Script $SignalScript -Triggers $signalTriggers

# ── Pre-market Signals: 9:03, 9:18, 9:34 AM Mon-Fri ─────────────────────────
foreach ($min in @("09:03","09:18","09:34")) {
    $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $min
    Register-TradingTask -Name "TradingBot_PreMarket_$($min.Replace(':',''))" -Script $SignalScript -Triggers $t
}

Write-Host ""
Write-Host "All tasks registered. They will run automatically every trading day." -ForegroundColor Cyan
Write-Host "No need to keep Claude open for these to run." -ForegroundColor Yellow
