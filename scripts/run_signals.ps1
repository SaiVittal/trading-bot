# ============================================================
# Trading Bot — Opening Drive + 0DTE Signal Runner
# Branch : fix/entry-price-t1-t2-t3
#
# FIX: Entry price (and T1/T2/T3) now pulled from Alpaca
#      /v2/stocks/{sym}/trades/latest  →  actual last trade price
#      Never uses $pmP as entry anchor.
# ============================================================

param(
    [string[]]$Tickers = @("TSLA","NBIS","COST","MSFT","IBM","DELL","ORCL","APP","META"),
    [string]$ApiKey    = "PKUVZN3EUNDEDFIIWS3NHWMKXK",
    [string]$ApiSecret = "2otPyywguF8Xn2mgCwgLifEbP9s8RrbLF9mjVn95EjXo",
    [string]$TgToken   = "8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw",
    [string]$TgChat    = "5979887660"
)

$headers = @{ "APCA-API-KEY-ID" = $ApiKey; "APCA-API-SECRET-KEY" = $ApiSecret }
$base    = "https://data.alpaca.markets"

# ── helpers ──────────────────────────────────────────────────
function Calc-EMA([double[]]$arr, [int]$period) {
    if ($arr.Count -eq 0) { return 0.0 }
    $n  = [Math]::Min($period, $arr.Count)
    $k  = 2.0 / ($n + 1.0)
    $e  = $arr[0]
    for ($i = 1; $i -lt $arr.Count; $i++) { $e = $arr[$i] * $k + $e * (1.0 - $k) }
    return [Math]::Round($e, 2)
}

function Calc-RSI([double[]]$closes, [int]$period = 14) {
    if ($closes.Count -lt ($period + 1)) { return 50.0 }
    $gains = 0.0; $losses = 0.0
    for ($i = 1; $i -le $period; $i++) {
        $d = $closes[$i] - $closes[$i - 1]
        if ($d -gt 0) { $gains += $d } else { $losses += [Math]::Abs($d) }
    }
    $ag = $gains / $period; $al = $losses / $period
    for ($i = ($period + 1); $i -lt $closes.Count; $i++) {
        $d = $closes[$i] - $closes[$i - 1]
        if ($d -gt 0) { $ag = ($ag * ($period - 1) + $d) / $period; $al = ($al * ($period - 1)) / $period }
        else          { $al = ($al * ($period - 1) + [Math]::Abs($d)) / $period; $ag = ($ag * ($period - 1)) / $period }
    }
    if ($al -eq 0) { return 100.0 }
    return [Math]::Round(100.0 - (100.0 / (1.0 + $ag / $al)), 1)
}

function Calc-ATR([object[]]$bars, [int]$period = 14) {
    if ($bars.Count -lt 2) { return 0.0 }
    $trs = @()
    for ($i = 1; $i -lt $bars.Count; $i++) {
        $hl  = $bars[$i].h - $bars[$i].l
        $hpc = [Math]::Abs($bars[$i].h - $bars[$i - 1].c)
        $lpc = [Math]::Abs($bars[$i].l - $bars[$i - 1].c)
        $trs += [Math]::Max($hl, [Math]::Max($hpc, $lpc))
    }
    $n = [Math]::Min($period, $trs.Count)
    return [Math]::Round(($trs | Select-Object -Last $n | Measure-Object -Sum).Sum / $n, 2)
}

function Calc-VWAP([object[]]$bars) {
    $tpv = 0.0; $tv = 0.0
    foreach ($b in $bars) {
        $tp   = ($b.h + $b.l + $b.c) / 3.0
        $tpv += $tp * $b.v
        $tv  += $b.v
    }
    if ($tv -eq 0) { return 0.0 }
    return [Math]::Round($tpv / $tv, 2)
}

function Send-TG([string]$msg) {
    $body = '{"chat_id":"' + $TgChat + '","text":' + ($msg | ConvertTo-Json) + '}'
    try { Invoke-RestMethod -Uri "https://api.telegram.org/bot$TgToken/sendMessage" -Method POST -Body $body -ContentType "application/json" | Out-Null }
    catch { Write-Host "TG-ERR: $($_.Exception.Message)" }
    Start-Sleep -Milliseconds 900
}

