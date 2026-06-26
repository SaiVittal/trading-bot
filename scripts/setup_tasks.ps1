# Run once as Administrator to register all trading bot scheduled tasks
#Requires -RunAsAdministrator

$CrossScript  = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_cross_alerts_now.ps1"
$SignalScript = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_signals_now.ps1"
$PS           = "powershell.exe"
$PSArgs       = "-WindowStyle Hidden -ExecutionPolicy Bypass -File"

function Register-TradingTask {
    param($Name, $Script, $Trigger)
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
    $Action   = New-ScheduledTaskAction -Execute $PS -Argument "$PSArgs `"$Script`""
    $Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -StartWhenAvailable -MultipleInstances IgnoreNew
    try {
        Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings -RunLevel Highest -Force -ErrorAction Stop | Out-Null
        Write-Host "OK: $Name" -ForegroundColor Green
    } catch {
        Write-Host "FAILED: $Name - $_" -ForegroundColor Red
    }
}

# Cross Alerts: every 5 min starting 9:30 AM, running for 6.5 hrs (until 4 PM), Mon-Fri
$tCross = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "09:30"
$tCross.Repetition.Interval = "PT5M"
$tCross.Repetition.Duration = "PT6H30M"
Register-TradingTask -Name "TradingBot_CrossAlerts" -Script $CrossScript -Trigger $tCross

# Intraday Signals: every 15 min starting 10:00 AM, running for 5h45m (until 3:45 PM), Mon-Fri
$tSignal = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "10:00"
$tSignal.Repetition.Interval = "PT15M"
$tSignal.Repetition.Duration = "PT5H45M"
Register-TradingTask -Name "TradingBot_IntradaySignals" -Script $SignalScript -Trigger $tSignal

# Pre-market signals: 9:03, 9:18, 9:34 AM Mon-Fri
foreach ($t in @("09:03","09:18","09:34")) {
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $t
    Register-TradingTask -Name "TradingBot_PreMarket_$($t.Replace(':',''))" -Script $SignalScript -Trigger $trigger
}

Write-Host ""
Write-Host "Done. Tasks run automatically every trading day - no Claude needed." -ForegroundColor Cyan
