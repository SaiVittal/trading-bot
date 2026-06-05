# ============================================================
# Golden Cross / Death Cross Alert Runner  v1
# S24 — Golden Cross : 9 EMA crosses ABOVE 21 EMA on 5-min
# S25 — Death  Cross : 9 EMA crosses BELOW 21 EMA on 5-min
#
# Runs every 5 minutes during market hours (9:30 AM - 4:00 PM ET)
# Fires IMMEDIATELY when a cross is detected
# Sends a SEPARATE dedicated Telegram alert per cross event
# ============================================================

param(
    [string[]]$Tickers = @("APP","TSLA","NVDA","INTC","AAPL","QQQ","SPY","META","IWM","MSFT"),
    [string]$ApiKey    = "PKUVZN3EUNDEDFIIWS3NHWMKXK",
    [string]$ApiSecret = "2otPyywguF8Xn2mgCwgLifEbP9s8RrbLF9mjVn95EjXo",
    [string]$TgToken   = "8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw",
    [string]$TgChat    = "-5177803835"
)

$ErrorActionPreference = "SilentlyContinue"
$hdr     = @{ "APCA-API-KEY-ID" = $ApiKey; "APCA-API-SECRET-KEY" = $ApiSecret }
$baseUrl = "https://data.alpaca.markets"

# ============================================================
#  MARKET HOURS GATE — 9:30 AM to 4:00 PM ET, Mon-Fri only
# ============================================================
$nowUtc      = [DateTime]::UtcNow.ToUniversalTime()
$todayDate   = $nowUtc.ToString("yyyy-MM-dd")
$nowET       = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($nowUtc,"Eastern Standard Time")
[int]$etMins = $nowET.Hour * 60 + $nowET.Minute
[int]$dow    = [int]$nowET.DayOfWeek

if ($dow -lt 1 -or $dow -gt 5 -or $etMins -lt 570 -or $etMins -gt 960) {
    Write-Host ("=== CROSS SCAN SKIPPED: "+$nowET.ToString("HH:mm")+" ET / "+$nowET.DayOfWeek+" ===")
    exit 0
}

$sessionSt = $todayDate + "T13:30:00Z"
$expDate   = $todayDate.Substring(5,2)+"/"+$todayDate.Substring(8,2)
$EQ        = "=" * 48
$DV        = "-" * 46

Write-Host ("=== Cross Alert Scan @ "+$nowET.ToString("HH:mm")+" ET  "+$todayDate+" ===")

# ============================================================
#  HELPER FUNCTIONS
# ============================================================
function fmt([double]$v) { return "$" + [Math]::Round($v,2).ToString("F2") }

function Get-5MinBars([string]$s) {
    $url = $baseUrl+"/v2/stocks/"+$s+"/bars?timeframe=5Min&start="+$sessionSt+"&limit=100&feed=iex&sort=asc"
    try { return @((Invoke-RestMethod -Uri $url -Headers $hdr).bars) } catch { return @() }
}

function Get-LastPrice([string]$s) {
    try { return [Math]::Round([double](Invoke-RestMethod -Uri ($baseUrl+"/v2/stocks/"+$s+"/trades/latest?feed=iex") -Headers $hdr).trade.p,2) } catch { return 0.0 }
}

function Calc-EMA-Series([double[]]$a,[int]$p) {
    $result = [System.Collections.Generic.List[double]]::new()
    if ($a.Count -eq 0) { return $result }
    $k = 2.0 / ([Math]::Min($p,$a.Count) + 1.0)
    $e = $a[0]
    $result.Add([Math]::Round($e,2))
    for ($i=1; $i -lt $a.Count; $i++) {
        $e = $a[$i]*$k + $e*(1.0-$k)
        $result.Add([Math]::Round($e,2))
    }
    return $result
}

function Calc-ATR([object[]]$bars,[int]$p=14) {
    if ($bars.Count -lt 2) { return 1.0 }
    $trs = @()
    for ($i=1; $i -lt $bars.Count; $i++) {
        $hl  = $bars[$i].h - $bars[$i].l
        $hpc = [Math]::Abs($bars[$i].h - $bars[$i-1].c)
        $lpc = [Math]::Abs($bars[$i].l - $bars[$i-1].c)
        $trs += [Math]::Max($hl,[Math]::Max($hpc,$lpc))
    }
    $n = [Math]::Min($p,$trs.Count)
    return [Math]::Round(($trs|Select-Object -Last $n|Measure-Object -Sum).Sum/$n,2)
}

function Calc-VWAP([object[]]$bars) {
    $tpv=0.0; $tv=0.0
    foreach ($b in $bars) { $tp=($b.h+$b.l+$b.c)/3.0; $tpv+=$tp*$b.v; $tv+=$b.v }
    if ($tv -eq 0) { return 0.0 }
    return [Math]::Round($tpv/$tv,2)
}