function Get-Bars([string]$sym, [string]$tf, [string]$start, [int]$limit = 100) {
    $url  = "$base/v2/stocks/$sym/bars?timeframe=$tf&start=$start&limit=$limit&feed=iex&sort=asc"
    try   { return (Invoke-RestMethod -Uri $url -Headers $headers).bars }
    catch { return @() }
}

# ── FIX: get actual last trade price ─────────────────────────
function Get-LastPrice([string]$sym) {
    try {
        $r = Invoke-RestMethod -Uri "$base/v2/stocks/$sym/trades/latest?feed=iex" -Headers $headers
        return [Math]::Round([double]$r.trade.p, 2)
    } catch {
        # fallback to latest bar close
        try {
            $r = Invoke-RestMethod -Uri "$base/v2/stocks/$sym/bars/latest?feed=iex" -Headers $headers
            return [Math]::Round([double]$r.bar.c, 2)
        } catch { return 0.0 }
    }
}

# ── Date anchors ──────────────────────────────────────────────
$todayDate  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
$pmStart    = "${todayDate}T09:00:00Z"   # premarket bars
$dailyStart = "2026-03-01T00:00:00Z"     # daily bars for RVOL/prevClose
$sessionStart = "${todayDate}T13:30:00Z" # regular session 5-min bars

$nowET = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Eastern Standard Time')
Write-Host "=== Signal Run @ $($nowET.ToString('HH:mm')) ET on $todayDate ==="

# ── Summary header ────────────────────────────────────────────
$summary = @()

