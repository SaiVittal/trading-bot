#Requires -RunAsAdministrator

$Cross    = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_cross_alerts_now.ps1"
$Signal   = "C:\Users\sdlr2\Downloads\trading-bot\scripts\run_signals_now.ps1"
$RepoRoot = "C:\Users\sdlr2\Downloads\trading-bot"
$Run      = "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File"

function Remove-BatteryBlock {
    param($Name)
    try {
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $task = $svc.GetFolder("\").GetTask($Name)
        $def  = $task.Definition
        $def.Settings.DisallowStartIfOnBatteries = $false
        $def.Settings.StopIfGoingOnBatteries     = $false
        $svc.GetFolder("\").RegisterTaskDefinition($Name, $def, 4, $null, $null, 3) | Out-Null
    } catch { Write-Host "  Battery fix failed for $Name`: $_" -ForegroundColor Yellow }
}

function Make-Task {
    param($Name, $Action, $StartTime, $Days, $RepeatMins, $DurationHHMM)
    schtasks /delete /tn $Name /f 2>$null
    if ($RepeatMins) {
        schtasks /create /tn $Name /tr $Action /sc weekly /d $Days /st $StartTime /ri $RepeatMins /du $DurationHHMM /rl HIGHEST /f | Out-Null
    } else {
        schtasks /create /tn $Name /tr $Action /sc weekly /d $Days /st $StartTime /rl HIGHEST /f | Out-Null
    }
    if ($LASTEXITCODE -eq 0) {
        Remove-BatteryBlock $Name
        Write-Host "OK: $Name" -ForegroundColor Green
    } else { Write-Host "FAILED: $Name" -ForegroundColor Red }
}

function Make-MinuteTask {
    param($Name, $Action, $StartTime, $EndTime, $IntervalMins)
    schtasks /delete /tn $Name /f 2>$null
    if ($EndTime) {
        schtasks /create /tn $Name /tr $Action /sc minute /mo $IntervalMins /st $StartTime /et $EndTime /k /rl HIGHEST /f | Out-Null
    } else {
        schtasks /create /tn $Name /tr $Action /sc minute /mo $IntervalMins /st $StartTime /rl HIGHEST /f | Out-Null
    }
    if ($LASTEXITCODE -eq 0) {
        # Remove battery restrictions via COM so task fires on battery too
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $task = $svc.GetFolder("\").GetTask($Name)
        $def  = $task.Definition
        $def.Settings.DisallowStartIfOnBatteries = $false
        $def.Settings.StopIfGoingOnBatteries     = $false
        $svc.GetFolder("\").RegisterTaskDefinition($Name, $def, 4, $null, $null, 3) | Out-Null
        Write-Host "OK: $Name (battery restriction removed)" -ForegroundColor Green
    } else { Write-Host "FAILED: $Name" -ForegroundColor Red }
}

$Days = "MON,TUE,WED,THU,FRI"

# Cross Alerts: every 5 min, no end time (script exits if outside 9:30-16:00)
Make-MinuteTask "TradingBot_CrossAlerts"     "$Run `"$Cross`""  "09:30" $null 5

# Intraday Signals: every 15 min, no end time (script exits if outside 10:00-15:45)
Make-MinuteTask "TradingBot_IntradaySignals" "$Run `"$Signal`"" "10:00" $null 15

# Pre-market signals: 9:03, 9:18, 9:34 AM
Make-Task "TradingBot_PreMarket_0903"  "$Run `"$Signal`"" "09:03" $Days $null $null
Make-Task "TradingBot_PreMarket_0918"  "$Run `"$Signal`"" "09:18" $Days $null $null
Make-Task "TradingBot_PreMarket_0934"  "$Run `"$Signal`"" "09:34" $Days $null $null

# OD Aggregator: 9:05, 9:20, 9:35 AM
$OD1 = "$Run `"$RepoRoot\scripts\run_od_run1.ps1`""
$OD2 = "$Run `"$RepoRoot\scripts\run_od_run2.ps1`""
$OD3 = "$Run `"$RepoRoot\scripts\run_od_run3.ps1`""
Make-Task "TradingBot_OD_Run1" $OD1 "09:05" $Days $null $null
Make-Task "TradingBot_OD_Run2" $OD2 "09:20" $Days $null $null
Make-Task "TradingBot_OD_Run3" $OD3 "09:35" $Days $null $null

Write-Host ""
Write-Host "All 8 tasks installed. Runs automatically Mon-Fri. No Claude needed." -ForegroundColor Cyan
Write-Host "Verify: taskschd.msc" -ForegroundColor Gray
