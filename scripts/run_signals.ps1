# ============================================================
# Trading Bot Signal Runner  v8
# Signals : Opening Drive | 0DTE | Swing | SCALP | MOMENTUM
# v6 fixes : BBW gate | VWAP Reclaim signal | Capitulation flush detection
#             Daily-trend soft penalty | Failed-breakdown reversal pattern
# v8 fixes : EMA20 (was EMA21) | Prev Day High/Low | Vol 150% hard check
#             Opening Range = 15 min (3 bars) | Consensus threshold 85%
#             Max 3 signals limiter | Risk/Reward 1:2 enforced | PDH/PDL signals
# Branch  : feat/scalp-momentum-signals
#
# RULE: No price variable ever goes inside a double-quoted string.
#       All prices use fmt() + pure concatenation only.
# ============================================================

param(
    [string[]]$Tickers = @("TSLA","NBIS","COST","MSFT","IBM","ORCL","APP","NOW","AVGO","NVDA","AMD"),
    [string]$ApiKey    = "PKUVZN3EUNDEDFIIWS3NHWMKXK",
    [string]$ApiSecret = "2otPyywguF8Xn2mgCwgLifEbP9s8RrbLF9mjVn95EjXo",
    [string]$TgToken   = "8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw",
    [string]$TgChat    = "5979887660"
)

$ErrorActionPreference = "SilentlyContinue"
$hdr     = @{ "APCA-API-KEY-ID" = $ApiKey; "APCA-API-SECRET-KEY" = $ApiSecret }
$baseUrl = "https://data.alpaca.markets"

# ============================================================
#  HELPER FUNCTIONS
# ============================================================

# fmt: always "$NNN.NN" via concatenation -- NEVER embed in ""
function fmt([double]$v) { return "$" + [Math]::Round($v,2).ToString("F2") }
function pct([double]$v) { return [Math]::Round($v,1).ToString() + "%" }

function Get-Bars([string]$s,[string]$tf,[string]$start,[int]$lim=100,[bool]$extHours=$false) {
    # CONFIRMED: Algo Trader account has SIP access.
    # SIP feed returns pre-market bars naturally WITHOUT extended_hours parameter.
    # extended_hours=true causes 400 Bad Request on ALL Alpaca accounts -- wrong param.
    # Pre-market (extHours=true): feed=sip (returns 4AM-8PM ET data)
    # Market hours (extHours=false): feed=iex (faster, real-time 9:30AM-4PM ET)
    $feed = if ($extHours) { "sip" } else { "iex" }
    $url  = $baseUrl+"/v2/stocks/"+$s+"/bars?timeframe="+$tf+"&start="+$start+"&limit="+$lim+"&feed="+$feed+"&sort=asc"
    try { return @((Invoke-RestMethod -Uri $url -Headers $hdr).bars) } catch { return @() }
}

function Get-LastPrice([string]$s,[bool]$extHours=$false) {
    # Pre-market: sip feed (Algo Trader); market hours: iex feed (faster)
    $feed = if ($extHours) { "sip" } else { "iex" }
    try {
        $p = [Math]::Round([double](Invoke-RestMethod -Uri ($baseUrl+"/v2/stocks/"+$s+"/trades/latest?feed="+$feed) -Headers $hdr).trade.p,2)
        if ($p -gt 0) { return $p }
    } catch {}
    try {
        $p = [Math]::Round([double](Invoke-RestMethod -Uri ($baseUrl+"/v2/stocks/"+$s+"/bars/latest?feed="+$feed) -Headers $hdr).bar.c,2)
        if ($p -gt 0) { return $p }
    } catch {}
    return 0.0
}

function Calc-EMA([double[]]$a,[int]$p) {
    if ($a.Count -eq 0) { return 0.0 }
    $k = 2.0/([Math]::Min($p,$a.Count)+1.0)
    $e = $a[0]
    for ($i=1; $i -lt $a.Count; $i++) { $e = $a[$i]*$k + $e*(1.0-$k) }
    return [Math]::Round($e,2)
}

# Calc-EMA-Series: returns full array of EMA values (one per bar)
# Used for Golden Cross / Death Cross detection — need prev bar vs current bar EMA values
function Calc-EMA-Series([double[]]$a,[int]$p) {
    $result = [System.Collections.Generic.List[double]]::new()
    if ($a.Count -eq 0) { return $result }
    $k = 2.0/([Math]::Min($p,$a.Count)+1.0)
    $e = $a[0]
    $result.Add([Math]::Round($e,2))
    for ($i=1; $i -lt $a.Count; $i++) {
        $e = $a[$i]*$k + $e*(1.0-$k)
        $result.Add([Math]::Round($e,2))
    }
    return $result
}

function Calc-RSI([double[]]$c,[int]$p=14) {
    if ($c.Count -lt ($p+1)) { return 50.0 }
    $ag=0.0; $al=0.0
    for ($i=1; $i -le $p; $i++) {
        $d = $c[$i]-$c[$i-1]
        if ($d -gt 0) { $ag+=$d } else { $al+=[Math]::Abs($d) }
    }
    $ag/=$p; $al/=$p
    for ($i=($p+1); $i -lt $c.Count; $i++) {
        $d = $c[$i]-$c[$i-1]
        if ($d -gt 0) { $ag=($ag*($p-1)+$d)/$p; $al=$al*($p-1)/$p }
        else { $al=($al*($p-1)+[Math]::Abs($d))/$p; $ag=$ag*($p-1)/$p }
    }
    if ($al -eq 0) { return 100.0 }
    return [Math]::Round(100.0-100.0/(1.0+$ag/$al),1)
}

