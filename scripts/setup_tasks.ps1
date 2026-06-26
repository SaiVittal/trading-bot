#Requires -RunAsAdministrator

$Cross  = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_cross_alerts_now.ps1"
$Signal = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_signals_now.ps1"
$Run    = "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File"

function Make-Task {
    param($Name, $Script, $StartTime, $Days, $RepeatMins, $DurationHHMM)
    schtasks /delete /tn $Name /f 2>$null
    if ($RepeatMins) {
        schtasks /create /tn $Name /tr "$Run `"$Script`"" /sc weekly /d $Days /st $StartTime /ri $RepeatMins /du $DurationHHMM /rl HIGHEST /f | Out-Null
    } else {
        schtasks /create /tn $Name /tr "$Run `"$Script`"" /sc weekly /d $Days /st $StartTime /rl HIGHEST /f | Out-Null
    }
    if ($LASTEXITCODE -eq 0) { Write-Host "OK: $Name" -ForegroundColor Green }
    else { Write-Host "FAILED: $Name" -ForegroundColor Red }
}

$Days = "MON,TUE,WED,THU,FRI"

# Cross Alerts: every 5 min from 9:30 AM for 6h30m (until 4:00 PM)
Make-Task "TradingBot_CrossAlerts"     $Cross  "09:30" $Days 5  "0006:30"

# Intraday Signals: every 15 min from 10:00 AM for 5h45m (until 3:45 PM)
Make-Task "TradingBot_IntradaySignals" $Signal "10:00" $Days 15 "0005:45"

# Pre-market signals: 9:03, 9:18, 9:34 AM (no repeat)
Make-Task "TradingBot_PreMarket_0903"  $Signal "09:03" $Days $null $null
Make-Task "TradingBot_PreMarket_0918"  $Signal "09:18" $Days $null $null
Make-Task "TradingBot_PreMarket_0934"  $Signal "09:34" $Days $null $null

Write-Host ""
Write-Host "All tasks installed. Runs automatically Mon-Fri. No Claude needed." -ForegroundColor Cyan
Write-Host ""
Write-Host "Verify in Task Scheduler: taskschd.msc" -ForegroundColor Gray