function Calc-RVOL([object[]]$bars,[double]$avgVol20) {
    $minsEl = ([DateTime]::UtcNow - [DateTime]::Parse($todayDate+"T13:30:00Z").ToUniversalTime()).TotalMinutes
    $frac   = [Math]::Max([Math]::Min($minsEl/390.0,1.0),0.001)
    $todVol = ($bars|ForEach-Object{[double]$_.v}|Measure-Object -Sum).Sum
    if ($avgVol20 -gt 0 -and $todVol -gt 0) { return [Math]::Round(($todVol/$frac)/$avgVol20,2) }
    return 0.0
}

function Build-TgBody([string]$chatId,[string]$text) {
    $esc = $text -replace '\\','\\\\' -replace '"','\"' -replace "`r",'' -replace "`n",'\n' -replace "`t",'\t'
    return '{"chat_id":"'+$chatId+'","text":"'+$esc+'"}'
}

function Send-TG([string]$msg) {
    $maxChunk = 3800
    $lines    = $msg -split "`n"
    $chunks   = [System.Collections.Generic.List[string]]::new()
    $cur      = ""
    foreach ($line in $lines) {
        $cand = if ($cur -eq "") { $line } else { $cur+"`n"+$line }
        if ($cand.Length -gt $maxChunk) { if ($cur.Length -gt 0){$chunks.Add($cur)}; $cur=$line } else { $cur=$cand }
    }
    if ($cur.Length -gt 0) { $chunks.Add($cur) }
    $url   = "https://api.telegram.org/bot"+$TgToken+"/sendMessage"
    $total = $chunks.Count
    for ($i=0; $i -lt $total; $i++) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes((Build-TgBody $TgChat $chunks[$i]))
        try {
            Invoke-RestMethod -Uri $url -Method POST -Body $bytes -ContentType "application/json; charset=utf-8" | Out-Null
            if ($total -gt 1) { Write-Host ("  TG part "+($i+1)+"/"+$total+" sent") }
        } catch { Write-Host ("TG-ERR: "+$_.Exception.Message) }
        Start-Sleep -Milliseconds 700
    }
}

# ============================================================
#  GET 20-DAY AVG VOLUME (for RVOL)
# ============================================================
$dailyStart = "2026-01-01T00:00:00Z"

function Get-AvgVol20([string]$s) {
    $url = $baseUrl+"/v2/stocks/"+$s+"/bars?timeframe=1Day&start="+$dailyStart+"&limit=30&feed=iex&sort=asc"
    try {
        $d = @((Invoke-RestMethod -Uri $url -Headers $hdr).bars)
        if ($d.Count -gt 0) { return ($d|Select-Object -Last 20|ForEach-Object{[double]$_.v}|Measure-Object -Average).Average }
    } catch {}
    return 0.0
}

# ============================================================
#  CROSS DETECTION LOOP
# ============================================================
$crossesDetected = 0

