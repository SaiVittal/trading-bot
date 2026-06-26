#Requires -RunAsAdministrator

$Cross    = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_cross_alerts_now.ps1"
$Signal   = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_signals_now.ps1"
$RepoRoot = "C:\Users\sdlr2\Downloads\trading-bot"
$Python   = "python"
$Run      = "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File"

function Make-Task {
    param($Name, $Action, $StartTime, $Days, $RepeatMins, $DurationHHMM)
    schtasks /delete /tn $Name /f 2>$null
    if ($RepeatMins) {
        schtasks /create /tn $Name /tr $Action /sc weekly /d $Days /st $StartTime /ri $RepeatMins /du $DurationHHMM /rl HIGHEST /f | Out-Null
    } else {
        schtasks /create /tn $Name /tr $Action /sc weekly /d $Days /st $StartTime /rl HIGHEST /f | Out-Null
    }
    if ($LASTEXITCODE -eq 0) { Write-Host "OK: $Name" -ForegroundColor Green }
    else { Write-Host "FAILED: $Name" -ForegroundColor Red }
}

$Days = "MON,TUE,WED,THU,FRI"

# Cross Alerts: every 5 min from 9:30 AM for 6h30m (until 4:00 PM)
Make-Task "TradingBot_CrossAlerts"     "$Run `"$Cross`""  "09:30" $Days 5  "0006:30"

# Intraday Signals: every 15 min from 10:00 AM for 5h45m (until 3:45 PM)
Make-Task "TradingBot_IntradaySignals" "$Run `"$Signal`"" "10:00" $Days 15 "0005:45"

# Pre-market signals: 9:03, 9:18, 9:34 AM
Make-Task "TradingBot_PreMarket_0903"  "$Run `"$Signal`"" "09:03" $Days $null $null
Make-Task "TradingBot_PreMarket_0918"  "$Run `"$Signal`"" "09:18" $Days $null $null
Make-Task "TradingBot_PreMarket_0934"  "$Run `"$Signal`"" "09:34" $Days $null $null

# OD Aggregator: 9:05, 9:20, 9:35 AM (runs Python script)
$OD1 = "cmd /c `"cd /d $RepoRoot && $Python scripts\od_aggregator.py --run 1`""
$OD2 = "cmd /c `"cd /d $RepoRoot && $Python scripts\od_aggregator.py --run 2`""
$OD3 = "cmd /c `"cd /d $RepoRoot && $Python scripts\od_aggregator.py --run 3`""
Make-Task "TradingBot_OD_Run1" $OD1 "09:05" $Days $null $null
Make-Task "TradingBot_OD_Run2" $OD2 "09:20" $Days $null $null
Make-Task "TradingBot_OD_Run3" $OD3 "09:35" $Days $null $null

Write-Host ""
Write-Host "All 8 tasks installed. Runs automatically Mon-Fri. No Claude needed." -ForegroundColor Cyan
Write-Host "Verify: taskschd.msc" -ForegroundColor Gray