foreach ($sym in $Tickers) {
    Write-Host "`n--- $sym ---"

    # 1. Daily bars (for prevClose, 20-day avg vol, ATR daily)
    $daily = @(Get-Bars $sym "1Day" $dailyStart 55)
    if ($daily.Count -lt 5) { Write-Host "${sym}: insufficient daily data"; continue }

    [double]$prevClose = [Math]::Round([double]$daily[-1].c, 2)
    [double]$avgVol20  = ($daily | Select-Object -Last 20 | ForEach-Object { [double]$_.v } | Measure-Object -Average).Average
    [double]$atrDaily  = Calc-ATR $daily 14
    $dailyCloses       = $daily | ForEach-Object { [double]$_.c }

    # 2. FIX: actual last trade price (never use $pmP for entry)
    [double]$curP = Get-LastPrice $sym
    if ($curP -eq 0.0) { $curP = $prevClose }
    Write-Host "$sym last price = $curP"

    # 3. Premarket bars
    $pmBars = @(Get-Bars $sym "1Min" $pmStart 200)
    [bool]$hasPM  = ($pmBars.Count -gt 0)
    [double]$pmO  = if ($hasPM) { [Math]::Round([double]$pmBars[0].o, 2) } else { 0.0 }
    [double]$pmH  = if ($hasPM) { ($pmBars | ForEach-Object { [double]$_.h } | Measure-Object -Maximum).Maximum } else { 0.0 }
    [double]$pmVol= if ($hasPM) { ($pmBars | ForEach-Object { [double]$_.v } | Measure-Object -Sum).Sum }         else { 0.0 }
    [string]$pmDataStr = if ($hasPM) { "O=$pmO  H=$([Math]::Round($pmH,2))  Vol=$([int]$pmVol)" } else { "No premarket activity yet" }

    # 4. Gap %
    [double]$gapRef = if ($hasPM -and $pmO -gt 0) { $pmO } else { $curP }
    [double]$gapPct = if ($prevClose -gt 0) { [Math]::Round((($gapRef - $prevClose) / $prevClose) * 100, 2) } else { 0.0 }
    [string]$gapStr = if ($gapPct -ge 0) { "+$gapPct%" } else { "$gapPct%" }

    # 5. Intraday session bars (available after 9:30 ET)
    $idays = @(Get-Bars $sym "5Min" $sessionStart 80)
    $useIntraday = ($idays.Count -ge 2)   # use as soon as 2 bars exist

    # For RSI/EMA: blend last 30 daily closes + intraday closes for rich lookback
    # This prevents RSI collapsing to 50 when only 2-3 intraday bars exist
    $dailyTail  = @($dailyCloses | Select-Object -Last 30)
    $intradayC  = @(if ($useIntraday) { $idays | ForEach-Object { [double]$_.c } }
                    elseif ($hasPM)   { $pmBars | ForEach-Object { [double]$_.c } }
                    else              { @() })
    $blendedC   = @($dailyTail + $intradayC)   # daily anchor + today's prints

    # ATR: use daily ATR until 14+ intraday bars exist (intraday ATR is noise with 2-3 bars)
    [double]$iATRraw = if ($idays.Count -ge 14) { Calc-ATR $idays 14 } else { 0.0 }
    [double]$atr     = if ($iATRraw -gt ($atrDaily * 0.3)) { $iATRraw } else { $atrDaily }

    [double]$rsi   = Calc-RSI  ([double[]]$blendedC) 14
    [double]$ema9  = Calc-EMA  ([double[]]$blendedC) 9
    [double]$ema21 = Calc-EMA  ([double[]]$blendedC) 21
    [double]$ema50 = Calc-EMA  ([double[]]$blendedC) 50
    [double]$vwap  = if ($useIntraday) { Calc-VWAP $idays } else { if ($hasPM) { Calc-VWAP $pmBars } else { $prevClose } }

    # RVOL — pace today's volume vs avg daily; fix: use ToUniversalTime() on both ends
    $sessionOpenUtc = [DateTime]::Parse("${todayDate}T13:30:00Z").ToUniversalTime()
    $nowUtcMins     = ([DateTime]::UtcNow.ToUniversalTime() - $sessionOpenUtc).TotalMinutes
    $fracElapsed    = [Math]::Max([Math]::Min($nowUtcMins / 390.0, 1.0), 0.001)
    [double]$todayVol = if ($useIntraday) { ($idays | ForEach-Object { [double]$_.v } | Measure-Object -Sum).Sum }
                        elseif ($hasPM)   { $pmVol }
                        else              { 0.0 }
    [double]$projVol  = $todayVol / $fracElapsed
    [double]$rvol     = if ($avgVol20 -gt 0 -and $projVol -gt 0) { [Math]::Round($projVol / $avgVol20, 2) } else { 0.0 }

    [bool]$abvEMA9  = ($curP -gt $ema9)
    [bool]$abvVWAP  = ($curP -gt $vwap)
    [string]$trend  = if ($ema9 -gt $ema21 -and $ema21 -gt $ema50) { "UP" } elseif ($ema9 -lt $ema21) { "DOWN" } else { "NEUTRAL" }

    Write-Host "  RSI=$rsi  EMA9=$ema9  ATR=$atr  RVOL=$($rvol)x  VWAP=$vwap  Trend=$trend"

    # ── 6. Strategy Engine ────────────────────────────────────
    # Signals stored as pipe-delimited strings: TYPE|ID|NAME|DIR|CONF|ACTION
    $raw = @()

    # --- OPENING DRIVE SIGNALS ---
    # S19A: Gap Breakout (Gap>3%, RVOL>1.2, RSI>52, AbvVWAP)
    if ($gapPct -gt 3.0 -and $rvol -gt 1.2 -and $rsi -gt 52 -and $abvVWAP) {
        $conf = 70
        if ($gapPct -gt 10) { $conf += 10 }
        if ($rvol   -gt 3)  { $conf += 8  }
        if ($rsi    -gt 60) { $conf += 5  }
        if ($hasPM)         { $conf += 5  }
        $conf = [Math]::Min($conf, 98)
        $raw += "OD|S19A|Opening Drive Gap Breakout|LONG|$conf|Gap=$gapPct% with RVOL=$($rvol)x confirms drive."
    }

    # S19B: Opening Drive Pullback (Gap>2%, AbvVWAP, RSI 45–65)
    if ($gapPct -gt 2.0 -and $abvVWAP -and $rsi -gt 45 -and $rsi -lt 68 -and $rvol -gt 1.0) {
        $conf = 65
        if ($rvol -gt 2) { $conf += 5 }
        if ($hasPM)      { $conf += 5 }
        $raw += "OD|S19B|Opening Drive Pullback|LONG|$conf|Gap=$gapPct% pullback to VWAP=$vwap. Enter on bounce."
    }

    # S08: ORB (Opening Range Breakout) — requires intraday bars
    if ($useIntraday -and $idays.Count -ge 2) {
        [double]$orH = ($idays | Select-Object -First 2 | ForEach-Object { [double]$_.h } | Measure-Object -Maximum).Maximum
        [double]$orL = ($idays | Select-Object -First 2 | ForEach-Object { [double]$_.l } | Measure-Object -Minimum).Minimum
        if ($curP -gt $orH -and $rvol -gt 1.2) {
            $conf = 72; if ($rvol -gt 2) { $conf += 6 }
            $raw += "OD|S08|Opening Range Breakout|LONG|$conf|Price broke above ORB high=$([Math]::Round($orH,2))."
        } elseif ($curP -lt $orL -and $rvol -gt 1.2) {
            $conf = 70; if ($rvol -gt 2) { $conf += 6 }
            $raw += "OD|S08|Opening Range Breakdown|SHORT|$conf|Price broke below ORB low=$([Math]::Round($orL,2))."
        }
    }

    # --- 0DTE SIGNALS ---
    # S18: 9-EMA Cross Momentum
    if ($atr -gt 0) {
        [double]$e9Dist = [Math]::Round([Math]::Abs($curP - $ema9) / $atr, 2)
        if ($abvEMA9 -and $e9Dist -ge 0.3 -and $e9Dist -lt 5.0 -and $rsi -gt 48 -and $rsi -lt 80 -and $trend -eq "UP") {
            $conf = 78; if ($e9Dist -lt 1.5) { $conf += 5 }; if ($rsi -gt 55 -and $rsi -lt 72) { $conf += 4 }
            $conf = [Math]::Min($conf, 95)
            $raw += "0DTE|S18|9-EMA Cross Momentum|LONG|$conf|Price crossed above EMA9 $ema9 (dist=$e9Dist ATR). Enter calls at 9:35 AM retest."
        } elseif (-not $abvEMA9 -and $e9Dist -ge 0.3 -and $e9Dist -lt 5.0 -and $rsi -gt 20 -and $rsi -lt 52 -and $trend -eq "DOWN") {
            $conf = 75
            $raw += "0DTE|S18|9-EMA Cross Momentum|SHORT|$conf|Price crossed below EMA9 $ema9. Enter puts."
        }
    }

    # S16: VWAP Momentum — price thrust above VWAP with volume
    if ($abvVWAP -and $rvol -gt 1.5 -and $rsi -gt 52 -and $rsi -lt 75) {
        $conf = 68; if ($rvol -gt 2.5) { $conf += 7 }
        $raw += "0DTE|S16|VWAP Momentum Thrust|LONG|$conf|RVOL=$($rvol)x with price above VWAP $vwap."
    }

    # S17: EMA9/EMA21 Squeeze
    if ($abvEMA9 -and $ema9 -gt $ema21 -and $rsi -gt 50 -and $rsi -lt 72 -and $rvol -gt 1.0) {
        $conf = 65; if ($rvol -gt 1.8) { $conf += 5 }
        $raw += "0DTE|S17|EMA9-21 Trend Squeeze|LONG|$conf|EMA9 > EMA21 with momentum."
    }

    # S13: Momentum Breakout
    if ($rsi -gt 60 -and $rvol -gt 2.0 -and $abvVWAP -and $gapPct -gt 2.0) {
        $conf = 72; if ($gapPct -gt 8) { $conf += 8 }
        $raw += "0DTE|S13|Momentum Breakout|LONG|$conf|RSI=$rsi + RVOL=$($rvol)x breakout."
    }

    # S15: HOD Breakout (needs intraday)
    if ($useIntraday) {
        [double]$hod = ($idays | ForEach-Object { [double]$_.h } | Measure-Object -Maximum).Maximum
        if ($curP -ge $hod * 0.999 -and $rvol -gt 1.5 -and $rsi -gt 55) {
            $conf = 70; if ($rvol -gt 3) { $conf += 7 }
            $raw += "0DTE|S15|HOD Breakout|LONG|$conf|Testing HOD=$([Math]::Round($hod,2)) with RVOL=$($rvol)x."
        }
    }

    # S01: VWAP Bounce Long
    if ($abvVWAP -and $rsi -gt 45 -and $rsi -lt 70) {
        $conf = 60; if ($rvol -gt 1.3) { $conf += 5 }
        $raw += "0DTE|S01|VWAP Bounce Long|LONG|$conf|Price bouncing off VWAP $vwap. ATM calls at VWAP touch."
    }

    # S07: Overbought Fade — PUT signal
    if ($rsi -gt 74 -and $rvol -gt 0.8) {
        $conf = 65; if ($rsi -gt 80) { $conf += 8 }
        $raw += "0DTE|S07|Overbought Exhaustion|SHORT|$conf|RSI=$rsi. Fade gap for puts. Wait for first red 5-min candle."
    }

    # ── 7. Filter & Consensus ─────────────────────────────────
    $fired = @($raw | Where-Object { [int]($_.Split("|")[4]) -ge 55 } | Sort-Object { [int]($_.Split("|")[4]) } -Descending)
    $odSigs   = @($fired | Where-Object { $_.Split("|")[0] -eq "OD"   })
    $odteSigs = @($fired | Where-Object { $_.Split("|")[0] -eq "0DTE" })

    $topConf   = if ($fired.Count -gt 0) { [int]($fired[0].Split("|")[4]) } else { 0 }
    $consensus = ($fired.Count -ge 2) -or ($topConf -ge 78)

    # Direction majority vote
    $longCt  = ($fired | Where-Object { $_.Split("|")[3] -eq "LONG"  }).Count
    $shortCt = ($fired | Where-Object { $_.Split("|")[3] -eq "SHORT" }).Count
    $majDir  = if ($longCt -ge $shortCt) { "LONG" } else { "SHORT" }

    Write-Host "  Signals fired: $($fired.Count)  Consensus: $consensus  Direction: $majDir"

    # ── 8. BUILD ALERT ────────────────────────────────────────
    # FIX: ALL price levels computed from $curP (actual last trade price)
    # Never use $pmP or $pmO as the entry anchor.
    [double]$entryP = $curP
    [double]$atrUse = if ($atr -gt 0.01) { $atr } else { $entryP * 0.01 }

    if ($majDir -eq "LONG") {
        [string]$sEntry = "`$" + $entryP
        [string]$sStop  = "`$" + [Math]::Round($entryP - $atrUse,     2)
        [string]$sT1    = "`$" + [Math]::Round($entryP + $atrUse,     2)
        [string]$sT2    = "`$" + [Math]::Round($entryP + $atrUse*2.0, 2)
        [string]$sT3    = "`$" + [Math]::Round($entryP + $atrUse*3.0, 2)
        [string]$optType = "CALL"
    } else {
        [string]$sEntry = "`$" + $entryP
        [string]$sStop  = "`$" + [Math]::Round($entryP + $atrUse,     2)
        [string]$sT1    = "`$" + [Math]::Round($entryP - $atrUse,     2)
        [string]$sT2    = "`$" + [Math]::Round($entryP - $atrUse*2.0, 2)
        [string]$sT3    = "`$" + [Math]::Round($entryP - $atrUse*3.0, 2)
        [string]$optType = "PUT"
    }

    # ATM strike (round to nearest $5)
    [int]$strike = [int]([Math]::Round($entryP / 5.0) * 5)

    $header = if ($consensus) { ">>> $majDir ALERT FIRES <<<" } else { "BELOW CONSENSUS - watch at open" }

    # ── Opening Drive block ───────────────────────────────────
    $odBlock = ""
    if ($odSigs.Count -gt 0) {
        $odBlock = "OPENING DRIVE SIGNALS:`n"
        foreach ($s in $odSigs) {
            $p = $s.Split("|")
            $odBlock += "  [$($p[1])] $($p[2])`n"
            $odBlock += "  Direction  : $($p[3])`n"
            $odBlock += "  Confidence : $($p[4])%`n"
            $odBlock += "  Action     : $($p[5])`n"
        }
    } else {
        $odBlock = "OPENING DRIVE SIGNALS:`n  None fired above threshold`n"
    }

    # ── 0DTE block ───────────────────────────────────────────
    $dtBlock = ""
    if ($odteSigs.Count -gt 0) {
        $dtBlock = "0DTE SIGNALS:`n"
        foreach ($s in $odteSigs) {
            $p = $s.Split("|")
            $dtBlock += "  [$($p[1])] $($p[2]) - 0DTE " + ($optType + "S") + "`n"
            $dtBlock += "  Direction  : $($p[3])`n"
            $dtBlock += "  Confidence : $($p[4])%`n"
            $dtBlock += "  Action     : $($p[5])`n"
        }
    } else {
        $dtBlock = "0DTE SIGNALS:`n  None fired above threshold`n"
    }

    # ── Compose full message ──────────────────────────────────
    $eq = "=" * 42
    $dv = "-" * 40
    $msg  = $eq + "`n"
    $msg += "$sym  |  $header`n"
    $msg += $eq + "`n"
    $msg += "PREV CLOSE  : `$$prevClose`n"
    $msg += "LAST PRICE  : `$$curP`n"
    if ($hasPM) {
        $msg += "PM DATA     : $pmDataStr`n"
        $msg += "GAP         : $gapStr`n"
    } else {
        $msg += "PM DATA     : No premarket activity yet`n"
    }
    $msg += $dv + "`n"
    $msg += "RSI  : $rsi    TREND : $trend`n"
    $msg += "EMA9 : $ema9   EMA21 : $ema21`n"
    $msg += "EMA50: $ema50  ATR   : $atrUse`n"
    $msg += "RVOL : $($rvol)x   VWAP  : $vwap`n"
    $msg += "AbvEMA9:$abvEMA9   AbvVWAP:$abvVWAP`n"
    $msg += $dv + "`n"
    $msg += $odBlock
    $msg += $dv + "`n"
    $msg += $dtBlock

    if ($consensus) {
        $msg += "`n"
        $msg += $dv + "`n"
        $msg += "DIRECTION   : $majDir`n"
        $msg += "ENTRY       : $sEntry  (confirm at 9:35 AM open)`n"
        $msg += "STOP        : $sStop  (hard stop 1x ATR)`n"
        $msg += "TARGET 1    : $sT1`n"
        $msg += "TARGET 2    : $sT2`n"
        $msg += "TARGET 3    : $sT3`n"
        $msg += "R:R         : 1:2 minimum`n"
        $msg += $dv + "`n"
        $msg += "0DTE OPTIONS (exp $($todayDate.Substring(5,2))/$($todayDate.Substring(8,2))):`n"
        $msg += "  Type   : $optType`n"
        $msg += "  Strike : ~`$$strike`n"
        $msg += "  Delta  : 0.40-0.55 ATM`n"
        $msg += "  Rule   : Enter ONLY if confirms direction at 9:35 AM`n"
        $msg += "  Exit   : T1 OR 11:00 AM - whichever first`n"
    }
    $msg += $eq + "`n"
    $msg += "Run: $($nowET.ToString('HH:mm')) ET"

    # ── Send ──────────────────────────────────────────────────
    Send-TG $msg

    # ── Summary row ───────────────────────────────────────────
    $summaryStatus = if ($consensus) { "$majDir ALERT ($($fired.Count) sigs, top $topConf%)" } else { "watch ($($fired.Count) sig)" }
    $summary += [PSCustomObject]@{ Ticker=$sym; Status=$summaryStatus; LastP=$curP; Gap=$gapStr; RVOL=$rvol; RSI=$rsi }
}

# ── Summary message ───────────────────────────────────────────
$sumMsg  = "=" * 42 + "`n"
$sumMsg += "SIGNAL SUMMARY -- " + $nowET.ToString('HH:mm') + " ET`n"
$sumMsg += "=" * 42 + "`n"
foreach ($r in $summary) {
    $sumMsg += "$($r.Ticker.PadRight(6)) | $($r.Status)`n"
    $sumMsg += "       Price=`$$($r.LastP)  Gap=$($r.Gap)  RVOL=$($r.RVOL)x  RSI=$($r.RSI)`n"
}
$sumMsg += "=" * 42
Send-TG $sumMsg

Write-Host "`n=== Done. $($summary.Count) tickers processed. ==="
$summary | Format-Table -AutoSize