foreach ($sym in $Tickers) {
    Write-Host ("`n["+$sym+"] scanning...")

    $bars5m = @(Get-5MinBars $sym)
    if ($bars5m.Count -lt 2) {
        Write-Host ("  Insufficient bars ("+$bars5m.Count+") -- need 2+ 5-min bars, skip")
        continue
    }

    # Build EMA series on 5-min closes
    [double[]]$closes5m = @($bars5m | ForEach-Object { [double]$_.c })
    $ema9Series  = @(Calc-EMA-Series $closes5m 9)
    $ema21Series = @(Calc-EMA-Series $closes5m 21)

    if ($ema9Series.Count -lt 2 -or $ema21Series.Count -lt 2) { continue }

    [int]$last  = $ema9Series.Count - 1
    [int]$prev  = $last - 1

    [double]$ema9Curr  = $ema9Series[$last]
    [double]$ema9Prev  = $ema9Series[$prev]
    [double]$ema21Curr = $ema21Series[$last]
    [double]$ema21Prev = $ema21Series[$prev]

    # Current market data
    [double]$curP  = Get-LastPrice $sym
    if ($curP -eq 0.0) { $curP = $closes5m[-1] }
    [double]$vwap  = Calc-VWAP $bars5m
    [double]$atr   = Calc-ATR  $bars5m 14
    if ($atr -lt 0.01) { $atr = $curP * 0.01 }
    [double]$avg20 = Get-AvgVol20 $sym
    [double]$rvol  = Calc-RVOL $bars5m $avg20
    [bool]$abvVwap = ($curP -gt $vwap -and $vwap -gt 0)

    # Cross detection
    [bool]$goldenCross = ($ema9Prev -lt $ema21Prev) -and ($ema9Curr -gt $ema21Curr)
    [bool]$deathCross  = ($ema9Prev -gt $ema21Prev) -and ($ema9Curr -lt $ema21Curr)
    [bool]$atEMA9      = ([Math]::Abs($curP - $ema9Curr) -lt ($atr * 0.3))

    Write-Host ("  EMA9: prev="+$ema9Prev+" curr="+$ema9Curr+"  EMA21: prev="+$ema21Prev+" curr="+$ema21Curr)
    Write-Host ("  GC="+$goldenCross+" DC="+$deathCross+"  Price="+$curP+"  VWAP="+$vwap+"  RVOL="+$rvol+"x")

    # ---- S24: GOLDEN CROSS ----
    if ($goldenCross -and $curP -gt $ema9Curr -and $curP -gt $ema21Curr -and $abvVwap -and $rvol -gt 1.5) {
        $crossesDetected++
        [int]$gcConf = 82
        if ($rvol -gt 2.5){$gcConf+=5}; if ($rvol -gt 4.0){$gcConf+=4}
        if ($atEMA9)      {$gcConf+=6}
        $gcConf = [Math]::Min($gcConf,97)

        [string]$gcEntry  = fmt $curP
        [string]$gcStop   = fmt ([Math]::Round($curP - $atr*0.5,2))
        [string]$gcT1     = fmt ([Math]::Round($curP + $atr*1.0,2))
        [string]$gcT2     = fmt ([Math]::Round($curP + $atr*2.0,2))
        [string]$gcT3     = fmt ([Math]::Round($curP + $atr*3.0,2))
        [string]$gcStrike = fmt ([Math]::Ceiling($curP/5.0)*5.0)
        [string]$pullNote = if ($atEMA9) {"PULLBACK ENTRY NOW -- price at 9 EMA "+$ema9Curr} else {"Wait for pullback to 9 EMA="+$ema9Curr}

        $gcMsg  = $EQ+"`n"
        $gcMsg += "[S24] GOLDEN CROSS -- "+$sym+"`n"
        $gcMsg += $EQ+"`n"
        $gcMsg += "9 EMA crossed ABOVE 21 EMA (5-min chart)`n"
        $gcMsg += $DV+"`n"
        $gcMsg += "PREV BAR    : EMA9="+$ema9Prev+" was BELOW EMA21="+$ema21Prev+"`n"
        $gcMsg += "CURR BAR    : EMA9="+$ema9Curr+" now ABOVE EMA21="+$ema21Curr+"`n"
        $gcMsg += $DV+"`n"
        $gcMsg += "PRICE       : "+(fmt $curP)+"`n"
        $gcMsg += "VWAP        : "+(fmt $vwap)+"  (+"+[Math]::Round($curP-$vwap,2)+" above -- CONFIRMED)`n"
        $gcMsg += "EMA9        : "+$ema9Curr+"  EMA21: "+$ema21Curr+"`n"
        $gcMsg += "RVOL        : "+$rvol+"x   ATR: "+$atr+"`n"
        $gcMsg += "CONFIDENCE  : "+$gcConf+"%`n"
        $gcMsg += $DV+"`n"
        $gcMsg += "ENTRY SETUP : "+$pullNote+"`n"
        $gcMsg += $DV+"`n"
        $gcMsg += "OPTION      : [UP] CALL "+$gcStrike+" (0DTE exp "+$expDate+")`n"
        $gcMsg += "Entry(stk)  : "+$gcEntry+"`n"
        $gcMsg += "Exit T1     : "+$gcT1+"  (1x ATR -- TAKE PROFIT)`n"
        $gcMsg += "Exit T2     : "+$gcT2+"  (2x ATR -- EXTENDED)`n"
        $gcMsg += "Exit T3     : "+$gcT3+"  (3x ATR -- MAX TARGET)`n"
        $gcMsg += "Stop(stk)   : "+$gcStop+"  (0.5x ATR -- CUT LOSS)`n"
        $gcMsg += $DV+"`n"
        $gcMsg += "RULES:`n"
        $gcMsg += "  1. Wait for 5-min candle CLOSE above both EMA9+EMA21`n"
        $gcMsg += "  2. Price must hold above VWAP "+(fmt $vwap)+"`n"
        $gcMsg += "  3. Enter on first pullback to EMA9="+$ema9Curr+"`n"
        $gcMsg += "  4. Trail stop to EMA9 once T1 hit`n"
        $gcMsg += $EQ+"`n"
        $gcMsg += "v1-GC | "+$nowET.ToString("HH:mm")+" ET | "+$sym

        Send-TG $gcMsg
        Write-Host ("  >>> S24 GOLDEN CROSS FIRED: "+$sym+" @ "+$curP+" | Conf="+$gcConf+"% | RVOL="+$rvol+"x")
    }

    # ---- S25: DEATH CROSS ----
    elseif ($deathCross -and $curP -lt $ema9Curr -and $curP -lt $ema21Curr -and (-not $abvVwap) -and $rvol -gt 1.5) {
        $crossesDetected++
        [int]$dcConf = 80
        if ($rvol -gt 2.5){$dcConf+=5}; if ($rvol -gt 4.0){$dcConf+=4}
        if ($atEMA9)      {$dcConf+=6}
        $dcConf = [Math]::Min($dcConf,97)

        [string]$dcEntry  = fmt $curP
        [string]$dcStop   = fmt ([Math]::Round($curP + $atr*0.5,2))
        [string]$dcT1     = fmt ([Math]::Round($curP - $atr*1.0,2))
        [string]$dcT2     = fmt ([Math]::Round($curP - $atr*2.0,2))
        [string]$dcT3     = fmt ([Math]::Round($curP - $atr*3.0,2))
        [string]$dcStrike = fmt ([Math]::Floor($curP/5.0)*5.0)
        [string]$bounNote = if ($atEMA9) {"BOUNCE ENTRY NOW -- price at 9 EMA "+$ema9Curr} else {"Wait for bounce to 9 EMA="+$ema9Curr}

        $dcMsg  = $EQ+"`n"
        $dcMsg += "[S25] DEATH CROSS -- "+$sym+"`n"
        $dcMsg += $EQ+"`n"
        $dcMsg += "9 EMA crossed BELOW 21 EMA (5-min chart)`n"
        $dcMsg += $DV+"`n"
        $dcMsg += "PREV BAR    : EMA9="+$ema9Prev+" was ABOVE EMA21="+$ema21Prev+"`n"
        $dcMsg += "CURR BAR    : EMA9="+$ema9Curr+" now BELOW EMA21="+$ema21Curr+"`n"
        $dcMsg += $DV+"`n"
        $dcMsg += "PRICE       : "+(fmt $curP)+"`n"
        $dcMsg += "VWAP        : "+(fmt $vwap)+"  ("+[Math]::Round($curP-$vwap,2)+" below -- CONFIRMED)`n"
        $dcMsg += "EMA9        : "+$ema9Curr+"  EMA21: "+$ema21Curr+"`n"
        $dcMsg += "RVOL        : "+$rvol+"x   ATR: "+$atr+"`n"
        $dcMsg += "CONFIDENCE  : "+$dcConf+"%`n"
        $dcMsg += $DV+"`n"
        $dcMsg += "ENTRY SETUP : "+$bounNote+"`n"
        $dcMsg += $DV+"`n"
        $dcMsg += "OPTION      : [DN] PUT "+$dcStrike+" (0DTE exp "+$expDate+")`n"
        $dcMsg += "Entry(stk)  : "+$dcEntry+"`n"
        $dcMsg += "Exit T1     : "+$dcT1+"  (1x ATR -- TAKE PROFIT)`n"
        $dcMsg += "Exit T2     : "+$dcT2+"  (2x ATR -- EXTENDED)`n"
        $dcMsg += "Exit T3     : "+$dcT3+"  (3x ATR -- MAX TARGET)`n"
        $dcMsg += "Stop(stk)   : "+$dcStop+"  (0.5x ATR -- CUT LOSS)`n"
        $dcMsg += $DV+"`n"
        $dcMsg += "RULES:`n"
        $dcMsg += "  1. Wait for 5-min candle CLOSE below both EMA9+EMA21`n"
        $dcMsg += "  2. Price must hold below VWAP "+(fmt $vwap)+"`n"
        $dcMsg += "  3. Enter on first bounce to EMA9="+$ema9Curr+"`n"
        $dcMsg += "  4. Trail stop to EMA9 once T1 hit`n"
        $dcMsg += $EQ+"`n"
        $dcMsg += "v1-DC | "+$nowET.ToString("HH:mm")+" ET | "+$sym

        Send-TG $dcMsg
        Write-Host ("  >>> S25 DEATH CROSS FIRED: "+$sym+" @ "+$curP+" | Conf="+$dcConf+"% | RVOL="+$rvol+"x")
    }
    else {
        Write-Host ("  No cross detected")
    }
}

Write-Host ("`n=== Cross Scan Complete. "+$crossesDetected+" crosses detected @ "+$nowET.ToString("HH:mm")+" ET ===")
if ($crossesDetected -eq 0) { Write-Host "  No Golden or Death Cross on any ticker this scan." }
