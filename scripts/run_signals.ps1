# ============================================================
# Trading Bot Signal Runner  v3
# Branch : fix/entry-price-t1-t2-t3
#
# KEY RULE: NO price variable is ever placed inside a double-quoted
# string.  Every price output uses explicit string concatenation (+)
# so PowerShell never re-parses the dollar sign prefix.
# ============================================================

param(
    [string[]]$Tickers = @("TSLA","NBIS","COST","MSFT","IBM","SPY","QQQ","ORCL","APP"),
    [string]$ApiKey    = "PKUVZN3EUNDEDFIIWS3NHWMKXK",
    [string]$ApiSecret = "2otPyywguF8Xn2mgCwgLifEbP9s8RrbLF9mjVn95EjXo",
    [string]$TgToken   = "8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw",
    [string]$TgChat    = "5979887660"
)

$ErrorActionPreference = "SilentlyContinue"
$headers  = @{ "APCA-API-KEY-ID" = $ApiKey; "APCA-API-SECRET-KEY" = $ApiSecret }
$baseUrl  = "https://data.alpaca.markets"
$paperUrl = "https://paper-api.alpaca.markets"

# ════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════

function fmt([double]$v) {
    # Returns  "$123.45"  using pure concatenation -- never interpolation
    return "$" + [Math]::Round($v, 2).ToString("F2")
}

function Get-Bars([string]$sym, [string]$tf, [string]$startUtc, [int]$lim = 100) {
    $url = $baseUrl + "/v2/stocks/" + $sym + "/bars?timeframe=" + $tf +
           "&start=" + $startUtc + "&limit=" + $lim + "&feed=iex&sort=asc"
    try { return @((Invoke-RestMethod -Uri $url -Headers $headers).bars) }
    catch { return @() }
}

function Get-LastPrice([string]$sym) {
    try {
        $r = Invoke-RestMethod -Uri ($baseUrl + "/v2/stocks/" + $sym + "/trades/latest?feed=iex") -Headers $headers
        return [Math]::Round([double]$r.trade.p, 2)
    } catch {
        try {
            $r2 = Invoke-RestMethod -Uri ($baseUrl + "/v2/stocks/" + $sym + "/bars/latest?feed=iex") -Headers $headers
            return [Math]::Round([double]$r2.bar.c, 2)
        } catch { return 0.0 }
    }
}

function Calc-EMA([double[]]$arr, [int]$period) {
    if ($arr.Count -eq 0) { return 0.0 }
    $k = 2.0 / ([Math]::Min($period, $arr.Count) + 1.0)
    $e = $arr[0]
    for ($i = 1; $i -lt $arr.Count; $i++) { $e = $arr[$i] * $k + $e * (1.0 - $k) }
    return [Math]::Round($e, 2)
}

function Calc-RSI([double[]]$closes, [int]$period = 14) {
    if ($closes.Count -lt ($period + 1)) { return 50.0 }
    $ag = 0.0; $al = 0.0
    for ($i = 1; $i -le $period; $i++) {
        $d = $closes[$i] - $closes[$i - 1]
        if ($d -gt 0) { $ag += $d } else { $al += [Math]::Abs($d) }
    }
    $ag /= $period; $al /= $period
    for ($i = ($period + 1); $i -lt $closes.Count; $i++) {
        $d = $closes[$i] - $closes[$i - 1]
        if ($d -gt 0) { $ag = ($ag * ($period - 1) + $d) / $period; $al = $al * ($period - 1) / $period }
        else          { $al = ($al * ($period - 1) + [Math]::Abs($d)) / $period; $ag = $ag * ($period - 1) / $period }
    }
    if ($al -eq 0) { return 100.0 }
    return [Math]::Round(100.0 - 100.0 / (1.0 + $ag / $al), 1)
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
        $tp = ($b.h + $b.l + $b.c) / 3.0
        $tpv += $tp * $b.v; $tv += $b.v
    }
    if ($tv -eq 0) { return 0.0 }
    return [Math]::Round($tpv / $tv, 2)
}

