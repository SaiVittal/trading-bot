# ============================================================
# EOD Audit Report Generator  v1
# Runs after 4:00 PM ET — collects final prices, evaluates
# all signals fired during the session, generates Excel (CSV)
# and sends the file to Telegram via sendDocument API
# ============================================================

param(
    [string[]]$Tickers  = @("APP","TSLA","NVDA","QQQ","SPY","META","IWM","MSFT","AMZN","AAPL","INTC","NOW","GLD"),
    [string]$ApiKey         = "PKEQAQFOVYKIWW64RCEYJJD7N4",
    [string]$ApiSecret      = "Hxc6xXX3VRc25t6mx1r3HEE9u5WzYVrucWjKVyw7j1u4",
    [string]$AlpacaKey      = "",
    [string]$AlpacaSecret   = "",
    [string]$TgToken        = "8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw",
    [string]$TelegramToken  = "",
    [string]$TgChat         = "-4999357279",
    [string]$ChatId         = ""
)

# Accept alternate param names
if ($AlpacaKey    -ne "") { $ApiKey    = $AlpacaKey }
if ($AlpacaSecret -ne "") { $ApiSecret = $AlpacaSecret }
if ($TelegramToken -ne "") { $TgToken  = $TelegramToken }
if ($ChatId       -ne "") { $TgChat    = $ChatId }

$ErrorActionPreference = "SilentlyContinue"
$hdr     = @{ "APCA-API-KEY-ID" = $ApiKey; "APCA-API-SECRET-KEY" = $ApiSecret }
$baseUrl = "https://data.alpaca.markets"

# ============================================================
#  HELPERS
# ============================================================
function fmt([double]$v) { return [Math]::Round($v,2).ToString("F2") }

function Get-Bars([string]$s,[string]$tf,[string]$start,[int]$lim=100) {
    $url = $baseUrl+"/v2/stocks/"+$s+"/bars?timeframe="+$tf+"&start="+$start+"&limit="+$lim+"&feed=iex&sort=asc"
    try { return @((Invoke-RestMethod -Uri $url -Headers $hdr).bars) } catch { return @() }
}

function Get-LastPrice([string]$s) {
    try { return [Math]::Round([double](Invoke-RestMethod -Uri ($baseUrl+"/v2/stocks/"+$s+"/trades/latest?feed=iex") -Headers $hdr).trade.p,2) } catch { return 0.0 }
}

function Calc-EMA([double[]]$a,[int]$p) {
    if ($a.Count -eq 0) { return 0.0 }
    $k = 2.0/([Math]::Min($p,$a.Count)+1.0); $e = $a[0]
    for ($i=1;$i -lt $a.Count;$i++){$e=$a[$i]*$k+$e*(1.0-$k)}
    return [Math]::Round($e,2)
}

function Calc-RSI([double[]]$c,[int]$p=14) {
    if ($c.Count -lt ($p+1)){return 50.0}
    $ag=0.0;$al=0.0
    for($i=1;$i -le $p;$i++){$d=$c[$i]-$c[$i-1];if($d -gt 0){$ag+=$d}else{$al+=[Math]::Abs($d)}}
    $ag/=$p;$al/=$p
    for($i=($p+1);$i -lt $c.Count;$i++){$d=$c[$i]-$c[$i-1];if($d -gt 0){$ag=($ag*($p-1)+$d)/$p;$al=$al*($p-1)/$p}else{$al=($al*($p-1)+[Math]::Abs($d))/$p;$ag=$ag*($p-1)/$p}}
    if($al -eq 0){return 100.0}
    return [Math]::Round(100.0-100.0/(1.0+$ag/$al),1)
}

function Calc-VWAP([object[]]$bars) {
    $tpv=0.0;$tv=0.0
    foreach($b in $bars){$tp=($b.h+$b.l+$b.c)/3.0;$tpv+=$tp*$b.v;$tv+=$b.v}
    if($tv -eq 0){return 0.0}
    return [Math]::Round($tpv/$tv,2)
}

function Calc-ATR([object[]]$bars,[int]$p=14) {
    if($bars.Count -lt 2){return 1.0}
    $trs=@()
    for($i=1;$i -lt $bars.Count;$i++){$hl=$bars[$i].h-$bars[$i].l;$hpc=[Math]::Abs($bars[$i].h-$bars[$i-1].c);$lpc=[Math]::Abs($bars[$i].l-$bars[$i-1].c);$trs+=[Math]::Max($hl,[Math]::Max($hpc,$lpc))}
    $n=[Math]::Min($p,$trs.Count)
    return [Math]::Round(($trs|Select-Object -Last $n|Measure-Object -Sum).Sum/$n,2)
}