function Calc-ATR([object[]]$bars,[int]$p=14) {
    if ($bars.Count -lt 2) { return 0.0 }
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

# Bollinger Band width (normalized): high value = expanded/trending, low = squeeze
function Calc-BBWidth([double[]]$c,[int]$p=20) {
    if ($c.Count -lt $p) { return 0.0 }
    $slice = $c | Select-Object -Last $p
    $mean  = ($slice | Measure-Object -Average).Average
    $std   = [Math]::Sqrt((($slice | ForEach-Object { ($_ - $mean)*($_ - $mean) }) | Measure-Object -Sum).Sum / $p)
    if ($mean -eq 0) { return 0.0 }
    return [Math]::Round(($std * 4.0) / $mean * 100.0, 2)   # BB width as % of price
}

# Average volume of last N 1-min bars (volume velocity)
function Calc-AvgVol1m([object[]]$bars1m,[int]$n=10) {
    if ($bars1m.Count -eq 0) { return 0.0 }
    return [Math]::Round(($bars1m|Select-Object -Last $n|ForEach-Object{[double]$_.v}|Measure-Object -Average).Average,0)
}

function Build-TgBody([string]$chatId, [string]$text) {
    # Build JSON body using raw UTF-8 — avoids PS5.1 ConvertTo-Json emoji expansion
    # PS5.1 converts ✅ -> ✅ (6 chars), 👀 -> 👀 (12 chars) tripling size
    # Manual escaping keeps emoji as real UTF-8 chars (3-4 bytes vs 6-12 in JSON unicode escapes)
    $escaped = $text `
        -replace '\\', '\\\\' `
        -replace '"',  '\\"'  `
        -replace "`r", ''     `
        -replace "`n", '\n'   `
        -replace "`t", '\t'
    return '{"chat_id":"' + $chatId + '","text":"' + $escaped + '"}'
}

function Send-TG([string]$msg) {
    # Telegram limit: 4096 UTF-8 chars of TEXT.
    # Chunk at 3800 raw chars — each chunk's JSON body stays well under 4096 bytes.
    $maxChunk = 3800
    $lines    = $msg -split "`n"
    $chunks   = [System.Collections.Generic.List[string]]::new()
    $current  = ""
    foreach ($line in $lines) {
        $candidate = if ($current -eq "") { $line } else { $current + "`n" + $line }
        if ($candidate.Length -gt $maxChunk) {
            if ($current.Length -gt 0) { $chunks.Add($current) }
            $current = $line
        } else { $current = $candidate }
    }
    if ($current.Length -gt 0) { $chunks.Add($current) }

    $tgUrl = "https://api.telegram.org/bot"+$TgToken+"/sendMessage"
    $total = $chunks.Count
    for ($i = 0; $i -lt $total; $i++) {
        $bodyStr  = Build-TgBody $TgChat $chunks[$i]
        $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($bodyStr)
        try {
            Invoke-RestMethod -Uri $tgUrl -Method POST -Body $bodyBytes `
                -ContentType "application/json; charset=utf-8" | Out-Null
            if ($total -gt 1) { Write-Host ("  TG: Sent part "+($i+1)+"/"+$total) }
        } catch {
            Write-Host ("TG-ERR: "+$_.Exception.Message+" (part "+($i+1)+"/"+$total+", "+$chunks[$i].Length+" chars)")
        }
        Start-Sleep -Milliseconds 900
    }
}

# ============================================================
#  DATE / TIME ANCHORS
# ============================================================

$nowUtc      = [DateTime]::UtcNow.ToUniversalTime()
$todayDate   = $nowUtc.ToString("yyyy-MM-dd")
$nowET       = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($nowUtc,"Eastern Standard Time")
$sessionOpen = [DateTime]::Parse($todayDate+"T13:30:00Z").ToUniversalTime()
$dailyStart  = "2026-01-01T00:00:00Z"
$sessionSt   = $todayDate+"T13:30:00Z"
$pmStart     = $todayDate+"T08:00:00Z"   # 4:00 AM ET (EDT=UTC-4) -- SIP covers from 4AM ET
$expDate     = $todayDate.Substring(5,2)+"/"+$todayDate.Substring(8,2)

# ============================================================
#  MARKET HOURS GATE
#  Allowed window: 8:30 AM – 4:30 PM ET, Monday–Friday only
#  Pre-market OD alerts: 8:30–9:30 AM
#  Intraday signals    : 9:30 AM–4:30 PM
#  Outside window      : exit silently — no alerts sent
# ============================================================
[int]$etHourNow = $nowET.Hour
[int]$etMinNow  = $nowET.Minute
[int]$etTotalMin = $etHourNow * 60 + $etMinNow   # minutes since midnight ET
[int]$dayOfWeek  = [int]$nowET.DayOfWeek          # 0=Sun, 1=Mon, ..., 6=Sat

$isWeekday     = ($dayOfWeek -ge 1 -and $dayOfWeek -le 5)
$isInWindow    = ($etTotalMin -ge 510 -and $etTotalMin -le 990)  # 8:30=510 .. 4:30=990

if (-not $isWeekday -or -not $isInWindow) {
    $reason = if (-not $isWeekday) { "Weekend ("+$nowET.DayOfWeek+")" } `
              else { "Outside market hours ("+$nowET.ToString("HH:mm")+" ET -- allowed 8:30 AM-4:30 PM)" }
    Write-Host ("=== SKIPPED: "+$reason+" ===")
    exit 0
}

# ----------------------------------------------------------------
# $inODWindow MUST be defined here (globally, before the ticker loop)
# It is used in Get-LastPrice and Get-Bars calls inside the loop.
# BUG FIX: previously defined at line ~727 AFTER first use at line ~229
# causing $inODWindow=$false (undefined) = IEX feed used instead of SIP
# ----------------------------------------------------------------
[bool]$inODWindow = ($etTotalMin -ge 510 -and $etTotalMin -le 585)  # 8:30 AM=510 .. 9:45 AM=585

$EQ = "=" * 48
$DV = "-" * 46

Write-Host ("=== Signal Run v8 @ "+$nowET.ToString("HH:mm")+" ET  "+$todayDate+" ===")

$summary = @()

# ============================================================
#  PER-TICKER LOOP
# ============================================================

foreach ($sym in $Tickers) {
    Write-Host ("`n--- "+$sym+" ---")

    # -- Daily bars --
    $daily = @(Get-Bars $sym "1Day" $dailyStart 90)
    if ($daily.Count -lt 10) { Write-Host ("  "+$sym+": insufficient daily data"); continue }

    [double]$prevClose   = [Math]::Round([double]$daily[-1].c,2)
    [double]$prevDayHigh = [Math]::Round([double]$daily[-1].h,2)   # v8: Previous Day High
    [double]$prevDayLow  = [Math]::Round([double]$daily[-1].l,2)   # v8: Previous Day Low
    [double]$avgVol20    = ($daily|Select-Object -Last 20|ForEach-Object{[double]$_.v}|Measure-Object -Average).Average
    [double]$atrDaily  = Calc-ATR $daily 14
    [double[]]$dailyC  = @($daily|ForEach-Object{[double]$_.c})
    [double]$dEMA9     = Calc-EMA $dailyC 9
    [double]$dEMA20    = Calc-EMA $dailyC 20   # v8: EMA20 (was EMA21)
    [double]$dEMA21    = $dEMA20                # alias kept for compatibility
    [double]$dEMA50    = Calc-EMA $dailyC 50
    [double]$dRSI      = Calc-RSI $dailyC 14

    # -- Last trade price -- use SIP feed in pre-market, IEX during market hours
    [double]$curP = Get-LastPrice $sym $inODWindow
    if ($curP -eq 0.0) { $curP = $prevClose }

    # -- Premarket bars -- use SIP feed (IEX has no pre-market data)
    # $extHours=$true switches Get-Bars to feed=sip + extended_hours=true
    $pmBars    = @(Get-Bars $sym "1Min" $pmStart 300 $true)
    [bool]$hasPM   = ($pmBars.Count -gt 0)
    [double]$pmO   = if ($hasPM){[Math]::Round([double]$pmBars[0].o,2)}else{0.0}
    [double]$pmH   = if ($hasPM){($pmBars|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum}else{0.0}
    [double]$pmVol = if ($hasPM){($pmBars|ForEach-Object{[double]$_.v}|Measure-Object -Sum).Sum}else{0.0}
    $pmStr         = if ($hasPM){"O="+$pmO+"  H="+[Math]::Round($pmH,2)+"  Vol="+[int]$pmVol}else{"No premarket activity"}

    # -- Gap --
    [double]$gapRef = if ($hasPM -and $pmO -gt 0){$pmO}else{$curP}
    [double]$gapPct = if ($prevClose -gt 0){[Math]::Round(($gapRef-$prevClose)/$prevClose*100.0,2)}else{0.0}
    $gapStr         = if ($gapPct -ge 0){"+" + $gapPct + "%"}else{$gapPct.ToString()+"%"}

    # -- 5-min intraday bars --
    $idays       = @(Get-Bars $sym "5Min" $sessionSt 80)
    [bool]$hasID = ($idays.Count -ge 2)

    # -- 1-min intraday bars (scalp timeframe) --
    $bars1m        = @(Get-Bars $sym "1Min" $sessionSt 200)
    [bool]$has1m   = ($bars1m.Count -ge 5)

    # -- Blended closes for RSI/EMA (30 daily + intraday) --
    [double[]]$idC   = if ($hasID){@($idays|ForEach-Object{[double]$_.c})}else{@()}
    [double[]]$pmC2  = if ($hasPM){@($pmBars|ForEach-Object{[double]$_.c})}else{@()}
    [double[]]$blend = @($dailyC|Select-Object -Last 30) + $(if($hasID){$idC}elseif($hasPM){$pmC2}else{@()})

    # -- 1-min closes for scalp RSI --
    [double[]]$c1m = if ($has1m){ @($bars1m|ForEach-Object{[double]$_.c}) }else{ $blend }

    # -- Intraday indicators --
    [double]$iATR  = if ($idays.Count -ge 14){Calc-ATR $idays 14}else{0.0}
    [double]$atr   = if ($iATR -gt ($atrDaily*0.3)){$iATR}else{$atrDaily}
    [double]$rsi   = Calc-RSI  $blend 14
    [double]$ema9  = Calc-EMA  $blend 9
    [double]$ema20 = Calc-EMA  $blend 20   # v8: EMA20 (was EMA21)
    [double]$ema21 = $ema20                 # alias kept for compatibility
    [double]$ema50 = Calc-EMA  $blend 50
    [double]$vwap  = if ($hasID){Calc-VWAP $idays}elseif($hasPM){Calc-VWAP $pmBars}else{$prevClose}

    # -- 1-min indicators for scalp --
    [double]$rsi1m   = Calc-RSI $c1m 7          # faster RSI for scalp
    [double]$ema9_1m = Calc-EMA $c1m 9
    # FIX #1: BBW gate -- require 20+ 1-min bars before computing squeeze
    # At open (< 20 bars) BBW=0% is meaningless noise, not a real squeeze
    [bool]$bbwValid  = ($bars1m.Count -ge 20)
    [double]$bbw     = if ($bbwValid) { Calc-BBWidth $c1m 20 } else { 0.0 }

    # Last 3 1-min candles for momentum detection
    [double]$vol1mAvg = Calc-AvgVol1m $bars1m 10
    [double]$vol1mLst = if ($has1m){[double]($bars1m[-1]).v}else{0.0}
    [double]$volSpike = if ($vol1mAvg -gt 0){[Math]::Round($vol1mLst/$vol1mAvg,1)}else{0.0}

    # RVOL (paced vs 20-day avg)
    # Pre-market: use pmVol from SIP bars paced against pre-market elapsed time (4AM-9:30AM = 330 min)
    # Intraday  : use idays volume paced against session elapsed time (390 min full day)
    $minsElapsed = ($nowUtc - $sessionOpen).TotalMinutes
    if ($inODWindow -and $hasPM -and $pmVol -gt 0) {
        # Pre-market window: pace pmVol against pre-market duration (8:30AM start = etTotalMin-510 mins)
        [double]$pmMinsElapsed = [Math]::Max($etTotalMin - 480, 1)  # mins since 8:00 AM ET
        [double]$pmFrac        = [Math]::Min($pmMinsElapsed / 90.0, 1.0)  # 90 min pre-market window
        [double]$todayVol      = $pmVol
        [double]$fracEl        = [Math]::Max($pmFrac, 0.001)
    } else {
        [double]$fracEl   = [Math]::Max([Math]::Min($minsElapsed/390.0,1.0),0.001)
        [double]$todayVol = if ($hasID){($idays|ForEach-Object{[double]$_.v}|Measure-Object -Sum).Sum}
                            elseif($hasPM){$pmVol}else{0.0}
    }
    [double]$rvol  = if($avgVol20 -gt 0 -and $todayVol -gt 0){[Math]::Round(($todayVol/$fracEl)/$avgVol20,2)}else{0.0}

    [bool]$abvEMA9  = ($curP -gt $ema9)
    [bool]$abvVWAP  = ($curP -gt $vwap -and $vwap -gt 0)
    [string]$trend  = if($ema9 -gt $ema21 -and $ema21 -gt $ema50){"UP"}elseif($ema9 -lt $ema21){"DOWN"}else{"NEUTRAL"}
    [string]$dTrend = if($dEMA9 -gt $dEMA21 -and $dEMA21 -gt $dEMA50){"UPTREND"}
                      elseif($dEMA9 -lt $dEMA21){"DOWNTREND"}else{"SIDEWAYS"}

    Write-Host ("  Price="+$curP+"  RSI="+$rsi+"  RSI1m="+$rsi1m+"  EMA9="+$ema9+"  ATR="+$atr)
    [string]$bbwDisplay = if ($bbwValid) { ([string]$bbw)+"%  " } else { "N/A ("+$bars1m.Count+" bars)" }
    Write-Host ("  RVOL="+$rvol+"x  VWAP="+$vwap+"  BBW="+$bbwDisplay+"  VolSpike="+$volSpike+"x  Trend="+$trend)
    Write-Host ("  Daily: dRSI="+$dRSI+"  dEMA9="+$dEMA9+"  dTrend="+$dTrend)

    # ============================================================
    #  STRATEGY ENGINE
    #  Format: TYPE|ID|NAME|DIR|OPT_TYPE|CONF|ACTION_NOTE
    # ============================================================
    $raw = [System.Collections.Generic.List[string]]::new()

    # ---- A. OPENING DRIVE SIGNALS (OD) -------------------------

    # S19A: Gap Breakout Opening Drive  [v8: RVOL threshold raised to 1.5x = 150% volume]
    if ($gapPct -gt 3.0 -and $rvol -gt 1.5 -and $rsi -gt 52 -and $abvVWAP) {
        [int]$c=70
        if($gapPct -gt 10){$c+=10};if($gapPct -gt 20){$c+=5};if($gapPct -gt 50){$c+=5}
        if($rvol -gt 3.0){$c+=8};if($rvol -gt 8.0){$c+=5}
        if($rsi -gt 60){$c+=5};if($hasPM){$c+=2}
        $c=[Math]::Min($c,98)
        $raw.Add("OD|S19A|Opening Drive Gap Breakout|LONG|CALL|"+$c+"|Gap="+$gapPct+"% RVOL="+$rvol+"x. Enter on first 5-min pullback to VWAP.")
    }

    # S19B: Opening Drive Pullback Entry
    if ($gapPct -gt 2.0 -and $abvVWAP -and $rsi -gt 46 -and $rsi -lt 68 -and $rvol -gt 1.0) {
        [int]$c=65; if($rvol -gt 2.0){$c+=5}; if($hasPM){$c+=3}
        $raw.Add("OD|S19B|Opening Drive Pullback|LONG|CALL|"+$c+"|Pullback to VWAP "+$vwap+". Buy dip in opening drive window 9:30-9:50 AM.")
    }

    # S08: Opening Range Breakout / Breakdown
    # v8: OR now uses first 3 × 5-min bars = 15 minutes (was 2 bars = 10 min)
    # v8: RVOL threshold raised to 1.5x = 150% volume requirement
    if ($hasID -and $idays.Count -ge 3) {
        [double]$orH = ($idays|Select-Object -First 3|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum
        [double]$orL = ($idays|Select-Object -First 3|ForEach-Object{[double]$_.l}|Measure-Object -Minimum).Minimum
        if ($curP -gt $orH -and $rvol -gt 1.5) {
            [int]$c=74; if($rvol -gt 2.0){$c+=6}; if($rvol -gt 5.0){$c+=4}
            $raw.Add("OD|S08|Opening Range Breakout (15min)|LONG|CALL|"+$c+"|Broke 15-min ORB high "+[Math]::Round($orH,2)+" RVOL="+$rvol+"x (>=150% vol confirmed).")
        } elseif ($curP -lt $orL -and $rvol -gt 1.5) {
            [int]$c=72; if($rvol -gt 2.0){$c+=6}
            $raw.Add("OD|S08|Opening Range Breakdown (15min)|SHORT|PUT|"+$c+"|Broke 15-min ORB low "+[Math]::Round($orL,2)+" RVOL="+$rvol+"x (>=150% vol confirmed).")
        }
    }

    # S20: VWAP Reclaim -- price crossed from below VWAP to above (reversal confirmation)
    # FIX #3: Detect VWAP reclaim cross -- the single most important reversal signal
    # Uses last 2 intraday 5-min closes: prev below VWAP, current above VWAP
    if ($hasID -and $idays.Count -ge 2 -and $vwap -gt 0) {
        [double]$prevClose5m = [double]($idays[-2]).c
        [double]$currClose5m = [double]($idays[-1]).c
        [bool]$vwapReclaim   = ($prevClose5m -lt $vwap) -and ($currClose5m -gt $vwap)
        if ($vwapReclaim -and $rvol -gt 0.8) {
            [int]$c=80
            if ($rvol -gt 1.5){$c+=5}; if ($rsi1m -gt 50){$c+=4}; if ($rvol -gt 3.0){$c+=4}
            $c=[Math]::Min($c,95)
            $raw.Add("OD|S20|VWAP Reclaim Reversal|LONG|CALL|"+$c+"|Price crossed ABOVE VWAP "+$vwap+" (prev close "+[Math]::Round($prevClose5m,2)+" -- now "+[Math]::Round($currClose5m,2)+"). RVOL="+$rvol+"x. HIGH-PRIORITY reversal signal -- enter LONG on next 1-min close above VWAP.")
        }
    }

    # S21: Failed Breakdown / Gap Fade Reversal
    # FIX #5: Detect gap-up that dipped below prior close then recovered -- bearish trap
    # Pattern: gapped up BUT price fell below prevClose at some point, now recovering above VWAP
    if ($hasID -and $gapPct -gt 0.1 -and $abvVWAP -and $vwap -gt 0) {
        [double]$sessionLow = ($idays|ForEach-Object{[double]$_.l}|Measure-Object -Minimum).Minimum
        [bool]$dippedBelowPrevClose = ($sessionLow -lt $prevClose * 0.999)   # touched or broke prior close
        [bool]$nowRecovered         = ($curP -gt $vwap) -and ($curP -gt $prevClose * 0.998)
        if ($dippedBelowPrevClose -and $nowRecovered -and $rsi1m -gt 45) {
            [int]$c=75; if($rvol -gt 1.5){$c+=5}; if($rsi -gt 52){$c+=4}
            $c=[Math]::Min($c,92)
            $raw.Add("OD|S21|Failed Breakdown Reversal|LONG|CALL|"+$c+"|Gap up "+$gapPct+"% but session low "+[Math]::Round($sessionLow,2)+" breached prev close "+$prevClose+". Price now recovered above VWAP "+$vwap+". FAILED BREAKDOWN = bullish trap sprung -- LONG.")
        }
    }

    # S22: Previous Day High Breakout (v8)
    if ($prevDayHigh -gt 0 -and $curP -gt $prevDayHigh -and $rvol -gt 1.5 -and $abvVWAP) {
        [int]$c=78; if($rvol -gt 2.5){$c+=7}; if($rvol -gt 5.0){$c+=5}
        $c=[Math]::Min($c,95)
        $raw.Add("OD|S22|Prev Day High Breakout|LONG|CALL|"+$c+"|Cleared PDH="+$prevDayHigh+" RVOL="+$rvol+"x above VWAP. Key resistance broken -- continuation LONG.")
    }

    # S23: Previous Day Low Breakdown (v8)
    if ($prevDayLow -gt 0 -and $curP -lt $prevDayLow -and $rvol -gt 1.5 -and (-not $abvVWAP)) {
        [int]$c=76; if($rvol -gt 2.5){$c+=7}
        $c=[Math]::Min($c,95)
        $raw.Add("OD|S23|Prev Day Low Breakdown|SHORT|PUT|"+$c+"|Broke PDL="+$prevDayLow+" RVOL="+$rvol+"x below VWAP. Key support broken -- continuation SHORT.")
    }

    # ---- B. 0DTE SIGNALS ---------------------------------------

    # S18: 9-EMA Cross Momentum
    if ($atr -gt 0) {
        [double]$e9d = [Math]::Round([Math]::Abs($curP-$ema9)/$atr,2)
        if ($abvEMA9 -and $e9d -ge 0.3 -and $e9d -lt 5.0 -and $rsi -gt 48 -and $rsi -lt 80 -and $trend -eq "UP") {
            [int]$c=78; if($e9d -lt 1.5){$c+=5}; if($rsi -gt 55 -and $rsi -lt 72){$c+=4}
            $c=[Math]::Min($c,95)
            $raw.Add("0DTE|S18|9-EMA Cross Momentum|LONG|CALL|"+$c+"|Price "+$e9d+"x ATR above EMA9="+$ema9+". Enter 0DTE CALL on EMA9 retest.")
        } elseif (-not $abvEMA9 -and $e9d -ge 0.3 -and $e9d -lt 5.0 -and $rsi -gt 20 -and $rsi -lt 52 -and $trend -eq "DOWN") {
            [int]$c=75
            $raw.Add("0DTE|S18|9-EMA Cross Momentum|SHORT|PUT|"+$c+"|Price "+$e9d+"x ATR below EMA9="+$ema9+". Enter 0DTE PUT on bounce to EMA9.")
        }
    }

    # S16: VWAP Momentum Thrust
    if ($abvVWAP -and $rvol -gt 1.5 -and $rsi -gt 52 -and $rsi -lt 76) {
        [int]$c=68; if($rvol -gt 3.0){$c+=7}; if($rvol -gt 8.0){$c+=5}
        $raw.Add("0DTE|S16|VWAP Momentum Thrust|LONG|CALL|"+$c+"|RVOL="+$rvol+"x above VWAP="+$vwap+". 0DTE ATM CALL.")
    }

    # S17: EMA9/21 Squeeze
    if ($abvEMA9 -and $ema9 -gt $ema21 -and $rsi -gt 50 -and $rsi -lt 73 -and $rvol -gt 1.0) {
        [int]$c=65; if($rvol -gt 2.0){$c+=5}
        $raw.Add("0DTE|S17|EMA9-21 Trend Squeeze|LONG|CALL|"+$c+"|EMA9="+$ema9+" > EMA21="+$ema21+". Trend intact -- 0DTE CALL.")
    }

    # S13: Momentum Breakout 0DTE
    if ($rsi -gt 60 -and $rvol -gt 2.0 -and $abvVWAP -and $gapPct -gt 2.0) {
        [int]$c=72; if($gapPct -gt 8){$c+=8}; if($gapPct -gt 20){$c+=5}; if($rvol -gt 5){$c+=5}
        $c=[Math]::Min($c,95)
        $raw.Add("0DTE|S13|Momentum Breakout|LONG|CALL|"+$c+"|RSI="+$rsi+" RVOL="+$rvol+"x Gap="+$gapPct+"% breakout. 0DTE CALL.")
    }

    # S15: HOD Breakout
    if ($hasID) {
        [double]$hod = ($idays|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum
        if ($curP -ge ($hod*0.999) -and $rvol -gt 1.5 -and $rsi -gt 55) {
            [int]$c=70; if($rvol -gt 3.0){$c+=7}
            $raw.Add("0DTE|S15|HOD Breakout|LONG|CALL|"+$c+"|Testing HOD="+[Math]::Round($hod,2)+" RVOL="+$rvol+"x. 0DTE CALL breakout.")
        }
    }

    # S01: VWAP Bounce
    if ($abvVWAP -and $rsi -gt 45 -and $rsi -lt 72) {
        [int]$c=60; if($rvol -gt 1.3){$c+=5}
        $raw.Add("0DTE|S01|VWAP Bounce|LONG|CALL|"+$c+"|Holding above VWAP="+$vwap+". 0DTE ATM CALL on VWAP touch.")
    }

    # S07: Overbought Fade (PUT)
    if ($rsi -gt 74 -and $rvol -gt 0.8) {
        [int]$c=65; if($rsi -gt 80){$c+=8}; if($rsi -gt 85){$c+=5}
        $raw.Add("0DTE|S07|Overbought Exhaustion Fade|SHORT|PUT|"+$c+"|RSI="+$rsi+" overbought. 0DTE PUT -- wait for first red 5-min candle.")
    }

    # ---- C. SWING SIGNALS (daily timeframe) --------------------

    # SW01: Uptrend Continuation
    if ($dTrend -eq "UPTREND" -and $dRSI -gt 40 -and $dRSI -lt 65 -and $curP -gt $dEMA21) {
        [int]$c=68; if($dRSI -gt 50){$c+=5}; if($curP -gt $dEMA9){$c+=5}
        $raw.Add("SWING|SW01|Swing Uptrend Continuation|LONG|NA|"+$c+"|Daily EMA9="+$dEMA9+" > EMA21="+$dEMA21+". RSI="+$dRSI+" buyable zone. Hold 3-5 days.")
    }

    # SW02: Oversold Bounce
    if ($dRSI -lt 35 -and $curP -gt ($dEMA50*0.97) -and $curP -lt ($dEMA50*1.05)) {
        [int]$c=65; if($dRSI -lt 28){$c+=8}
        $raw.Add("SWING|SW02|Swing Oversold Bounce|LONG|NA|"+$c+"|Daily RSI="+$dRSI+" oversold near EMA50="+$dEMA50+". Target EMA21="+$dEMA21+".")
    }

    # SW03: Gap-and-Hold
    if ($gapPct -gt 5.0 -and $curP -gt $dEMA9 -and $rvol -gt 1.5 -and $dRSI -gt 45) {
        [int]$c=72; if($gapPct -gt 15){$c+=8}; if($gapPct -gt 30){$c+=5}; if($rvol -gt 3){$c+=5}
        $c=[Math]::Min($c,95)
        $raw.Add("SWING|SW03|Swing Gap-and-Hold|LONG|NA|"+$c+"|Gap="+$gapPct+"% holding above dEMA9="+$dEMA9+". Hold 2-4 days.")
    }

    # SW04: Downtrend Short
    if ($dTrend -eq "DOWNTREND" -and $dRSI -gt 55 -and $dRSI -lt 70 -and $curP -lt $dEMA21) {
        [int]$c=65; if($dRSI -gt 60){$c+=5}
        $raw.Add("SWING|SW04|Swing Downtrend Short|SHORT|NA|"+$c+"|Daily downtrend. RSI="+$dRSI+" bounce-to-sell below EMA21="+$dEMA21+".")
    }

    # SW05: 20-Day Momentum Breakout
    if ($daily.Count -ge 20) {
        [double]$high20 = ($daily|Select-Object -Last 20|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum
        if ($curP -ge ($high20*0.998) -and $rvol -gt 1.5 -and $dRSI -gt 50) {
            [int]$c=75; if($rvol -gt 3){$c+=5}; if($gapPct -gt 5){$c+=5}
            $raw.Add("SWING|SW05|Swing Momentum Breakout|LONG|NA|"+$c+"|At/near 20-day high="+[Math]::Round($high20,2)+" RVOL="+$rvol+"x. Hold 5-10 days.")
        }
    }

    # ---- D. SCALP SIGNALS (1-5 min, quick in/out) --------------

    # SC01: VWAP Scalp -- price tight to VWAP + 1-min volume spike
    if ($vwap -gt 0) {
        [double]$vwapDist = [Math]::Round([Math]::Abs($curP-$vwap)/$atr,2)
        if ($vwapDist -lt 0.3 -and $volSpike -gt 1.8 -and $rsi1m -gt 45 -and $rsi1m -lt 70) {
            [int]$c=72
            if ($curP -gt $vwap) {
                if($volSpike -gt 3.0){$c+=8}; if($rsi1m -gt 55){$c+=5}
                $raw.Add("SCALP|SC01|VWAP Scalp Long|LONG|CALL|"+$c+"|Price "+$vwapDist+"x ATR from VWAP="+$vwap+". Vol spike "+$volSpike+"x. Scalp CALL 1-3 min target "+[Math]::Round($atr*0.5,2)+" pts.")
            } else {
                if($volSpike -gt 3.0){$c+=8}; if($rsi1m -lt 45){$c+=5}
                $raw.Add("SCALP|SC01|VWAP Scalp Short|SHORT|PUT|"+$c+"|Price "+$vwapDist+"x ATR below VWAP="+$vwap+". Vol spike "+$volSpike+"x. Scalp PUT 1-3 min target "+[Math]::Round($atr*0.5,2)+" pts.")
            }
        }
    }

    # SC02: EMA9 1-min Scalp -- price crossing EMA9 on 1-min with momentum
    if ($has1m -and $atr -gt 0) {
        [double]$e9dist1m = [Math]::Round([Math]::Abs($curP - $ema9_1m) / $atr, 2)
        if ($e9dist1m -lt 0.4 -and $volSpike -gt 1.5) {
            [int]$c=68
            if ($curP -gt $ema9_1m -and $rsi1m -gt 50) {
                if($rvol -gt 2.0){$c+=7}
                $raw.Add("SCALP|SC02|EMA9 1-min Scalp Long|LONG|CALL|"+$c+"|1-min EMA9="+$ema9_1m+" crossed. Vol spike "+$volSpike+"x. Scalp CALL -- exit within 2-4 candles.")
            } elseif ($curP -lt $ema9_1m -and $rsi1m -lt 50) {
                if($rvol -gt 2.0){$c+=7}
                $raw.Add("SCALP|SC02|EMA9 1-min Scalp Short|SHORT|PUT|"+$c+"|1-min EMA9="+$ema9_1m+" rejected. Vol spike "+$volSpike+"x. Scalp PUT -- exit within 2-4 candles.")
            }
        }
    }

    # SC03: HOD/LOD Scalp -- price testing intraday high or low
    if ($hasID) {
        [double]$hodS = ($idays|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum
        [double]$lodS = ($idays|ForEach-Object{[double]$_.l}|Measure-Object -Minimum).Minimum
        [double]$distH = [Math]::Round(($hodS - $curP)/$atr, 2)
        [double]$distL = [Math]::Round(($curP - $lodS)/$atr, 2)
        if ($distH -lt 0.15 -and $rvol -gt 1.5 -and $rsi1m -gt 55) {
            [int]$c=74; if($rvol -gt 3.0){$c+=6}
            $raw.Add("SCALP|SC03|HOD Breakout Scalp|LONG|CALL|"+$c+"|Within 0.15 ATR of HOD="+[Math]::Round($hodS,2)+". RVOL="+$rvol+"x. Scalp CALL on HOD break -- 5-10 min hold.")
        } elseif ($distL -lt 0.15 -and $rvol -gt 1.5 -and $rsi1m -lt 45) {
            [int]$c=72; if($rvol -gt 3.0){$c+=6}
            $raw.Add("SCALP|SC03|LOD Breakdown Scalp|SHORT|PUT|"+$c+"|Within 0.15 ATR of LOD="+[Math]::Round($lodS,2)+". RVOL="+$rvol+"x. Scalp PUT on LOD break -- 5-10 min hold.")
        }
    }

    # SC04: BB Squeeze Scalp -- tight Bollinger band squeeze about to expand
    # FIX #1 applied: only fire when bbwValid=true (20+ bars). BBW=0% at open is NOT a squeeze.
    if ($bbwValid -and $bbw -gt 0 -and $bbw -lt 1.5 -and $rvol -gt 1.2) {
        [int]$c=70; if($bbw -lt 0.8){$c+=8}; if($rvol -gt 2.5){$c+=5}
        [string]$squeezeDir = if($curP -gt $vwap){"LONG"}else{"SHORT"}
        [string]$squeezeOpt = if($squeezeDir -eq "LONG"){"CALL"}else{"PUT"}
        $raw.Add("SCALP|SC04|BB Squeeze Breakout Scalp|"+$squeezeDir+"|"+$squeezeOpt+"|"+$c+"|BB width="+$bbw+"% ("+$bars1m.Count+"-bar confirmed squeeze). RVOL="+$rvol+"x. Scalp "+$squeezeOpt+" -- explosive move expected in 1-5 min.")
    }

    # ---- E. MOMENTUM SIGNALS -----------------------------------

    # MO01: Volume Surge Momentum -- RVOL > 2.5x with trend continuation
    # FIX #2: Detect capitulation flush BEFORE firing SHORT momentum
    # Capitulation = high RVOL below VWAP + RSI1m oversold + volume drying on recent 1-min bars
    [bool]$volDrying   = ($vol1mAvg -gt 0) -and ($vol1mLst -lt $vol1mAvg * 0.65)   # last bar < 65% of avg = drying
    [bool]$rsi1mFlush  = ($rsi1m -lt 38)                                             # 1-min RSI oversold
    [bool]$capitulation = ((-not $abvVWAP) -and $rvol -gt 2.5 -and $volDrying -and $rsi1mFlush)

    if ($rvol -gt 2.5 -and $rsi -gt 50 -and $trend -eq "UP" -and $abvVWAP) {
        [int]$c=75; if($rvol -gt 4.0){$c+=8}; if($rvol -gt 8.0){$c+=5}; if($rsi -gt 60){$c+=4}
        $c=[Math]::Min($c,95)
        $raw.Add("MOMENTUM|MO01|Volume Surge Momentum|LONG|CALL|"+$c+"|RVOL="+$rvol+"x (abnormal volume surge). Trend UP above VWAP="+$vwap+". Ride momentum -- CALL.")
    } elseif ($capitulation) {
        # Capitulation flush: do NOT fire SHORT -- fire a WATCH reversal note instead
        $raw.Add("0DTE|S17B|Capitulation Flush Watch|LONG|CALL|62|RVOL="+$rvol+"x flush below VWAP but vol drying ("+$vol1mLst+" vs avg "+[Math]::Round($vol1mAvg,0)+") + RSI1m="+$rsi1m+" oversold. POSSIBLE REVERSAL -- wait for VWAP reclaim "+$vwap+" before entry.")
    } elseif ($rvol -gt 2.5 -and $rsi -lt 50 -and $trend -eq "DOWN" -and (-not $abvVWAP) -and (-not $capitulation)) {
        [int]$c=73; if($rvol -gt 4.0){$c+=8}
        $c=[Math]::Min($c,95)
        $raw.Add("MOMENTUM|MO01|Volume Surge Momentum|SHORT|PUT|"+$c+"|RVOL="+$rvol+"x surge below VWAP. Trend DOWN. Confirmed continuation (vol NOT drying) -- PUT.")
    }

    # MO02: RSI Momentum Thrust -- RSI 60-76 with full EMA alignment
    if ($rsi -gt 60 -and $rsi -lt 77 -and $ema9 -gt $ema21 -and $ema21 -gt $ema50 -and $abvVWAP) {
        [int]$c=78; if($rsi -gt 68){$c+=5}; if($rvol -gt 2.0){$c+=4}
        $c=[Math]::Min($c,95)
        $raw.Add("MOMENTUM|MO02|RSI Momentum Thrust|LONG|CALL|"+$c+"|RSI="+$rsi+" in momentum zone. EMA9 > EMA21 > EMA50 fully aligned. Strong CALL -- momentum continuation.")
    }

    # MO03: Gap Momentum -- large gap with intraday confirmation
    if ($gapPct -gt 8.0 -and $rvol -gt 1.5 -and $rsi -gt 50 -and $curP -gt $dEMA9) {
        [int]$c=74; if($gapPct -gt 20){$c+=8}; if($gapPct -gt 40){$c+=5}; if($rvol -gt 3.0){$c+=5}
        $c=[Math]::Min($c,95)
        $raw.Add("MOMENTUM|MO03|Gap Momentum Play|LONG|CALL|"+$c+"|Gap="+$gapPct+"% confirmed above dEMA9="+$dEMA9+". RVOL="+$rvol+"x. Momentum CALL -- trail stop at VWAP.")
    }

    # MO04: Multi-Timeframe Momentum Alignment
    # All 3 timeframes (1-min RSI, intraday RSI, daily RSI) bullish simultaneously
    if ($rsi1m -gt 58 -and $rsi -gt 55 -and $dRSI -gt 52 -and $abvVWAP -and $trend -eq "UP") {
        [int]$c=80; if($rvol -gt 2.0){$c+=5}; if($dTrend -eq "UPTREND"){$c+=5}
        $c=[Math]::Min($c,95)
        $raw.Add("MOMENTUM|MO04|Multi-Timeframe Momentum|LONG|CALL|"+$c+"|1m-RSI="+$rsi1m+" + Intraday-RSI="+$rsi+" + Daily-RSI="+$dRSI+" all bullish. Strongest momentum signal -- CALL.")
    } elseif ($rsi1m -lt 42 -and $rsi -lt 45 -and $dRSI -lt 48 -and (-not $abvVWAP) -and $trend -eq "DOWN") {
        [int]$c=78; if($rvol -gt 2.0){$c+=5}
        $c=[Math]::Min($c,95)
        $raw.Add("MOMENTUM|MO04|Multi-Timeframe Momentum|SHORT|PUT|"+$c+"|1m-RSI="+$rsi1m+" + Intraday-RSI="+$rsi+" + Daily-RSI="+$dRSI+" all bearish. Strongest momentum signal -- PUT.")
    }

    # MO05: Intraday Acceleration -- back-to-back strong 5-min candles
    if ($hasID -and $idays.Count -ge 3) {
        $last3  = $idays | Select-Object -Last 3
        [double]$c1body = [Math]::Abs([double]$last3[0].c - [double]$last3[0].o)
        [double]$c2body = [Math]::Abs([double]$last3[1].c - [double]$last3[1].o)
        [double]$c3body = [Math]::Abs([double]$last3[2].c - [double]$last3[2].o)
        [bool]$allGreen = ([double]$last3[0].c -gt [double]$last3[0].o) -and
                          ([double]$last3[1].c -gt [double]$last3[1].o) -and
                          ([double]$last3[2].c -gt [double]$last3[2].o)
        [bool]$allRed   = ([double]$last3[0].c -lt [double]$last3[0].o) -and
                          ([double]$last3[1].c -lt [double]$last3[1].o) -and
                          ([double]$last3[2].c -lt [double]$last3[2].o)
        [bool]$accel    = ($c2body -gt $c1body*1.1) -and ($c3body -gt $c2body*1.1)
        if ($allGreen -and $accel -and $rvol -gt 1.2) {
            [int]$c=73; if($rvol -gt 2.0){$c+=6}
            $raw.Add("MOMENTUM|MO05|Intraday Acceleration|LONG|CALL|"+$c+"|3 consecutive expanding green 5-min candles. Acceleration pattern detected. Momentum CALL.")
        } elseif ($allRed -and $accel -and $rvol -gt 1.2) {
            [int]$c=71; if($rvol -gt 2.0){$c+=6}
            $raw.Add("MOMENTUM|MO05|Intraday Acceleration|SHORT|PUT|"+$c+"|3 consecutive expanding red 5-min candles. Acceleration pattern detected. Momentum PUT.")
        }
    }

    # ============================================================
    #  FILTER + CONSENSUS
    # ============================================================
    $fired   = @($raw|Where-Object{[int]($_.Split("|")[5]) -ge 55}|Sort-Object{[int]($_.Split("|")[5])} -Descending)
    $odSigs  = @($fired|Where-Object{$_.Split("|")[0] -eq "OD"})
    $dtSigs  = @($fired|Where-Object{$_.Split("|")[0] -eq "0DTE"})
    $swSigs  = @($fired|Where-Object{$_.Split("|")[0] -eq "SWING"})
    $scSigs  = @($fired|Where-Object{$_.Split("|")[0] -eq "SCALP"})
    $moSigs  = @($fired|Where-Object{$_.Split("|")[0] -eq "MOMENTUM"})

    [int]$topOD  = if($odSigs.Count -gt 0){[int]($odSigs[0].Split("|")[5])}else{0}
    [int]$topDT  = if($dtSigs.Count -gt 0){[int]($dtSigs[0].Split("|")[5])}else{0}
    [int]$topSW  = if($swSigs.Count -gt 0){[int]($swSigs[0].Split("|")[5])}else{0}
    [int]$topSC  = if($scSigs.Count -gt 0){[int]($scSigs[0].Split("|")[5])}else{0}
    [int]$topMO  = if($moSigs.Count -gt 0){[int]($moSigs[0].Split("|")[5])}else{0}
    [int]$topAll = [Math]::Max($topOD,[Math]::Max($topDT,[Math]::Max($topSW,[Math]::Max($topSC,$topMO))))

    # FIX #4: Daily trend SOFT PENALTY -- reduce confidence, do NOT block signals
    # A daily downtrend is context, not a hard block. Intraday reversals happen every session.
    # Penalty: -7% confidence on LONG signals when daily=DOWNTREND; -7% on SHORT when UPTREND
    $firedAdjusted = [System.Collections.Generic.List[string]]::new()
    foreach ($sig in $fired) {
        $parts   = $sig.Split("|")
        [int]$sigConf = [int]$parts[5]
        [string]$sigDir = $parts[3]
        if ($dTrend -eq "DOWNTREND" -and $sigDir -eq "LONG") {
            $sigConf = [Math]::Max(55, $sigConf - 7)   # small penalty, floor at 55
        } elseif ($dTrend -eq "UPTREND" -and $sigDir -eq "SHORT") {
            $sigConf = [Math]::Max(55, $sigConf - 7)
        }
        $parts[5] = $sigConf.ToString()
        $firedAdjusted.Add($parts -join "|")
    }
    $fired = @($firedAdjusted)

    # v8: Consensus threshold raised to 85% (was 78%) per filtering rules
    [bool]$consensus = ($fired.Count -ge 2) -or ($topAll -ge 85)

    # v8: Max 3 active signals — keep only top 3 by confidence score
    if ($fired.Count -gt 3) {
        $fired = @($fired | Select-Object -First 3)
        # Re-categorise after trim
        $odSigs = @($fired|Where-Object{$_.Split("|")[0] -eq "OD"})
        $dtSigs = @($fired|Where-Object{$_.Split("|")[0] -eq "0DTE"})
        $swSigs = @($fired|Where-Object{$_.Split("|")[0] -eq "SWING"})
        $scSigs = @($fired|Where-Object{$_.Split("|")[0] -eq "SCALP"})
        $moSigs = @($fired|Where-Object{$_.Split("|")[0] -eq "MOMENTUM"})
    }

    [int]$longCt  = @($fired|Where-Object{$_.Split("|")[3] -eq "LONG"}).Count
    [int]$shortCt = @($fired|Where-Object{$_.Split("|")[3] -eq "SHORT"}).Count
    [string]$dir  = if($longCt -ge $shortCt){"LONG"}else{"SHORT"}

    Write-Host ("  Sigs: OD="+$odSigs.Count+" 0DTE="+$dtSigs.Count+" SW="+$swSigs.Count+" SC="+$scSigs.Count+" MO="+$moSigs.Count+"  TopConf="+$topAll+"%  Consensus="+$consensus+"  Dir="+$dir)

    # ============================================================
    #  PRICE LEVELS  (v8: Risk/Reward 1:2 minimum enforced)
    #  Rule: T1 must be >= 2x the stop distance from entry
    #  If ATR-based T1 does not meet 1:2, levels are adjusted up/down
    # ============================================================
    [double]$ep  = $curP
    [double]$aU  = if($atr -gt 0.01){$atr}else{$ep*0.01}
    [double]$aUs = if($atr -gt 0.01){$atr*0.3}else{$ep*0.003}   # scalp: 0.3x ATR

    # v8: R/R check -- stop = 0.5x ATR, T1 must be >= 1.0x ATR (2:1 ratio from 0.5x stop)
    # If ATR < minimum viable R/R distance, skip signal display but still alert
    [double]$rrStop     = $aU * 0.5      # risk = 0.5x ATR
    [double]$rrMinT1    = $aU * 1.0      # reward = 1.0x ATR = 2:1 ratio
    [bool]$rrValid      = ($rrMinT1 / [Math]::Max($rrStop,0.01)) -ge 2.0   # always true with ATR math but explicit check

    [string]$sEntry=""; [string]$sStop=""; [string]$sT1=""; [string]$sT2=""; [string]$sT3=""
    [string]$scStop=""; [string]$scT1=""; [string]$scT2=""
    [string]$moStop=""; [string]$moT1=""; [string]$moT2=""
    [string]$swStop=""; [string]$swT1=""; [string]$swT2=""
    [string]$optType="CALL"
    [int]$strike=[int]([Math]::Round($ep/5.0)*5)

    if ($dir -eq "LONG") {
        $sEntry  = fmt $ep
        $sStop   = fmt ($ep - $aU)
        $sT1     = fmt ($ep + $aU*1.0)
        $sT2     = fmt ($ep + $aU*2.0)
        $sT3     = fmt ($ep + $aU*3.0)
        # Scalp: tight 0.3x ATR targets
        $scStop  = fmt ($ep - $aUs)
        $scT1    = fmt ($ep + $aUs)
        $scT2    = fmt ($ep + $aUs*2.0)
        # Momentum: 1.5x and 2.5x ATR
        $moStop  = fmt ($ep - $aU*0.8)
        $moT1    = fmt ($ep + $aU*1.5)
        $moT2    = fmt ($ep + $aU*2.5)
        # Swing: 2x and 4x daily ATR
        $swStop  = fmt ($ep - $atrDaily*1.5)
        $swT1    = fmt ($ep + $atrDaily*2.0)
        $swT2    = fmt ($ep + $atrDaily*4.0)
        $optType = "CALL"
    } else {
        $sEntry  = fmt $ep
        $sStop   = fmt ($ep + $aU)
        $sT1     = fmt ($ep - $aU*1.0)
        $sT2     = fmt ($ep - $aU*2.0)
        $sT3     = fmt ($ep - $aU*3.0)
        $scStop  = fmt ($ep + $aUs)
        $scT1    = fmt ($ep - $aUs)
        $scT2    = fmt ($ep - $aUs*2.0)
        $moStop  = fmt ($ep + $aU*0.8)
        $moT1    = fmt ($ep - $aU*1.5)
        $moT2    = fmt ($ep - $aU*2.5)
        $swStop  = fmt ($ep + $atrDaily*1.5)
        $swT1    = fmt ($ep - $atrDaily*2.0)
        $swT2    = fmt ($ep - $atrDaily*4.0)
        $optType = "PUT"
    }

    # ============================================================
    #  BUILD TELEGRAM MESSAGE
    # ============================================================
    [string]$alertHdr = if($consensus){">>> "+$dir+" ALERT FIRES <<<"}else{"WATCH -- below consensus threshold"}

    # ---- Time-window check for Opening Drive ----
    # $inODWindow is set globally above (before ticker loop) -- do NOT redefine here
    # OD block shown between 8:30 AM and 9:45 AM ET only

    # Scorecard helper: returns emoji rating based on confidence + direction alignment
    function OD-Scorecard([int]$conf, [string]$sigDir, [string]$overallDir) {
        if ($sigDir -ne $overallDir) { return "[CONFLICT]" }
        if ($conf -ge 80) { return "[BUY]" }
        if ($conf -ge 68) { return "[WATCH]" }
        return "[SKIP]"
    }
    function DTE-Scorecard([int]$conf, [string]$sigDir, [string]$overallDir, [bool]$consensus) {
        if (-not $consensus)         { return "[SKIP - No Consensus]" }
        if ($sigDir -ne $overallDir) { return "[SKIP - Conflicting]" }
        if ($conf -ge 80)            { return "[BUY]" }
        if ($conf -ge 68)            { return "[WATCH]" }
        return "[SKIP]"
    }

    # Exact option strike levels: ITM strike for direction clarity
    # CALL: round up to next $5 strike above current price (slightly OTM)
    # PUT : round down to next $5 strike below current price (slightly OTM)
    [double]$callStrike = [Math]::Ceiling($ep  / 5.0) * 5.0
    [double]$putStrike  = [Math]::Floor($ep    / 5.0) * 5.0
    [double]$callT1     = [Math]::Round($ep + $aU * 1.0, 2)
    [double]$callT2     = [Math]::Round($ep + $aU * 2.0, 2)
    [double]$putT1      = [Math]::Round($ep - $aU * 1.0, 2)
    [double]$putT2      = [Math]::Round($ep - $aU * 2.0, 2)
    [double]$callStop   = [Math]::Round($ep - $aU * 0.5, 2)   # stock price stop for CALL
    [double]$putStop    = [Math]::Round($ep + $aU * 0.5, 2)   # stock price stop for PUT

    # Opening Drive block — time-gated: 8:30 AM – 9:45 AM ET only
    $odBlock = ""
    if ($inODWindow) {
        $odBlock = "--- OPENING DRIVE (Pre-Market Setup) ---`n"
        if ($odSigs.Count -gt 0) {
            foreach ($s in $odSigs) {
                $p      = $s.Split("|")
                $sigDir = $p[3]
                [int]$sigConf = [int]$p[5]
                $sc     = OD-Scorecard $sigConf $sigDir $dir

                # Direction arrow + option type with exact price
                if ($sigDir -eq "LONG") {
                    $odEmoji  = "[UP]"
                    $optLabel = "CALL " + (fmt $callStrike) + " (stock above " + (fmt $ep) + ")"
                    $tgt1     = fmt $callT1
                    $tgt2     = fmt $callT2
                    $stopLvl  = fmt $callStop
                } else {
                    $odEmoji  = "[DN]"
                    $optLabel = "PUT  " + (fmt $putStrike) + " (stock below " + (fmt $ep) + ")"
                    $tgt1     = fmt $putT1
                    $tgt2     = fmt $putT2
                    $stopLvl  = fmt $putStop
                }

                $odBlock += "["+$p[1]+"] "+$p[2]+"`n"
                $odBlock += "  Scorecard  : "+$sc+"`n"
                $odBlock += "  Direction  : "+$odEmoji+" "+$sigDir+"`n"
                $odBlock += "  Option     : "+$optLabel+"`n"
                $odBlock += "  Confidence : "+$sigConf+"%`n"
                $odBlock += "  Entry(stk) : "+(fmt $ep)+"  Stop(stk): "+$stopLvl+"`n"
                $odBlock += "  Target 1   : "+$tgt1+"  Target 2: "+$tgt2+"`n"
                $odBlock += "  Note       : "+$p[6]+"`n"
            }
        } else {
            $odBlock += "  None fired above threshold`n"
        }
        $odBlock += $DV+"`n"
    }
    # Outside OD window: silently suppress OD block (no clutter in intraday alerts)

    # 0DTE block — always shown, but with clear option + scorecard
    $dtBlock = "--- 0DTE OPTIONS (exp today "+$expDate+") ---`n"
    if ($dtSigs.Count -gt 0) {
        foreach ($s in $dtSigs) {
            $p      = $s.Split("|")
            $sigDir = $p[3]
            [int]$sigConf = [int]$p[5]
            $sc     = DTE-Scorecard $sigConf $sigDir $dir $consensus

            if ($sigDir -eq "LONG") {
                $dteLabel  = "[UP] CALL " + (fmt $callStrike)
                $dteEntry  = fmt $ep
                $dteExit1  = fmt $callT1
                $dteExit2  = fmt $callT2
                $dteStop   = fmt $callStop
            } else {
                $dteLabel  = "[DN] PUT  " + (fmt $putStrike)
                $dteEntry  = fmt $ep
                $dteExit1  = fmt $putT1
                $dteExit2  = fmt $putT2
                $dteStop   = fmt $putStop
            }

            $dtBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $dtBlock += "  SCORECARD  : "+$sc+"`n"
            $dtBlock += "  Option     : "+$dteLabel+"  (exp "+$expDate+")`n"
            $dtBlock += "  Entry(stk) : "+$dteEntry+"`n"
            $dtBlock += "  Exit T1    : "+$dteExit1+"  (1x ATR)`n"
            $dtBlock += "  Exit T2    : "+$dteExit2+"  (2x ATR)`n"
            $dtBlock += "  Stop(stk)  : "+$dteStop+"  (0.5x ATR)`n"
            $dtBlock += "  Confidence : "+$sigConf+"%`n"
            $dtBlock += "  Note       : "+$p[6]+"`n"
        }
    } else { $dtBlock += "  None fired above threshold`n" }

    # ============================================================
    #  SCORECARD HELPER — used by all 4 signal blocks + levels
    #  Returns a clear action label with emoji
    # ============================================================
    function Sig-Scorecard([int]$conf, [string]$sigDir, [string]$overallDir, [bool]$cons, [string]$optType) {
        $label = $sym + " " + $optType
        if (-not $cons)              { return "⏭ SKIP   -- " + $label + " (no consensus)" }
        if ($sigDir -ne $overallDir) { return "⏭ SKIP   -- " + $label + " (signal conflicts overall dir)" }
        if ($conf -ge 85)            { return "✅ BUY    -- " + $label }
        if ($conf -ge 75)            { return "👀 WATCH  -- " + $label }
        if ($conf -ge 65)            { return "😐 NEUTRAL-- " + $label }
        return                              "❌ SKIP   -- " + $label + " (low conf " + $conf + "%)"
    }

    # Pre-compute option labels used in all blocks
    [string]$scOptLabel  = if($dir -eq "LONG"){"CALL "+([int]$callStrike)+"C (buy above "+(fmt $ep)+")"}else{"PUT "+([int]$putStrike)+"P (buy below "+(fmt $ep)+")"}
    [string]$moOptLabel  = if($dir -eq "LONG"){"CALL "+([int]$callStrike)+"C (momentum ride)"}else{"PUT "+([int]$putStrike)+"P (momentum ride)"}
    [string]$swOptLabel  = if($dir -eq "LONG"){"CALL "+([int]$callStrike)+"C (2-5 day hold)"}else{"PUT "+([int]$putStrike)+"P (2-5 day hold)"}
    [string]$dtOptLabel  = if($dir -eq "LONG"){"CALL "+([int]$callStrike)+"C (0DTE exp "+$expDate+")"}else{"PUT "+([int]$putStrike)+"P (0DTE exp "+$expDate+")"}

    # ---- SCALP block -----------------------------------------------
    $scBlock = "--- SCALP SIGNALS (1-5 min quick in/out) ---`n"
    if ($scSigs.Count -gt 0) {
        foreach ($s in $scSigs) {
            $p       = $s.Split("|")
            $sigDir  = $p[3]
            [int]$sc = [int]$p[5]
            $optLbl  = if($sigDir -eq "LONG"){$sym+" "+[int]$callStrike+"C"}else{$sym+" "+[int]$putStrike+"P"}
            $scCard  = Sig-Scorecard $sc $sigDir $dir $consensus $optLbl
            if ($sigDir -eq "LONG") {
                $scEntry_ = fmt $ep; $scT1_ = fmt ([double]$ep + $atr*0.3); $scT2_ = fmt ([double]$ep + $atr*0.6); $scStop_ = fmt ([double]$ep - $atr*0.3)
                $scArr = "[UP]"
            } else {
                $scEntry_ = fmt $ep; $scT1_ = fmt ([double]$ep - $atr*0.3); $scT2_ = fmt ([double]$ep - $atr*0.6); $scStop_ = fmt ([double]$ep + $atr*0.3)
                $scArr = "[DN]"
            }
            $scBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $scBlock += "  SCORECARD  : "+$scCard+"`n"
            $scBlock += "  Option     : "+$scArr+" "+$optLbl+"  (scalp 1-5 min)`n"
            $scBlock += "  Entry(stk) : "+$scEntry_+"`n"
            $scBlock += "  Exit T1    : "+$scT1_+"  -- SCALP TARGET`n"
            $scBlock += "  Exit T2    : "+$scT2_+"  -- EXTENDED`n"
            $scBlock += "  Stop(stk)  : "+$scStop_+"  -- CUT LOSS`n"
            $scBlock += "  Confidence : "+$sc+"%`n"
            $scBlock += "  Note       : "+$p[6]+"`n"
        }
    } else { $scBlock += "  None fired above threshold`n" }

    # ---- MOMENTUM block --------------------------------------------
    $moBlock = "--- MOMENTUM SIGNALS (ride the wave) ---`n"
    if ($moSigs.Count -gt 0) {
        foreach ($s in $moSigs) {
            $p       = $s.Split("|")
            $sigDir  = $p[3]
            [int]$mc = [int]$p[5]
            $optLbl  = if($sigDir -eq "LONG"){$sym+" "+[int]$callStrike+"C"}else{$sym+" "+[int]$putStrike+"P"}
            $moCard  = Sig-Scorecard $mc $sigDir $dir $consensus $optLbl
            if ($sigDir -eq "LONG") {
                $moEntry_ = fmt $ep; $moT1_ = fmt ([double]$ep + $atr*1.5); $moT2_ = fmt ([double]$ep + $atr*2.5); $moStop_ = fmt ([double]$ep - $atr*0.8)
                $moArr = "[UP]"
            } else {
                $moEntry_ = fmt $ep; $moT1_ = fmt ([double]$ep - $atr*1.5); $moT2_ = fmt ([double]$ep - $atr*2.5); $moStop_ = fmt ([double]$ep + $atr*0.8)
                $moArr = "[DN]"
            }
            $moBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $moBlock += "  SCORECARD  : "+$moCard+"`n"
            $moBlock += "  Option     : "+$moArr+" "+$optLbl+"  (momentum ride)`n"
            $moBlock += "  Entry(stk) : "+$moEntry_+"`n"
            $moBlock += "  Exit T1    : "+$moT1_+"  -- MO TARGET (1.5x ATR)`n"
            $moBlock += "  Exit T2    : "+$moT2_+"  -- EXTENDED (2.5x ATR)`n"
            $moBlock += "  Stop(stk)  : "+$moStop_+"  -- CUT LOSS (0.8x ATR)`n"
            $moBlock += "  Confidence : "+$mc+"%`n"
            $moBlock += "  Note       : "+$p[6]+"`n"
        }
    } else { $moBlock += "  None fired above threshold`n" }

    # ---- SWING block -----------------------------------------------
    $swBlock = "--- SWING TRADE (2-10 day hold) ---`n"
    if ($swSigs.Count -gt 0) {
        foreach ($s in $swSigs) {
            $p       = $s.Split("|")
            $sigDir  = $p[3]
            [int]$wc = [int]$p[5]
            $optLbl  = if($sigDir -eq "LONG"){$sym+" "+[int]$callStrike+"C (2-5 day)"}else{$sym+" "+[int]$putStrike+"P (2-5 day)"}
            $swCard  = Sig-Scorecard $wc $sigDir $dir $consensus $optLbl
            if ($sigDir -eq "LONG") {
                $swEntry_ = fmt $ep; $swT1_ = fmt ([double]$ep + $atrDaily*2.0); $swT2_ = fmt ([double]$ep + $atrDaily*4.0); $swStop_ = fmt ([double]$ep - $atrDaily*1.5)
                $swArr = "[UP]"
            } else {
                $swEntry_ = fmt $ep; $swT1_ = fmt ([double]$ep - $atrDaily*2.0); $swT2_ = fmt ([double]$ep - $atrDaily*4.0); $swStop_ = fmt ([double]$ep + $atrDaily*1.5)
                $swArr = "[DN]"
            }
            $swBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $swBlock += "  SCORECARD  : "+$swCard+"`n"
            $swBlock += "  Option     : "+$swArr+" "+$optLbl+"`n"
            $swBlock += "  Entry(stk) : "+$swEntry_+"`n"
            $swBlock += "  Exit T1    : "+$swT1_+"  -- SWING TARGET (2x dATR)`n"
            $swBlock += "  Exit T2    : "+$swT2_+"  -- EXTENDED (4x dATR)`n"
            $swBlock += "  Stop(stk)  : "+$swStop_+"  -- CUT LOSS (1.5x dATR)`n"
            $swBlock += "  Confidence : "+$wc+"%`n"
            $swBlock += "  Basis      : "+$p[6]+"`n"
        }
    } else { $swBlock += "  None fired above threshold`n" }

    # ---- CONSOLIDATED LEVELS block (consensus only) ----------------
    $lvBlock = ""
    if ($consensus) {
        # Overall scorecard for the alert
        $overallCard = Sig-Scorecard $topAll $dir $dir $consensus (if($dir -eq "LONG"){$sym+" "+[int]$callStrike+"C"}else{$sym+" "+[int]$putStrike+"P"})

        # v8: R/R ratio calculation for display
        [double]$rrRatio = [Math]::Round($rrMinT1 / [Math]::Max($rrStop,0.01), 1)
        [string]$rrLabel = "1:"+$rrRatio+" R/R"+(if($rrRatio -ge 2.0){" [PASS]"}else{" [SKIP - below 1:2]"})

        $lvBlock  = $DV+"`n"
        $lvBlock += "DIRECTION   : "+$dir+"`n"
        $lvBlock += "ENTRY PRICE : "+$sEntry+"`n"
        $lvBlock += "RISK/REWARD : "+$rrLabel+"`n"
        $lvBlock += "PREV DAY H  : "+(fmt $prevDayHigh)+"   PDL: "+(fmt $prevDayLow)+"`n"
        $lvBlock += $DV+"`n"

        # === SCALP LEVELS ===
        $lvBlock += "SCALP (1-5 min) -- "+$scOptLabel+"`n"
        $lvBlock += "  Scorecard  : "+(Sig-Scorecard $topAll $dir $dir $consensus (if($dir -eq "LONG"){$sym+" "+[int]$callStrike+"C"}else{$sym+" "+[int]$putStrike+"P"}))+"`n"
        $lvBlock += "  Entry(stk) : "+$sEntry+"`n"
        $lvBlock += "  Exit T1    : "+$scT1+"  -- TAKE PROFIT`n"
        $lvBlock += "  Exit T2    : "+$scT2+"  -- EXTENDED`n"
        $lvBlock += "  Stop(stk)  : "+$scStop+"  -- CUT LOSS (0.3x ATR)`n"

        # === INTRADAY 0DTE LEVELS ===
        $lvBlock += $DV+"`n"
        $lvBlock += "INTRADAY 0DTE -- "+$dtOptLabel+"`n"
        $lvBlock += "  Scorecard  : "+$overallCard+"`n"
        $lvBlock += "  Entry(stk) : "+$sEntry+"`n"
        $lvBlock += "  Exit T1    : "+$sT1+"  -- TAKE PROFIT`n"
        $lvBlock += "  Exit T2    : "+$sT2+"  -- EXTENDED`n"
        $lvBlock += "  Exit T3    : "+$sT3+"  -- MAX TARGET`n"
        $lvBlock += "  Stop(stk)  : "+$sStop+"  -- CUT LOSS (1x ATR)`n"

        # === MOMENTUM LEVELS ===
        $lvBlock += $DV+"`n"
        $lvBlock += "MOMENTUM -- "+$moOptLabel+"`n"
        $lvBlock += "  Scorecard  : "+$overallCard+"`n"
        $lvBlock += "  Entry(stk) : "+$sEntry+"`n"
        $lvBlock += "  Exit T1    : "+$moT1+"  -- MO TARGET (1.5x ATR)`n"
        $lvBlock += "  Exit T2    : "+$moT2+"  -- EXTENDED (2.5x ATR)`n"
        $lvBlock += "  Stop(stk)  : "+$moStop+"  -- CUT LOSS (0.8x ATR)`n"

        # === SWING LEVELS ===
        $lvBlock += $DV+"`n"
        $lvBlock += "SWING (2-5 days) -- "+$swOptLabel+"`n"
        $lvBlock += "  Scorecard  : "+$overallCard+"`n"
        $lvBlock += "  Entry(stk) : "+$sEntry+"`n"
        $lvBlock += "  Exit T1    : "+$swT1+"  -- SWING TARGET (2x dATR)`n"
        $lvBlock += "  Exit T2    : "+$swT2+"  -- EXTENDED (4x dATR)`n"
        $lvBlock += "  Stop(stk)  : "+$swStop+"  -- CUT LOSS (1.5x dATR)`n"
        $lvBlock += $DV+"`n"
    }

    # Assemble FULL message -- complete signal breakdown + all levels
    $msg  = $EQ+"`n"
    $msg += $sym+"  |  "+$alertHdr+"`n"
    $msg += $EQ+"`n"
    $msg += "PREV CLOSE  : "+(fmt $prevClose)+"`n"
    $msg += "PREV DAY H  : "+(fmt $prevDayHigh)+"   PDL: "+(fmt $prevDayLow)+"`n"
    $msg += "LAST PRICE  : "+(fmt $curP)+"`n"
    $msg += "GAP         : "+$gapStr+"`n"
    $msg += "PM DATA     : "+$pmStr+"`n"
    $msg += $DV+"`n"
    $msg += "RSI(14)    : "+$rsi+"    RSI(1m): "+$rsi1m+"`n"
    $msg += "EMA9       : "+$ema9+"  EMA21: "+$ema21+"`n"
    $msg += "EMA50      : "+$ema50+"  ATR : "+$atr+"`n"
    $msg += "RVOL       : "+$rvol+"x   VWAP: "+$vwap+"`n"
    [string]$bbwStr = if ($bbwValid) { ([string]$bbw)+"%" } else { "N/A (<"+$bars1m.Count+" bars)" }
    $msg += "BB Width   : "+$bbwStr+"  VolSpike: "+$volSpike+"x`n"
    $msg += "Trend      : "+$trend+"   Daily: "+$dTrend+"`n"
    $msg += $DV+"`n"
    $msg += $odBlock   # empty string outside 8:30-9:45 AM ET window
    $msg += $DV+"`n"
    $msg += $dtBlock
    $msg += $DV+"`n"
    $msg += $scBlock
    $msg += $DV+"`n"
    $msg += $moBlock
    $msg += $DV+"`n"
    $msg += $swBlock
    $msg += $lvBlock

    # ----------------------------------------------------------------
    # WATCH CONTEXT BLOCK — shown when no signals fire (0% confidence)
    # Gives trader full situational awareness even with no trade signal
    # ----------------------------------------------------------------
    if (-not $consensus) {
        # RSI condition label
        $rsiLabel = if ($rsi -lt 30) { "OVERSOLD "+$rsi+" -- snap-back risk" }
                    elseif ($rsi -lt 40) { "Bearish "+$rsi }
                    elseif ($rsi -lt 50) { "Neutral-Bear "+$rsi }
                    elseif ($rsi -lt 60) { "Neutral "+$rsi }
                    elseif ($rsi -lt 70) { "Bullish "+$rsi }
                    else { "OVERBOUGHT "+$rsi }
        # RSI1m condition
        $rsi1mLabel = if ($rsi1m -lt 25) { "EXTREME OVERSOLD "+$rsi1m+" -- bounce alert" }
                      elseif ($rsi1m -lt 38) { "Oversold "+$rsi1m }
                      elseif ($rsi1m -lt 50) { "Soft "+$rsi1m }
                      elseif ($rsi1m -lt 65) { "Neutral "+$rsi1m }
                      else { "Hot "+$rsi1m }
        # VWAP position
        $vwapDelta = [Math]::Round($curP - $vwap, 2)
        $vwapLabel = if ($vwapDelta -gt 0) { "ABOVE VWAP +"+$vwapDelta }
                     elseif ($vwapDelta -lt 0) { "BELOW VWAP "+$vwapDelta }
                     else { "AT VWAP" }
        # BBW squeeze alert
        $bbwAlert  = if ($bbwValid -and $bbw -lt 0.3) { "EXTREME SQUEEZE "+$bbw+"% -- explosive move imminent" }
                     elseif ($bbwValid -and $bbw -lt 0.8) { "Tight squeeze "+$bbw+"%" }
                     else { "Normal "+$bbwStr }
        # What would trigger a signal
        $triggerNote = if ($dir -eq "LONG") {
            "LONG trigger: price reclaims VWAP "+(fmt $vwap)+" + OD/SC engine fires"
        } else {
            "SHORT trigger: price breaks below "+(fmt ($vwap - $atr*0.3))+" + SC engine fires"
        }
        # Key levels
        $watchLong  = fmt ([Math]::Round($curP + $atr*1.0, 2))
        $watchShort = fmt ([Math]::Round($curP - $atr*1.0, 2))
        $watchVwap  = fmt $vwap

        $msg += $DV+"`n"
        $msg += "--- WATCH CONTEXT (no signal fired) ---`n"
        $msg += "  RSI(14)    : "+$rsiLabel+"`n"
        $msg += "  RSI(1m)    : "+$rsi1mLabel+"`n"
        $msg += "  VWAP Pos   : "+$vwapLabel+"  (VWAP="+$watchVwap+")`n"
        $msg += "  BB Width   : "+$bbwAlert+"`n"
        $msg += "  Trend      : "+$trend+"   Daily: "+$dTrend+"`n"
        $msg += $DV+"`n"
        $msg += "  KEY LEVELS TO WATCH:`n"
        $msg += "  VWAP       : "+$watchVwap+"  (bull/bear line)`n"
        $msg += "  EMA9       : "+(fmt $ema9)+"  (intraday trend)`n"
        $msg += "  Upside R1  : "+$watchLong+"  (1x ATR above)`n"
        $msg += "  Downside S1: "+$watchShort+"  (1x ATR below)`n"
        $msg += $DV+"`n"
        $msg += "  NO TRADE NOW -- "+$triggerNote+"`n"
    }

    $msg += $EQ+"`n"
    $msg += "v8 | "+$nowET.ToString("HH:mm")+" ET | "+$sym

    Send-TG $msg

    # ============================================================
    #  S24 / S25 — GOLDEN CROSS & DEATH CROSS (5-min intraday)
    #  Separate dedicated alert — fires independently of main signal
    #
    #  GOLDEN CROSS (S24):
    #    prev bar: ema9 < ema21  →  current bar: ema9 > ema21
    #    + price above BOTH ema9 AND ema21
    #    + price above VWAP
    #    + RVOL >= 1.5x
    #    + Pullback entry: price within 0.3x ATR of ema9 (touching 9 EMA)
    #
    #  DEATH CROSS (S25):
    #    prev bar: ema9 > ema21  →  current bar: ema9 < ema21
    #    + price below BOTH ema9 AND ema21
    #    + price below VWAP
    #    + RVOL >= 1.5x
    #    + Bounce entry: price within 0.3x ATR of ema9 (touching 9 EMA)
    # ============================================================
    if ($hasID -and $idays.Count -ge 6) {
        # Build EMA series on 5-min closes
        [double[]]$id5C      = @($idays | ForEach-Object { [double]$_.c })
        $ema9Series          = @(Calc-EMA-Series $id5C 9)
        $ema21Series         = @(Calc-EMA-Series $id5C 21)

        if ($ema9Series.Count -ge 2 -and $ema21Series.Count -ge 2) {
            [int]$last  = $ema9Series.Count - 1
            [int]$prev  = $last - 1

            [double]$ema9Curr  = $ema9Series[$last]
            [double]$ema9Prev  = $ema9Series[$prev]
            [double]$ema21Curr = $ema21Series[$last]
            [double]$ema21Prev = $ema21Series[$prev]

            # Detect crossover transitions
            [bool]$goldenCross = ($ema9Prev -lt $ema21Prev) -and ($ema9Curr -gt $ema21Curr)
            [bool]$deathCross  = ($ema9Prev -gt $ema21Prev) -and ($ema9Curr -lt $ema21Curr)

            # Pullback / bounce proximity check (price within 0.3x ATR of 9 EMA)
            [bool]$atEMA9      = ([Math]::Abs($curP - $ema9Curr) -lt ($atr * 0.3))

            # ---- S24: GOLDEN CROSS ----
            if ($goldenCross -and $curP -gt $ema9Curr -and $curP -gt $ema21Curr -and $abvVWAP -and $rvol -gt 1.5) {
                [int]$gcConf = 82
                if ($rvol -gt 2.5){$gcConf += 5}
                if ($rsi -gt 55) {$gcConf += 4}
                if ($atEMA9)     {$gcConf += 6}   # pullback entry bonus
                $gcConf = [Math]::Min($gcConf, 97)

                [string]$gcEntry  = fmt $curP
                [string]$gcStop   = fmt ([Math]::Round($curP - $atr * 0.5, 2))
                [string]$gcT1     = fmt ([Math]::Round($curP + $atr * 1.0, 2))
                [string]$gcT2     = fmt ([Math]::Round($curP + $atr * 2.0, 2))
                [string]$gcT3     = fmt ([Math]::Round($curP + $atr * 3.0, 2))
                [string]$gcStrike = fmt ([Math]::Ceiling($curP / 5.0) * 5.0)
                [string]$gcPullbackNote = if ($atEMA9) { "PULLBACK ENTRY ACTIVE -- price touching 9 EMA now" } `
                                          else { "Wait for pullback to 9 EMA="+$ema9Curr+" for entry" }

                $gcMsg  = $EQ+"`n"
                $gcMsg += "*** [S24] GOLDEN CROSS -- "+$sym+" ***`n"
                $gcMsg += $EQ+"`n"
                $gcMsg += "CROSS TYPE  : 9 EMA crossed ABOVE 21 EMA (5-min)`n"
                $gcMsg += "PREV BAR    : EMA9="+$ema9Prev+" vs EMA21="+$ema21Prev+" (9 was BELOW 21)`n"
                $gcMsg += "CURR BAR    : EMA9="+$ema9Curr+" vs EMA21="+$ema21Curr+" (9 now ABOVE 21)`n"
                $gcMsg += $DV+"`n"
                $gcMsg += "PRICE       : "+(fmt $curP)+"`n"
                $gcMsg += "VWAP        : "+(fmt $vwap)+"  (price ABOVE VWAP -- confirmed)`n"
                $gcMsg += "EMA9 (5min) : "+$ema9Curr+"  EMA21: "+$ema21Curr+"`n"
                $gcMsg += "RSI         : "+$rsi+"    RSI1m: "+$rsi1m+"`n"
                $gcMsg += "RVOL        : "+$rvol+"x   ATR: "+$atr+"`n"
                $gcMsg += "CONFIDENCE  : "+$gcConf+"%`n"
                $gcMsg += $DV+"`n"
                $gcMsg += "ENTRY SETUP : "+$gcPullbackNote+"`n"
                $gcMsg += $DV+"`n"
                $gcMsg += "OPTION      : [UP] CALL "+$gcStrike+" (0DTE exp "+$expDate+")`n"
                $gcMsg += "Entry(stk)  : "+$gcEntry+"`n"
                $gcMsg += "Exit T1     : "+$gcT1+"  -- TAKE PROFIT (1x ATR)`n"
                $gcMsg += "Exit T2     : "+$gcT2+"  -- EXTENDED (2x ATR)`n"
                $gcMsg += "Exit T3     : "+$gcT3+"  -- MAX TARGET (3x ATR)`n"
                $gcMsg += "Stop(stk)   : "+$gcStop+"  -- CUT LOSS (0.5x ATR)`n"
                $gcMsg += $DV+"`n"
                $gcMsg += "RULES:`n"
                $gcMsg += "  1. Confirm 5-min candle CLOSES above both EMA9+EMA21`n"
                $gcMsg += "  2. Price must stay above VWAP "+$vwap+"`n"
                $gcMsg += "  3. Enter on pullback to EMA9="+$ema9Curr+" (within 0.3x ATR)`n"
                $gcMsg += "  4. Stop below "+$gcStop+" (0.5x ATR below entry)`n"
                $gcMsg += "  5. Trail stop to EMA9 once T1 hit`n"
                $gcMsg += $EQ+"`n"
                $gcMsg += "v8-GC | "+$nowET.ToString("HH:mm")+" ET | "+$sym

                Start-Sleep -Seconds 2   # extra gap so GC alert stands out from main signal
                Send-TG $gcMsg
                Write-Host ("  [S24] GOLDEN CROSS FIRED for "+$sym+" -- EMA9 crossed above EMA21 on 5-min")
            }

            # ---- S25: DEATH CROSS ----
            elseif ($deathCross -and $curP -lt $ema9Curr -and $curP -lt $ema21Curr -and (-not $abvVWAP) -and $rvol -gt 1.5) {
                [int]$dcConf = 80
                if ($rvol -gt 2.5){$dcConf += 5}
                if ($rsi -lt 45) {$dcConf += 4}
                if ($atEMA9)     {$dcConf += 6}   # bounce entry bonus
                $dcConf = [Math]::Min($dcConf, 97)

                [string]$dcEntry  = fmt $curP
                [string]$dcStop   = fmt ([Math]::Round($curP + $atr * 0.5, 2))
                [string]$dcT1     = fmt ([Math]::Round($curP - $atr * 1.0, 2))
                [string]$dcT2     = fmt ([Math]::Round($curP - $atr * 2.0, 2))
                [string]$dcT3     = fmt ([Math]::Round($curP - $atr * 3.0, 2))
                [string]$dcStrike = fmt ([Math]::Floor($curP / 5.0) * 5.0)
                [string]$dcBounceNote = if ($atEMA9) { "BOUNCE ENTRY ACTIVE -- price touching 9 EMA now" } `
                                        else { "Wait for bounce to 9 EMA="+$ema9Curr+" for entry" }

                $dcMsg  = $EQ+"`n"
                $dcMsg += "*** [S25] DEATH CROSS -- "+$sym+" ***`n"
                $dcMsg += $EQ+"`n"
                $dcMsg += "CROSS TYPE  : 9 EMA crossed BELOW 21 EMA (5-min)`n"
                $dcMsg += "PREV BAR    : EMA9="+$ema9Prev+" vs EMA21="+$ema21Prev+" (9 was ABOVE 21)`n"
                $dcMsg += "CURR BAR    : EMA9="+$ema9Curr+" vs EMA21="+$ema21Curr+" (9 now BELOW 21)`n"
                $dcMsg += $DV+"`n"
                $dcMsg += "PRICE       : "+(fmt $curP)+"`n"
                $dcMsg += "VWAP        : "+(fmt $vwap)+"  (price BELOW VWAP -- confirmed)`n"
                $dcMsg += "EMA9 (5min) : "+$ema9Curr+"  EMA21: "+$ema21Curr+"`n"
                $dcMsg += "RSI         : "+$rsi+"    RSI1m: "+$rsi1m+"`n"
                $dcMsg += "RVOL        : "+$rvol+"x   ATR: "+$atr+"`n"
                $dcMsg += "CONFIDENCE  : "+$dcConf+"%`n"
                $dcMsg += $DV+"`n"
                $dcMsg += "ENTRY SETUP : "+$dcBounceNote+"`n"
                $dcMsg += $DV+"`n"
                $dcMsg += "OPTION      : [DN] PUT "+$dcStrike+" (0DTE exp "+$expDate+")`n"
                $dcMsg += "Entry(stk)  : "+$dcEntry+"`n"
                $dcMsg += "Exit T1     : "+$dcT1+"  -- TAKE PROFIT (1x ATR)`n"
                $dcMsg += "Exit T2     : "+$dcT2+"  -- EXTENDED (2x ATR)`n"
                $dcMsg += "Exit T3     : "+$dcT3+"  -- MAX TARGET (3x ATR)`n"
                $dcMsg += "Stop(stk)   : "+$dcStop+"  -- CUT LOSS (0.5x ATR)`n"
                $dcMsg += $DV+"`n"
                $dcMsg += "RULES:`n"
                $dcMsg += "  1. Confirm 5-min candle CLOSES below both EMA9+EMA21`n"
                $dcMsg += "  2. Price must stay below VWAP "+$vwap+"`n"
                $dcMsg += "  3. Enter on bounce to EMA9="+$ema9Curr+" (within 0.3x ATR)`n"
                $dcMsg += "  4. Stop above "+$dcStop+" (0.5x ATR above entry)`n"
                $dcMsg += "  5. Trail stop to EMA9 once T1 hit`n"
                $dcMsg += $EQ+"`n"
                $dcMsg += "v8-DC | "+$nowET.ToString("HH:mm")+" ET | "+$sym

                Start-Sleep -Seconds 2   # extra gap so DC alert stands out from main signal
                Send-TG $dcMsg
                Write-Host ("  [S25] DEATH CROSS FIRED for "+$sym+" -- EMA9 crossed below EMA21 on 5-min")
            }
        }
    }

    $statusStr = if($consensus){$dir+" ALERT (OD:"+$odSigs.Count+" 0DTE:"+$dtSigs.Count+" SC:"+$scSigs.Count+" MO:"+$moSigs.Count+" SW:"+$swSigs.Count+" top:"+$topAll+"%)"}else{"WATCH (OD:"+$odSigs.Count+" 0DTE:"+$dtSigs.Count+" SC:"+$scSigs.Count+" MO:"+$moSigs.Count+" SW:"+$swSigs.Count+")"}
    $summary += [PSCustomObject]@{
        Sym=$sym; Status=$statusStr; Price=$curP; Gap=$gapStr; RVOL=$rvol; RSI=$rsi
        ScT1=$scT1; ScT2=$scT2; T1=$sT1; T2=$sT2; MoT1=$moT1; SwT1=$swT1; SwT2=$swT2
    }
}

# ============================================================
#  MASTER SUMMARY TELEGRAM
# ============================================================
$sumMsg  = $EQ+"`n"
$sumMsg += "MASTER SIGNAL SUMMARY v5`n"
$sumMsg += $nowET.ToString("HH:mm")+" ET  "+$todayDate+"`n"
$sumMsg += $EQ+"`n"
foreach ($r in $summary) {
    $sumMsg += $r.Sym.PadRight(6)+" | "+$r.Status+"`n"
    $sumMsg += "  Price="+(fmt $r.Price)+"  Gap="+$r.Gap+"  RVOL="+$r.RVOL+"x  RSI="+$r.RSI+"`n"
    if ($r.ScT1 -ne "") {
        $sumMsg += "  Scalp : T1="+$r.ScT1+"  T2="+$r.ScT2+"`n"
        $sumMsg += "  0DTE  : T1="+$r.T1+"  T2="+$r.T2+"`n"
        $sumMsg += "  Mo    : T1="+$r.MoT1+"`n"
        $sumMsg += "  Swing : T1="+$r.SwT1+"  T2="+$r.SwT2+"`n"
    }
}
$sumMsg += $EQ
Send-TG $sumMsg

Write-Host ("`n=== Complete. "+$summary.Count+" tickers processed ===")
$summary | Format-Table Sym,Status,Price,Gap,RVOL,RSI,ScT1,T1,MoT1 -AutoSize