function Send-TG([string]$msg) {
    $jsonText = $msg | ConvertTo-Json   # proper JSON escaping of newlines, quotes
    $body = '{"chat_id":"' + $TgChat + '","text":' + $jsonText + '}'
    try {
        Invoke-RestMethod -Uri ("https://api.telegram.org/bot" + $TgToken + "/sendMessage") `
            -Method POST -Body $body -ContentType "application/json" | Out-Null
    } catch {
        Write-Host "TG-ERR: " + $_.Exception.Message
    }
    Start-Sleep -Milliseconds 900
}

# ════════════════════════════════════════════
#  DATE / TIME ANCHORS
# ════════════════════════════════════════════

$nowUtc       = [DateTime]::UtcNow.ToUniversalTime()
$todayDate    = $nowUtc.ToString("yyyy-MM-dd")
$nowET        = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($nowUtc, "Eastern Standard Time")
$sessionOpen  = [DateTime]::Parse($todayDate + "T13:30:00Z").ToUniversalTime()
$dailyStart   = "2026-03-01T00:00:00Z"
$sessionStart = $todayDate + "T13:30:00Z"
$pmStart      = $todayDate + "T09:00:00Z"

$EQ = "=" * 44
$DV = "-" * 42

Write-Host ("=== Signal Run @ " + $nowET.ToString("HH:mm") + " ET  " + $todayDate + " ===")

$summary = @()

# ════════════════════════════════════════════
#  MAIN LOOP
# ════════════════════════════════════════════

foreach ($sym in $Tickers) {
    Write-Host ("`n--- " + $sym + " ---")

    # ── 1. Daily bars ──────────────────────────────────────────
    $daily = @(Get-Bars $sym "1Day" $dailyStart 60)
    if ($daily.Count -lt 5) {
        Write-Host ($sym + ": insufficient daily data -- skip")
        continue
    }
    [double]$prevClose = [Math]::Round([double]$daily[-1].c, 2)
    [double]$avgVol20  = ($daily | Select-Object -Last 20 | ForEach-Object { [double]$_.v } | Measure-Object -Average).Average
    [double]$atrDaily  = Calc-ATR $daily 14
    [double[]]$dailyC  = @($daily | ForEach-Object { [double]$_.c })

    # ── 2. Actual last trade price (entry anchor) ──────────────
    [double]$curP = Get-LastPrice $sym
    if ($curP -eq 0.0) { $curP = $prevClose }

    # ── 3. Premarket bars ──────────────────────────────────────
    $pmBars = @(Get-Bars $sym "1Min" $pmStart 300)
    [bool]$hasPM   = ($pmBars.Count -gt 0)
    [double]$pmO   = if ($hasPM) { [Math]::Round([double]$pmBars[0].o, 2) } else { 0.0 }
    [double]$pmH   = if ($hasPM) { ($pmBars | ForEach-Object { [double]$_.h } | Measure-Object -Maximum).Maximum } else { 0.0 }
    [double]$pmVol = if ($hasPM) { ($pmBars | ForEach-Object { [double]$_.v } | Measure-Object -Sum).Sum } else { 0.0 }
    $pmDataStr     = if ($hasPM) { "O=" + $pmO + "  H=" + [Math]::Round($pmH,2) + "  Vol=" + [int]$pmVol } else { "No premarket activity" }

    # ── 4. Gap % ───────────────────────────────────────────────
    [double]$gapRef = if ($hasPM -and $pmO -gt 0) { $pmO } else { $curP }
    [double]$gapPct = if ($prevClose -gt 0) { [Math]::Round(($gapRef - $prevClose) / $prevClose * 100.0, 2) } else { 0.0 }
    $gapStr         = if ($gapPct -ge 0) { "+" + $gapPct + "%" } else { $gapPct.ToString() + "%" }

    # ── 5. Intraday session bars ───────────────────────────────
    $idays       = @(Get-Bars $sym "5Min" $sessionStart 80)
    [bool]$hasID = ($idays.Count -ge 2)

    # ── 6. Blended closes for RSI/EMA (30 daily + today intraday) ─
    [double[]]$idayC   = if ($hasID) { @($idays | ForEach-Object { [double]$_.c }) } else { @() }
    [double[]]$pmC     = if ($hasPM) { @($pmBars | ForEach-Object { [double]$_.c }) } else { @() }
    [double[]]$blended = @($dailyC | Select-Object -Last 30) + $(if ($hasID) { $idayC } elseif ($hasPM) { $pmC } else { @() })

    # ── 7. Indicators ─────────────────────────────────────────
    # ATR: use daily until 14+ intraday bars exist
    [double]$iATR = if ($idays.Count -ge 14) { Calc-ATR $idays 14 } else { 0.0 }
    [double]$atr  = if ($iATR -gt ($atrDaily * 0.3)) { $iATR } else { $atrDaily }

    [double]$rsi   = Calc-RSI  $blended 14
    [double]$ema9  = Calc-EMA  $blended 9
    [double]$ema21 = Calc-EMA  $blended 21
    [double]$ema50 = Calc-EMA  $blended 50

    [double]$vwap  = if ($hasID)      { Calc-VWAP $idays  }
                     elseif ($hasPM)  { Calc-VWAP $pmBars }
                     else             { $prevClose }

    # RVOL -- paced against avg daily volume
    $minsElapsed  = ($nowUtc - $sessionOpen).TotalMinutes
    $fracElapsed  = [Math]::Max([Math]::Min($minsElapsed / 390.0, 1.0), 0.001)
    [double]$todayVol = if ($hasID)     { ($idays  | ForEach-Object { [double]$_.v } | Measure-Object -Sum).Sum }
                        elseif ($hasPM) { $pmVol }
                        else            { 0.0 }
    [double]$rvol = if ($avgVol20 -gt 0 -and $todayVol -gt 0) { [Math]::Round(($todayVol / $fracElapsed) / $avgVol20, 2) } else { 0.0 }

    [bool]$abvEMA9  = ($curP -gt $ema9)
    [bool]$abvVWAP  = ($curP -gt $vwap -and $vwap -gt 0)
    [string]$trend  = if ($ema9 -gt $ema21 -and $ema21 -gt $ema50) { "UP" }
                      elseif ($ema9 -lt $ema21)                     { "DOWN" }
                      else                                           { "NEUTRAL" }

    Write-Host ("  Price=" + $curP + "  RSI=" + $rsi + "  EMA9=" + $ema9 + "  ATR=" + $atr + "  RVOL=" + $rvol + "x  VWAP=" + $vwap + "  Trend=" + $trend)

    # ════════════════════════════════════════
    #  8. STRATEGY ENGINE
    # ════════════════════════════════════════
    # Format:  TYPE|ID|NAME|DIR|CONF|ACTION_TEXT
    [System.Collections.Generic.List[string]]$raw = @()

    # ── OPENING DRIVE SIGNALS ──────────────────────────────────

    # S19A: Gap Breakout Drive (Gap>3%, RVOL>1.2, RSI>52, AbvVWAP)
    if ($gapPct -gt 3.0 -and $rvol -gt 1.2 -and $rsi -gt 52 -and $abvVWAP) {
        [int]$c = 70
        if ($gapPct -gt 10)    { $c += 10 }
        if ($gapPct -gt 20)    { $c += 5  }
        if ($rvol   -gt 3.0)   { $c += 8  }
        if ($rvol   -gt 8.0)   { $c += 5  }
        if ($rsi    -gt 60)    { $c += 5  }
        if ($hasPM)            { $c += 2  }
        $c = [Math]::Min($c, 98)
        $raw.Add("OD|S19A|Opening Drive Gap Breakout|LONG|" + $c + "|Gap=" + $gapPct + "% RVOL=" + $rvol + "x confirms opening drive momentum.")
    }

    # S19B: Opening Drive Pullback to VWAP (Gap>2%, AbvVWAP, RSI 46--67)
    if ($gapPct -gt 2.0 -and $abvVWAP -and $rsi -gt 46 -and $rsi -lt 68 -and $rvol -gt 1.0) {
        [int]$c = 65
        if ($rvol -gt 2.0) { $c += 5 }
        if ($hasPM)        { $c += 3 }
        $raw.Add("OD|S19B|Opening Drive Pullback|LONG|" + $c + "|Gap=" + $gapPct + "% pullback to VWAP " + $vwap + ". Enter on bounce confirmation.")
    }

    # S08: Opening Range Breakout (requires intraday bars)
    if ($hasID -and $idays.Count -ge 2) {
        [double]$orH = ($idays | Select-Object -First 2 | ForEach-Object { [double]$_.h } | Measure-Object -Maximum).Maximum
        [double]$orL = ($idays | Select-Object -First 2 | ForEach-Object { [double]$_.l } | Measure-Object -Minimum).Minimum
        if ($curP -gt $orH -and $rvol -gt 1.2) {
            [int]$c = 72; if ($rvol -gt 2.0) { $c += 6 }; if ($rvol -gt 5.0) { $c += 4 }
            $raw.Add("OD|S08|Opening Range Breakout|LONG|" + $c + "|Broke above ORB high " + [Math]::Round($orH,2) + " with RVOL=" + $rvol + "x.")
        } elseif ($curP -lt $orL -and $rvol -gt 1.2) {
            [int]$c = 70; if ($rvol -gt 2.0) { $c += 6 }
            $raw.Add("OD|S08|Opening Range Breakdown|SHORT|" + $c + "|Broke below ORB low " + [Math]::Round($orL,2) + " with RVOL=" + $rvol + "x.")
        }
    }

    # ── 0DTE SIGNALS ──────────────────────────────────────────

    # S18: 9-EMA Cross Momentum (core 0DTE setup)
    if ($atr -gt 0) {
        [double]$e9d = [Math]::Round([Math]::Abs($curP - $ema9) / $atr, 2)
        if ($abvEMA9 -and $e9d -ge 0.3 -and $e9d -lt 5.0 -and $rsi -gt 48 -and $rsi -lt 80 -and $trend -eq "UP") {
            [int]$c = 78
            if ($e9d -lt 1.5) { $c += 5 }
            if ($rsi -gt 55 -and $rsi -lt 72) { $c += 4 }
            $c = [Math]::Min($c, 95)
            $raw.Add("0DTE|S18|9-EMA Cross Momentum|LONG|" + $c + "|Price above EMA9 " + $ema9 + " by " + $e9d + " ATR units. Enter calls on 9:35 retest.")
        } elseif (-not $abvEMA9 -and $e9d -ge 0.3 -and $e9d -lt 5.0 -and $rsi -gt 20 -and $rsi -lt 52 -and $trend -eq "DOWN") {
            [int]$c = 75
            $raw.Add("0DTE|S18|9-EMA Cross Momentum|SHORT|" + $c + "|Price below EMA9 " + $ema9 + ". Enter puts on bounce to EMA9.")
        }
    }

    # S16: VWAP Momentum Thrust
    if ($abvVWAP -and $rvol -gt 1.5 -and $rsi -gt 52 -and $rsi -lt 76) {
        [int]$c = 68; if ($rvol -gt 3.0) { $c += 7 }; if ($rvol -gt 8.0) { $c += 5 }
        $raw.Add("0DTE|S16|VWAP Momentum Thrust|LONG|" + $c + "|RVOL=" + $rvol + "x above VWAP " + $vwap + ". Momentum building.")
    }

    # S17: EMA9/EMA21 Trend Squeeze
    if ($abvEMA9 -and $ema9 -gt $ema21 -and $rsi -gt 50 -and $rsi -lt 73 -and $rvol -gt 1.0) {
        [int]$c = 65; if ($rvol -gt 2.0) { $c += 5 }
        $raw.Add("0DTE|S17|EMA9-21 Trend Squeeze|LONG|" + $c + "|EMA9 " + $ema9 + " > EMA21 " + $ema21 + ". Trend continuation.")
    }

    # S13: Momentum Breakout (gap + volume + RSI)
    if ($rsi -gt 60 -and $rvol -gt 2.0 -and $abvVWAP -and $gapPct -gt 2.0) {
        [int]$c = 72; if ($gapPct -gt 8) { $c += 8 }; if ($gapPct -gt 20) { $c += 5 }; if ($rvol -gt 5) { $c += 5 }
        $c = [Math]::Min($c, 95)
        $raw.Add("0DTE|S13|Momentum Breakout|LONG|" + $c + "|RSI=" + $rsi + " RVOL=" + $rvol + "x gap=" + $gapPct + "% breakout confirmed.")
    }

    # S15: HOD Breakout (needs intraday)
    if ($hasID) {
        [double]$hod = ($idays | ForEach-Object { [double]$_.h } | Measure-Object -Maximum).Maximum
        if ($curP -ge ($hod * 0.999) -and $rvol -gt 1.5 -and $rsi -gt 55) {
            [int]$c = 70; if ($rvol -gt 3.0) { $c += 7 }
            $raw.Add("0DTE|S15|HOD Breakout|LONG|" + $c + "|Testing HOD " + [Math]::Round($hod,2) + " with RVOL=" + $rvol + "x.")
        }
    }

    # S01: VWAP Bounce Long
    if ($abvVWAP -and $rsi -gt 45 -and $rsi -lt 72) {
        [int]$c = 60; if ($rvol -gt 1.3) { $c += 5 }
        $raw.Add("0DTE|S01|VWAP Bounce Long|LONG|" + $c + "|Price above VWAP " + $vwap + ". ATM calls on VWAP touch.")
    }

    # S07: Overbought Exhaustion Fade
    if ($rsi -gt 74 -and $rvol -gt 0.8) {
        [int]$c = 65; if ($rsi -gt 80) { $c += 8 }; if ($rsi -gt 85) { $c += 5 }
        $raw.Add("0DTE|S07|Overbought Exhaustion|SHORT|" + $c + "|RSI=" + $rsi + " overbought. Fade on first red 5-min candle.")
    }

    # ════════════════════════════════════════
    #  9. FILTER & CONSENSUS
    # ════════════════════════════════════════
    $fired    = @($raw | Where-Object { [int]($_.Split("|")[4]) -ge 55 } | Sort-Object { [int]($_.Split("|")[4]) } -Descending)
    $odSigs   = @($fired | Where-Object { $_.Split("|")[0] -eq "OD"   })
    $dtSigs   = @($fired | Where-Object { $_.Split("|")[0] -eq "0DTE" })
    [int]$topC = if ($fired.Count -gt 0) { [int]($fired[0].Split("|")[4]) } else { 0 }
    [bool]$consensus = ($fired.Count -ge 2) -or ($topC -ge 78)
    [int]$longCt  = @($fired | Where-Object { $_.Split("|")[3] -eq "LONG"  }).Count
    [int]$shortCt = @($fired | Where-Object { $_.Split("|")[3] -eq "SHORT" }).Count
    [string]$dir  = if ($longCt -ge $shortCt) { "LONG" } else { "SHORT" }

    Write-Host ("  Sigs=" + $fired.Count + "  Consensus=" + $consensus + "  Dir=" + $dir + "  TopConf=" + $topC + "%")

    # ════════════════════════════════════════
    #  10. PRICE LEVELS -- pure concatenation, no interpolation
    # ════════════════════════════════════════
    [double]$ep  = $curP
    [double]$aU  = if ($atr -gt 0.01) { $atr } else { $ep * 0.01 }

    # All price strings built with fmt() helper -- never goes inside ""
    [string]$sEntry  = fmt $ep
    [string]$sStop   = ""
    [string]$sT1     = ""
    [string]$sT2     = ""
    [string]$sT3     = ""
    [string]$optType = ""
    [int]$strike     = [int]([Math]::Round($ep / 5.0) * 5)

    if ($dir -eq "LONG") {
        $sStop   = fmt ($ep - $aU)
        $sT1     = fmt ($ep + $aU * 1.0)
        $sT2     = fmt ($ep + $aU * 2.0)
        $sT3     = fmt ($ep + $aU * 3.0)
        $optType = "CALL"
    } else {
        $sStop   = fmt ($ep + $aU)
        $sT1     = fmt ($ep - $aU * 1.0)
        $sT2     = fmt ($ep - $aU * 2.0)
        $sT3     = fmt ($ep - $aU * 3.0)
        $optType = "PUT"
    }

    # ════════════════════════════════════════
    #  11. BUILD TELEGRAM MESSAGE
    #  Rule: all variables used via + concatenation
    #  Dollar signs come from fmt(), never from interpolation
    # ════════════════════════════════════════
    [string]$header  = if ($consensus) { ">>> " + $dir + " ALERT FIRES <<<" } else { "BELOW CONSENSUS - watch" }
    [string]$expDate = $todayDate.Substring(5,2) + "/" + $todayDate.Substring(8,2)

    # Opening Drive block
    $odBlock = "--- OPENING DRIVE SIGNALS ---`n"
    if ($odSigs.Count -gt 0) {
        foreach ($s in $odSigs) {
            $p = $s.Split("|")
            $odBlock += "[" + $p[1] + "] " + $p[2] + "`n"
            $odBlock += "  Direction  : " + $p[3] + "`n"
            $odBlock += "  Confidence : " + $p[4] + "%`n"
            $odBlock += "  Action     : " + $p[5] + "`n"
        }
    } else {
        $odBlock += "  None fired above threshold`n"
    }

    # 0DTE block
    $dtBlock = "--- 0DTE SIGNALS ---`n"
    if ($dtSigs.Count -gt 0) {
        foreach ($s in $dtSigs) {
            $p = $s.Split("|")
            $dtBlock += "[" + $p[1] + "] " + $p[2] + " - 0DTE " + $optType + "S`n"
            $dtBlock += "  Direction  : " + $p[3] + "`n"
            $dtBlock += "  Confidence : " + $p[4] + "%`n"
            $dtBlock += "  Action     : " + $p[5] + "`n"
        }
    } else {
        $dtBlock += "  None fired above threshold`n"
    }

    # Price levels block (only on consensus -- all via concatenation)
    $levBlock = ""
    if ($consensus) {
        $levBlock  = $DV + "`n"
        $levBlock += "DIRECTION  : " + $dir + "`n"
        $levBlock += "ENTRY      : " + $sEntry + "`n"
        $levBlock += "STOP       : " + $sStop + "  (1x ATR hard stop)`n"
        $levBlock += "TARGET 1   : " + $sT1 + "`n"
        $levBlock += "TARGET 2   : " + $sT2 + "`n"
        $levBlock += "TARGET 3   : " + $sT3 + "`n"
        $levBlock += "R:R        : 1:2 minimum`n"
        $levBlock += $DV + "`n"
        $levBlock += "0DTE OPTIONS (exp " + $expDate + "):`n"
        $levBlock += "  Type   : " + $optType + "`n"
        $levBlock += "  Strike : ~" + (fmt $strike) + "`n"
        $levBlock += "  Delta  : 0.40-0.55 ATM`n"
        $levBlock += "  Rule   : Confirm direction at 9:35 AM open`n"
        $levBlock += "  Exit   : T1 OR 11:00 AM whichever first`n"
    }

    # Assemble full message -- NO price variable inside double-quotes
    $msg  = $EQ + "`n"
    $msg += $sym + "  |  " + $header + "`n"
    $msg += $EQ + "`n"
    $msg += "PREV CLOSE : " + (fmt $prevClose) + "`n"
    $msg += "LAST PRICE : " + (fmt $curP) + "`n"
    if ($hasPM) {
        $msg += "PM DATA    : " + $pmDataStr + "`n"
        $msg += "GAP        : " + $gapStr + "`n"
    } else {
        $msg += "PM DATA    : No premarket activity`n"
    }
    $msg += $DV + "`n"
    $msg += "RSI   : " + $rsi + "    TREND : " + $trend + "`n"
    $msg += "EMA9  : " + $ema9 + "   EMA21 : " + $ema21 + "`n"
    $msg += "EMA50 : " + $ema50 + "  ATR   : " + $atr + "`n"
    $msg += "RVOL  : " + $rvol + "x   VWAP  : " + $vwap + "`n"
    $msg += "AbvEMA9:" + $abvEMA9 + "  AbvVWAP:" + $abvVWAP + "`n"
    $msg += $DV + "`n"
    $msg += $odBlock
    $msg += $DV + "`n"
    $msg += $dtBlock
    $msg += $levBlock
    $msg += $EQ + "`n"
    $msg += "Run: " + $nowET.ToString("HH:mm") + " ET  |  " + $sym

    Send-TG $msg

    $statusStr = if ($consensus) { $dir + " ALERT (" + $fired.Count + " sigs, top " + $topC + "%)" } else { "watch (" + $fired.Count + " sig)" }
    $summary  += [PSCustomObject]@{ Sym=$sym; Status=$statusStr; Price=$curP; Gap=$gapStr; RVOL=$rvol; RSI=$rsi; T1=$sT1; T2=$sT2 }
}

# ════════════════════════════════════════════
#  SUMMARY MESSAGE
# ════════════════════════════════════════════
$sumMsg  = $EQ + "`n"
$sumMsg += "SIGNAL SUMMARY -- " + $nowET.ToString("HH:mm") + " ET  " + $todayDate + "`n"
$sumMsg += $EQ + "`n"
foreach ($r in $summary) {
    $sumMsg += $r.Sym.PadRight(5) + " | " + $r.Status + "`n"
    $sumMsg += "      Price=" + (fmt $r.Price) + "  Gap=" + $r.Gap + "  RVOL=" + $r.RVOL + "x  RSI=" + $r.RSI + "`n"
    if ($r.T1 -ne "") {
        $sumMsg += "      T1=" + $r.T1 + "  T2=" + $r.T2 + "`n"
    }
}
$sumMsg += $EQ
Send-TG $sumMsg

Write-Host "`n=== Done. " + $summary.Count + " tickers. ==="
$summary | Format-Table -AutoSize