# ============================================================
#  TIME SETUP
# ============================================================
$nowUtc    = [DateTime]::UtcNow.ToUniversalTime()
$todayDate = $nowUtc.ToString("yyyy-MM-dd")
$nowET     = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($nowUtc,"Eastern Standard Time")
$sessionSt = $todayDate+"T13:30:00Z"
$dailyStart= "2026-01-01T00:00:00Z"

Write-Host ("=== EOD Audit Report @ "+$nowET.ToString("HH:mm")+" ET  "+$todayDate+" ===")

# ============================================================
#  COLLECT DATA PER TICKER
# ============================================================
$rows = [System.Collections.Generic.List[object]]::new()

# Pass/Fail thresholds
$thresholds = @{
    "OpenDrive" = 0.005   # 0.5% within 30 min
    "0DTE"      = 0.010   # 1.0%
    "Intraday"  = 0.0075  # 0.75%
    "Swing"     = 0.020   # 2.0%
    "Momentum"  = 0.015   # 1.5%
}

foreach ($sym in $Tickers) {
    Write-Host ("  Processing "+$sym+"...")

    $daily   = @(Get-Bars $sym "1Day" $dailyStart 90)
    if ($daily.Count -lt 10) { continue }

    [double]$prevClose = [Math]::Round([double]$daily[-1].c,2)
    [double]$prevHigh  = [Math]::Round([double]$daily[-1].h,2)
    [double]$prevLow   = [Math]::Round([double]$daily[-1].l,2)
    [double]$avgVol20  = ($daily|Select-Object -Last 20|ForEach-Object{[double]$_.v}|Measure-Object -Average).Average

    # Intraday 5-min bars
    $idays        = @(Get-Bars $sym "5Min" $sessionSt 80)
    [bool]$hasID  = ($idays.Count -ge 2)

    # Current / EOD price
    [double]$eodPrice = Get-LastPrice $sym
    if ($eodPrice -eq 0.0) { $eodPrice = if($hasID){[double]$idays[-1].c}else{$prevClose} }

    # Daily high/low
    [double]$dayHigh = if($hasID){($idays|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum}else{$eodPrice}
    [double]$dayLow  = if($hasID){($idays|ForEach-Object{[double]$_.l}|Measure-Object -Minimum).Minimum}else{$eodPrice}

    # Opening price (first 5-min bar)
    [double]$openPrice = if($hasID){[double]$idays[0].o}else{$prevClose}

    # VWAP
    [double]$vwap = if($hasID){Calc-VWAP $idays}else{$prevClose}

    # Indicators
    [double[]]$dailyC = @($daily|ForEach-Object{[double]$_.c})
    [double[]]$idC    = if($hasID){@($idays|ForEach-Object{[double]$_.c})}else{@()}
    [double[]]$blend  = @($dailyC|Select-Object -Last 30) + $idC
    [double]$rsi      = Calc-RSI $blend 14
    [double]$ema9     = Calc-EMA $blend 9
    [double]$ema20    = Calc-EMA $blend 20
    [double]$atr      = Calc-ATR $idays 14
    if ($atr -lt 0.01) { $atr = Calc-ATR $daily 14 }

    # Gap
    [double]$gapPct = if($prevClose -gt 0){[Math]::Round(($openPrice-$prevClose)/$prevClose*100.0,2)}else{0.0}

    # RVOL
    $minsEl    = ([DateTime]::UtcNow - [DateTime]::Parse($todayDate+"T13:30:00Z").ToUniversalTime()).TotalMinutes
    $fracEl    = [Math]::Max([Math]::Min($minsEl/390.0,1.0),0.001)
    [double]$todayVol = if($hasID){($idays|ForEach-Object{[double]$_.v}|Measure-Object -Sum).Sum}else{0.0}
    [double]$rvol     = if($avgVol20 -gt 0 -and $todayVol -gt 0){[Math]::Round(($todayVol/$fracEl)/$avgVol20,2)}else{0.0}

    # Day change
    [double]$dayChgPct = if($prevClose -gt 0){[Math]::Round(($eodPrice-$prevClose)/$prevClose*100.0,2)}else{0.0}

    # ---- Evaluate each signal type ----
    # Open Drive
    [string]$odDir    = if($gapPct -gt 1.5){"LONG"}elseif($gapPct -lt -1.5){"SHORT"}else{"NEUTRAL"}
    [double]$odTarget = if($odDir -eq "LONG"){$openPrice*(1+$thresholds["OpenDrive"])}
                        elseif($odDir -eq "SHORT"){$openPrice*(1-$thresholds["OpenDrive"])}
                        else{$openPrice}
    [string]$odResult = "N/A"
    if ($odDir -ne "NEUTRAL") {
        if ($odDir -eq "LONG") {
            $odResult = if($dayHigh -ge $odTarget){"PASS"}elseif($eodPrice -gt $openPrice){"PARTIAL"}else{"FAIL"}
        } else {
            $odResult = if($dayLow -le $odTarget){"PASS"}elseif($eodPrice -lt $openPrice){"PARTIAL"}else{"FAIL"}
        }
    }

    # Intraday Bias (based on VWAP position + RSI)
    [string]$intradayDir = if($eodPrice -gt $vwap -and $rsi -gt 52){"LONG"}
                           elseif($eodPrice -lt $vwap -and $rsi -lt 48){"SHORT"}
                           else{"NEUTRAL"}
    [double]$intTarget = if($intradayDir -eq "LONG"){$openPrice*(1+$thresholds["Intraday"])}
                         elseif($intradayDir -eq "SHORT"){$openPrice*(1-$thresholds["Intraday"])}
                         else{$openPrice}
    [string]$intResult = "N/A"
    if ($intradayDir -ne "NEUTRAL") {
        if ($intradayDir -eq "LONG") {
            $intResult = if($dayHigh -ge $intTarget){"PASS"}elseif($eodPrice -gt $openPrice){"PARTIAL"}else{"FAIL"}
        } else {
            $intResult = if($dayLow -le $intTarget){"PASS"}elseif($eodPrice -lt $openPrice){"PARTIAL"}else{"FAIL"}
        }
    }

    # Momentum
    [string]$moDir    = if($ema9 -gt $ema20 -and $rsi -gt 55 -and $eodPrice -gt $vwap){"LONG"}
                        elseif($ema9 -lt $ema20 -and $rsi -lt 45 -and $eodPrice -lt $vwap){"SHORT"}
                        else{"NEUTRAL"}
    [double]$moTarget = if($moDir -eq "LONG"){$openPrice*(1+$thresholds["Momentum"])}
                        elseif($moDir -eq "SHORT"){$openPrice*(1-$thresholds["Momentum"])}
                        else{$openPrice}
    [string]$moResult = "N/A"
    if ($moDir -ne "NEUTRAL") {
        if ($moDir -eq "LONG") {
            $moResult = if($dayHigh -ge $moTarget){"PASS"}elseif($eodPrice -gt $openPrice){"PARTIAL"}else{"FAIL"}
        } else {
            $moResult = if($dayLow -le $moTarget){"PASS"}elseif($eodPrice -lt $openPrice){"PARTIAL"}else{"FAIL"}
        }
    }

    # 0DTE (ATM options based on price vs VWAP + RSI)
    [string]$dteDir    = if($eodPrice -gt $vwap -and $rsi -gt 55){"CALL"}
                         elseif($eodPrice -lt $vwap -and $rsi -lt 45){"PUT"}
                         else{"NEUTRAL"}
    [double]$dteTarget = if($dteDir -eq "CALL"){$openPrice*(1+$thresholds["0DTE"])}
                         elseif($dteDir -eq "PUT"){$openPrice*(1-$thresholds["0DTE"])}
                         else{$openPrice}
    [string]$dteResult = "N/A"
    if ($dteDir -ne "NEUTRAL") {
        if ($dteDir -eq "CALL") {
            $dteResult = if($dayHigh -ge $dteTarget){"PASS"}elseif($eodPrice -gt $openPrice){"PARTIAL"}else{"FAIL"}
        } else {
            $dteResult = if($dayLow -le $dteTarget){"PASS"}elseif($eodPrice -lt $openPrice){"PARTIAL"}else{"FAIL"}
        }
    }

    # Swing (daily trend based)
    [double]$dEMA9  = Calc-EMA $dailyC 9
    [double]$dEMA20 = Calc-EMA $dailyC 20
    [double]$dRSI   = Calc-RSI $dailyC 14
    [string]$swDir  = if($dEMA9 -gt $dEMA20 -and $dRSI -gt 50){"LONG"}
                      elseif($dEMA9 -lt $dEMA20 -and $dRSI -lt 50){"SHORT"}
                      else{"NEUTRAL"}
    [double]$swTarget = if($swDir -eq "LONG"){$openPrice*(1+$thresholds["Swing"])}
                        elseif($swDir -eq "SHORT"){$openPrice*(1-$thresholds["Swing"])}
                        else{$openPrice}
    [string]$swResult = "N/A"
    if ($swDir -ne "NEUTRAL") {
        if ($swDir -eq "LONG") {
            $swResult = if($dayHigh -ge $swTarget){"PASS"}elseif($eodPrice -gt $openPrice){"PARTIAL"}else{"FAIL"}
        } else {
            $swResult = if($dayLow -le $swTarget){"PASS"}elseif($eodPrice -lt $openPrice){"PARTIAL"}else{"FAIL"}
        }
    }

    # Overall score
    $results = @($odResult,$dteResult,$intResult,$swResult,$moResult) | Where-Object {$_ -ne "N/A"}
    $pass    = ($results | Where-Object {$_ -eq "PASS"}).Count
    $fail    = ($results | Where-Object {$_ -eq "FAIL"}).Count
    $partial = ($results | Where-Object {$_ -eq "PARTIAL"}).Count
    $total   = $results.Count
    [string]$overallScore = if($total -gt 0){[string][Math]::Round($pass/$total*100,0)+"%"}else{"N/A"}
    [string]$overallGrade = if($total -eq 0){"N/A"}
                            elseif($pass/$total -ge 0.7){"A"}
                            elseif($pass/$total -ge 0.5){"B"}
                            elseif($pass/$total -ge 0.3){"C"}
                            else{"F"}

    $rows.Add([PSCustomObject]@{
        Date             = $todayDate
        Ticker           = $sym
        PrevClose        = fmt $prevClose
        OpenPrice        = fmt $openPrice
        DayHigh          = fmt $dayHigh
        DayLow           = fmt $dayLow
        EOD_Price        = fmt $eodPrice
        Gap_Pct          = ($gapPct.ToString()+"%")
        DayChange_Pct    = ($dayChgPct.ToString()+"%")
        VWAP             = fmt $vwap
        RSI_14           = $rsi
        EMA9             = fmt $ema9
        EMA20            = fmt $ema20
        ATR              = fmt $atr
        RVOL             = ($rvol.ToString()+"x")
        PrevDayHigh      = fmt $prevHigh
        PrevDayLow       = fmt $prevLow
        OD_Direction     = $odDir
        OD_Target        = fmt $odTarget
        OD_Result        = $odResult
        DTE_Direction    = $dteDir
        DTE_Target       = fmt $dteTarget
        DTE_Result       = $dteResult
        Intraday_Dir     = $intradayDir
        Intraday_Target  = fmt $intTarget
        Intraday_Result  = $intResult
        Swing_Direction  = $swDir
        Swing_Target     = fmt $swTarget
        Swing_Result     = $swResult
        Momentum_Dir     = $moDir
        Momentum_Target  = fmt $moTarget
        Momentum_Result  = $moResult
        Total_Signals    = $total
        Pass_Count       = $pass
        Fail_Count       = $fail
        Partial_Count    = $partial
        Overall_Score    = $overallScore
        Overall_Grade    = $overallGrade
    })
}

# ============================================================
#  SUMMARY STATS
# ============================================================
$allOD   = @($rows | Where-Object {$_.OD_Result -ne "N/A"})
$allDTE  = @($rows | Where-Object {$_.DTE_Result -ne "N/A"})
$allInt  = @($rows | Where-Object {$_.Intraday_Result -ne "N/A"})
$allSW   = @($rows | Where-Object {$_.Swing_Result -ne "N/A"})
$allMO   = @($rows | Where-Object {$_.Momentum_Result -ne "N/A"})

function Get-PassPct($arr, $field) {
    if ($arr.Count -eq 0) { return "N/A" }
    $p = ($arr | Where-Object {$_.$field -eq "PASS"}).Count
    return [string][Math]::Round($p/$arr.Count*100,0)+"%"
}

# ============================================================
#  BUILD SUMMARY SHEET
# ============================================================
$summaryRows = @(
    [PSCustomObject]@{ Category="Signal Type"; Total_Alerts=$null; Pass=$null; Fail=$null; Partial=$null; Pass_Pct=$null; Fail_Pct=$null },
    [PSCustomObject]@{ Category="Open Drive";  Total_Alerts=$allOD.Count;
        Pass=($allOD|Where-Object{$_.OD_Result -eq "PASS"}).Count;
        Fail=($allOD|Where-Object{$_.OD_Result -eq "FAIL"}).Count;
        Partial=($allOD|Where-Object{$_.OD_Result -eq "PARTIAL"}).Count;
        Pass_Pct=(Get-PassPct $allOD "OD_Result");
        Fail_Pct=([string][Math]::Round(($allOD|Where-Object{$_.OD_Result -eq "FAIL"}).Count/$allOD.Count*100,0)+"%" )},
    [PSCustomObject]@{ Category="0DTE";        Total_Alerts=$allDTE.Count;
        Pass=($allDTE|Where-Object{$_.DTE_Result -eq "PASS"}).Count;
        Fail=($allDTE|Where-Object{$_.DTE_Result -eq "FAIL"}).Count;
        Partial=($allDTE|Where-Object{$_.DTE_Result -eq "PARTIAL"}).Count;
        Pass_Pct=(Get-PassPct $allDTE "DTE_Result");
        Fail_Pct=([string][Math]::Round(($allDTE|Where-Object{$_.DTE_Result -eq "FAIL"}).Count/$allDTE.Count*100,0)+"%")},
    [PSCustomObject]@{ Category="Intraday";    Total_Alerts=$allInt.Count;
        Pass=($allInt|Where-Object{$_.Intraday_Result -eq "PASS"}).Count;
        Fail=($allInt|Where-Object{$_.Intraday_Result -eq "FAIL"}).Count;
        Partial=($allInt|Where-Object{$_.Intraday_Result -eq "PARTIAL"}).Count;
        Pass_Pct=(Get-PassPct $allInt "Intraday_Result");
        Fail_Pct=([string][Math]::Round(($allInt|Where-Object{$_.Intraday_Result -eq "FAIL"}).Count/$allInt.Count*100,0)+"%")},
    [PSCustomObject]@{ Category="Swing";       Total_Alerts=$allSW.Count;
        Pass=($allSW|Where-Object{$_.Swing_Result -eq "PASS"}).Count;
        Fail=($allSW|Where-Object{$_.Swing_Result -eq "FAIL"}).Count;
        Partial=($allSW|Where-Object{$_.Swing_Result -eq "PARTIAL"}).Count;
        Pass_Pct=(Get-PassPct $allSW "Swing_Result");
        Fail_Pct=([string][Math]::Round(($allSW|Where-Object{$_.Swing_Result -eq "FAIL"}).Count/$allSW.Count*100,0)+"%")},
    [PSCustomObject]@{ Category="Momentum";    Total_Alerts=$allMO.Count;
        Pass=($allMO|Where-Object{$_.Momentum_Result -eq "PASS"}).Count;
        Fail=($allMO|Where-Object{$_.Momentum_Result -eq "FAIL"}).Count;
        Partial=($allMO|Where-Object{$_.Momentum_Result -eq "PARTIAL"}).Count;
        Pass_Pct=(Get-PassPct $allMO "Momentum_Result");
        Fail_Pct=([string][Math]::Round(($allMO|Where-Object{$_.Momentum_Result -eq "FAIL"}).Count/$allMO.Count*100,0)+"%")}
)

# ============================================================
#  EXPORT TO CSV (Excel-compatible)
# ============================================================
$outDir  = "C:\Users\sdlr2\Downloads\trading-bot\reports"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

$fileName    = "EOD_Audit_"+$todayDate+".csv"
$filePath    = $outDir+"\"+$fileName
$summaryPath = $outDir+"\EOD_Summary_"+$todayDate+".csv"

# Write summary sheet
$summaryRows | Export-Csv -Path $summaryPath -NoTypeInformation -Encoding UTF8

# Write detailed sheet
$rows | Export-Csv -Path $filePath -NoTypeInformation -Encoding UTF8

# Combine into one file with section headers
$combined = $outDir+"\EOD_FULL_Report_"+$todayDate+".csv"
$header1 = "EOD AUDIT REPORT - "+$todayDate+" - Generated by TradingBot v8"
$header2 = ""
$header3 = "=== SECTION 1: SUMMARY BY SIGNAL TYPE ==="
$section2 = "=== SECTION 2: DETAILED PER-TICKER BREAKDOWN ==="

Set-Content -Path $combined -Value $header1 -Encoding UTF8
Add-Content -Path $combined -Value $header2 -Encoding UTF8
Add-Content -Path $combined -Value $header3 -Encoding UTF8
Get-Content $summaryPath | Add-Content -Path $combined -Encoding UTF8
Add-Content -Path $combined -Value $header2 -Encoding UTF8
Add-Content -Path $combined -Value $section2 -Encoding UTF8
Get-Content $filePath | Add-Content -Path $combined -Encoding UTF8

Write-Host ("CSV report saved: "+$combined)

# ============================================================
#  SEND FILE TO TELEGRAM via sendDocument (WebClient method)
# ============================================================
$totalPass = ($rows|ForEach-Object{[int]$_.Pass_Count}|Measure-Object -Sum).Sum
$totalAll  = ($rows|ForEach-Object{[int]$_.Total_Signals}|Measure-Object -Sum).Sum
$overallPct = if($totalAll -gt 0){[Math]::Round($totalPass/$totalAll*100,0)}else{0}
$bestTicker = ($rows|Sort-Object {[int]($_.Overall_Score -replace '%','' -replace 'N/A','0')} -Descending|Select-Object -First 1).Ticker
$caption    = "EOD Audit Report - "+$todayDate+
              " | Pass: "+$overallPct+"%" +
              " | Best: "+$bestTicker+
              " | "+$nowET.ToString("HH:mm")+" ET"

$tgUrl = "https://api.telegram.org/bot"+$TgToken+"/sendDocument"

# Use WebClient for reliable multipart file upload
$wc = New-Object System.Net.WebClient
$wc.Headers.Add("Content-Type","multipart/form-data; boundary=AuditBoundary123")

$CRLF = "`r`n"
$enc  = [System.Text.Encoding]::UTF8
$fileBytes = [System.IO.File]::ReadAllBytes($combined)

$pre = $enc.GetBytes(
    "--AuditBoundary123"+$CRLF+
    'Content-Disposition: form-data; name="chat_id"'+$CRLF+$CRLF+
    $TgChat+$CRLF+
    "--AuditBoundary123"+$CRLF+
    'Content-Disposition: form-data; name="caption"'+$CRLF+$CRLF+
    $caption+$CRLF+
    "--AuditBoundary123"+$CRLF+
    'Content-Disposition: form-data; name="document"; filename="EOD_Audit_'+$todayDate+'.csv"'+$CRLF+
    "Content-Type: text/csv"+$CRLF+$CRLF
)
$post = $enc.GetBytes($CRLF+"--AuditBoundary123--"+$CRLF)
$payload = $pre + $fileBytes + $post

try {
    $respBytes = $wc.UploadData($tgUrl, "POST", $payload)
    $respStr   = [System.Text.Encoding]::UTF8.GetString($respBytes)
    Write-Host ("TG FILE SENT OK: "+$fileName)
    Write-Host ("Response: "+$respStr.Substring(0,[Math]::Min(120,$respStr.Length)))
} catch {
    Write-Host ("TG FILE UPLOAD ERROR: "+$_.Exception.Message)
    # Fallback: send text summary to Telegram
    $summary  = "EOD AUDIT REPORT - "+$todayDate+"`n"
    $summary += "Overall Pass Rate: "+$overallPct+"%`n"
    $summary += "Best Ticker: "+$bestTicker+"`n"
    $summary += "File saved: "+$fileName+"`n"
    $summary += ($rows | ForEach-Object { $_.Ticker+" "+$_.Gap_Pct+" EOD:"+$_.EOD_Price+" OD:"+$_.OD_Result+" MO:"+$_.Momentum_Result+" Score:"+$_.Overall_Score }) -join "`n"
    $esc = $summary -replace '\\','\\\\' -replace '"','\"' -replace "`r",'' -replace "`n",'\n'
    $fb  = '{"chat_id":"'+$TgChat+'","text":"'+$esc+'"}'
    $fbBytes = [System.Text.Encoding]::UTF8.GetBytes($fb)
    Invoke-RestMethod -Uri ("https://api.telegram.org/bot"+$TgToken+"/sendMessage") -Method POST -Body $fbBytes -ContentType "application/json; charset=utf-8" | Out-Null
    Write-Host "TG: Fallback text summary sent"
}

Write-Host ("`n=== EOD Audit Complete. "+$rows.Count+" tickers. Pass rate: "+$overallPct+"% ===")
$rows | Format-Table Ticker,OpenPrice,EOD_Price,Gap_Pct,DayChange_Pct,OD_Result,DTE_Result,Intraday_Result,Momentum_Result,Overall_Score -AutoSize
