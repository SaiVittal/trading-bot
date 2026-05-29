# ============================================================
# Trading Bot Signal Runner  v5
# Signals : Opening Drive | 0DTE | Swing | SCALP | MOMENTUM
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

function Get-Bars([string]$s,[string]$tf,[string]$start,[int]$lim=100) {
    $url = $baseUrl+"/v2/stocks/"+$s+"/bars?timeframe="+$tf+"&start="+$start+"&limit="+$lim+"&feed=iex&sort=asc"
    try { return @((Invoke-RestMethod -Uri $url -Headers $hdr).bars) } catch { return @() }
}

function Get-LastPrice([string]$s) {
    try {
        return [Math]::Round([double](Invoke-RestMethod -Uri ($baseUrl+"/v2/stocks/"+$s+"/trades/latest?feed=iex") -Headers $hdr).trade.p,2)
    } catch {
        try {
            return [Math]::Round([double](Invoke-RestMethod -Uri ($baseUrl+"/v2/stocks/"+$s+"/bars/latest?feed=iex") -Headers $hdr).bar.c,2)
        } catch { return 0.0 }
    }
}

function Calc-EMA([double[]]$a,[int]$p) {
    if ($a.Count -eq 0) { return 0.0 }
    $k = 2.0/([Math]::Min($p,$a.Count)+1.0)
    $e = $a[0]
    for ($i=1; $i -lt $a.Count; $i++) { $e = $a[$i]*$k + $e*(1.0-$k) }
    return [Math]::Round($e,2)
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

function Send-TG([string]$msg) {
    $body = '{"chat_id":"' + $TgChat + '","text":' + ($msg|ConvertTo-Json) + '}'
    try {
        Invoke-RestMethod -Uri ("https://api.telegram.org/bot"+$TgToken+"/sendMessage") `
            -Method POST -Body $body -ContentType "application/json" | Out-Null
    } catch { Write-Host ("TG-ERR: "+$_.Exception.Message) }
    Start-Sleep -Milliseconds 900
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
$pmStart     = $todayDate+"T09:00:00Z"
$expDate     = $todayDate.Substring(5,2)+"/"+$todayDate.Substring(8,2)

$EQ = "=" * 48
$DV = "-" * 46

Write-Host ("=== Signal Run v5 @ "+$nowET.ToString("HH:mm")+" ET  "+$todayDate+" ===")

$summary = @()

# ============================================================
#  PER-TICKER LOOP
# ============================================================

foreach ($sym in $Tickers) {
    Write-Host ("`n--- "+$sym+" ---")

    # -- Daily bars --
    $daily = @(Get-Bars $sym "1Day" $dailyStart 90)
    if ($daily.Count -lt 10) { Write-Host ("  "+$sym+": insufficient daily data"); continue }

    [double]$prevClose = [Math]::Round([double]$daily[-1].c,2)
    [double]$avgVol20  = ($daily|Select-Object -Last 20|ForEach-Object{[double]$_.v}|Measure-Object -Average).Average
    [double]$atrDaily  = Calc-ATR $daily 14
    [double[]]$dailyC  = @($daily|ForEach-Object{[double]$_.c})
    [double]$dEMA9     = Calc-EMA $dailyC 9
    [double]$dEMA21    = Calc-EMA $dailyC 21
    [double]$dEMA50    = Calc-EMA $dailyC 50
    [double]$dRSI      = Calc-RSI $dailyC 14

    # -- Last trade price --
    [double]$curP = Get-LastPrice $sym
    if ($curP -eq 0.0) { $curP = $prevClose }

    # -- Premarket bars --
    $pmBars    = @(Get-Bars $sym "1Min" $pmStart 300)
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
    [double]$ema21 = Calc-EMA  $blend 21
    [double]$ema50 = Calc-EMA  $blend 50
    [double]$vwap  = if ($hasID){Calc-VWAP $idays}elseif($hasPM){Calc-VWAP $pmBars}else{$prevClose}

    # -- 1-min indicators for scalp --
    [double]$rsi1m   = Calc-RSI $c1m 7          # faster RSI for scalp
    [double]$ema9_1m = Calc-EMA $c1m 9
    [double]$bbw     = Calc-BBWidth $c1m 20      # BB width % -- low = squeeze ready to pop

    # Last 3 1-min candles for momentum detection
    [double]$vol1mAvg = Calc-AvgVol1m $bars1m 10
    [double]$vol1mLst = if ($has1m){[double]($bars1m[-1]).v}else{0.0}
    [double]$volSpike = if ($vol1mAvg -gt 0){[Math]::Round($vol1mLst/$vol1mAvg,1)}else{0.0}

    # RVOL (paced vs 20-day avg)
    $minsElapsed   = ($nowUtc - $sessionOpen).TotalMinutes
    $fracEl        = [Math]::Max([Math]::Min($minsElapsed/390.0,1.0),0.001)
    [double]$todayVol = if ($hasID){($idays|ForEach-Object{[double]$_.v}|Measure-Object -Sum).Sum}
                        elseif($hasPM){$pmVol}else{0.0}
    [double]$rvol  = if($avgVol20 -gt 0 -and $todayVol -gt 0){[Math]::Round(($todayVol/$fracEl)/$avgVol20,2)}else{0.0}

    [bool]$abvEMA9  = ($curP -gt $ema9)
    [bool]$abvVWAP  = ($curP -gt $vwap -and $vwap -gt 0)
    [string]$trend  = if($ema9 -gt $ema21 -and $ema21 -gt $ema50){"UP"}elseif($ema9 -lt $ema21){"DOWN"}else{"NEUTRAL"}
    [string]$dTrend = if($dEMA9 -gt $dEMA21 -and $dEMA21 -gt $dEMA50){"UPTREND"}
                      elseif($dEMA9 -lt $dEMA21){"DOWNTREND"}else{"SIDEWAYS"}

    Write-Host ("  Price="+$curP+"  RSI="+$rsi+"  RSI1m="+$rsi1m+"  EMA9="+$ema9+"  ATR="+$atr)
    Write-Host ("  RVOL="+$rvol+"x  VWAP="+$vwap+"  BBW="+$bbw+"%  VolSpike="+$volSpike+"x  Trend="+$trend)
    Write-Host ("  Daily: dRSI="+$dRSI+"  dEMA9="+$dEMA9+"  dTrend="+$dTrend)

    # ============================================================
    #  STRATEGY ENGINE
    #  Format: TYPE|ID|NAME|DIR|OPT_TYPE|CONF|ACTION_NOTE
    # ============================================================
    $raw = [System.Collections.Generic.List[string]]::new()

    # ---- A. OPENING DRIVE SIGNALS (OD) -------------------------

    # S19A: Gap Breakout Opening Drive
    if ($gapPct -gt 3.0 -and $rvol -gt 1.2 -and $rsi -gt 52 -and $abvVWAP) {
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
    if ($hasID -and $idays.Count -ge 2) {
        [double]$orH = ($idays|Select-Object -First 2|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum
        [double]$orL = ($idays|Select-Object -First 2|ForEach-Object{[double]$_.l}|Measure-Object -Minimum).Minimum
        if ($curP -gt $orH -and $rvol -gt 1.2) {
            [int]$c=72; if($rvol -gt 2.0){$c+=6}; if($rvol -gt 5.0){$c+=4}
            $raw.Add("OD|S08|Opening Range Breakout|LONG|CALL|"+$c+"|Broke ORB high "+[Math]::Round($orH,2)+" RVOL="+$rvol+"x.")
        } elseif ($curP -lt $orL -and $rvol -gt 1.2) {
            [int]$c=70; if($rvol -gt 2.0){$c+=6}
            $raw.Add("OD|S08|Opening Range Breakdown|SHORT|PUT|"+$c+"|Broke ORB low "+[Math]::Round($orL,2)+" RVOL="+$rvol+"x.")
        }
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
    if ($bbw -gt 0 -and $bbw -lt 1.5 -and $rvol -gt 1.2) {
        [int]$c=70; if($bbw -lt 0.8){$c+=8}; if($rvol -gt 2.5){$c+=5}
        [string]$squeezeDir = if($curP -gt $vwap){"LONG"}else{"SHORT"}
        [string]$squeezeOpt = if($squeezeDir -eq "LONG"){"CALL"}else{"PUT"}
        $raw.Add("SCALP|SC04|BB Squeeze Breakout Scalp|"+$squeezeDir+"|"+$squeezeOpt+"|"+$c+"|BB width="+$bbw+"% (tight squeeze). RVOL="+$rvol+"x. Scalp "+$squeezeOpt+" -- explosive move expected in 1-5 min.")
    }

    # ---- E. MOMENTUM SIGNALS -----------------------------------

    # MO01: Volume Surge Momentum -- RVOL > 2.5x with trend continuation
    if ($rvol -gt 2.5 -and $rsi -gt 50 -and $trend -eq "UP" -and $abvVWAP) {
        [int]$c=75; if($rvol -gt 4.0){$c+=8}; if($rvol -gt 8.0){$c+=5}; if($rsi -gt 60){$c+=4}
        $c=[Math]::Min($c,95)
        $raw.Add("MOMENTUM|MO01|Volume Surge Momentum|LONG|CALL|"+$c+"|RVOL="+$rvol+"x (abnormal volume surge). Trend UP above VWAP="+$vwap+". Ride momentum -- CALL.")
    } elseif ($rvol -gt 2.5 -and $rsi -lt 50 -and $trend -eq "DOWN" -and (-not $abvVWAP)) {
        [int]$c=73; if($rvol -gt 4.0){$c+=8}
        $c=[Math]::Min($c,95)
        $raw.Add("MOMENTUM|MO01|Volume Surge Momentum|SHORT|PUT|"+$c+"|RVOL="+$rvol+"x surge below VWAP. Trend DOWN. Ride momentum -- PUT.")
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

    [bool]$consensus = ($fired.Count -ge 2) -or ($topAll -ge 78)
    [int]$longCt  = @($fired|Where-Object{$_.Split("|")[3] -eq "LONG"}).Count
    [int]$shortCt = @($fired|Where-Object{$_.Split("|")[3] -eq "SHORT"}).Count
    [string]$dir  = if($longCt -ge $shortCt){"LONG"}else{"SHORT"}

    Write-Host ("  Sigs: OD="+$odSigs.Count+" 0DTE="+$dtSigs.Count+" SW="+$swSigs.Count+" SC="+$scSigs.Count+" MO="+$moSigs.Count+"  TopConf="+$topAll+"%  Consensus="+$consensus+"  Dir="+$dir)

    # ============================================================
    #  PRICE LEVELS
    # ============================================================
    [double]$ep  = $curP
    [double]$aU  = if($atr -gt 0.01){$atr}else{$ep*0.01}
    [double]$aUs = if($atr -gt 0.01){$atr*0.3}else{$ep*0.003}   # scalp: 0.3x ATR

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

    # Opening Drive block
    $odBlock = "--- OPENING DRIVE (Intraday) ---`n"
    if ($odSigs.Count -gt 0) {
        foreach ($s in $odSigs) {
            $p = $s.Split("|")
            $odBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $odBlock += "  Type       : OPENING DRIVE "+$p[3]+"`n"
            $odBlock += "  Confidence : "+$p[5]+"%`n"
            $odBlock += "  Action     : "+$p[6]+"`n"
        }
    } else { $odBlock += "  None fired above threshold`n" }

    # 0DTE block
    $dtBlock = "--- 0DTE OPTIONS (exp today "+$expDate+") ---`n"
    if ($dtSigs.Count -gt 0) {
        foreach ($s in $dtSigs) {
            $p = $s.Split("|")
            $dtBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $dtBlock += "  0DTE Type  : 0DTE "+$p[4]+"S (expires "+$expDate+")`n"
            $dtBlock += "  Direction  : "+$p[3]+"`n"
            $dtBlock += "  Confidence : "+$p[5]+"%`n"
            $dtBlock += "  Note       : "+$p[6]+"`n"
        }
    } else { $dtBlock += "  None fired above threshold`n" }

    # Scalp block
    $scBlock = "--- SCALP SIGNALS (1-5 min quick in/out) ---`n"
    if ($scSigs.Count -gt 0) {
        foreach ($s in $scSigs) {
            $p = $s.Split("|")
            $scBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $scBlock += "  Scalp Type : SCALP "+$p[4]+"S`n"
            $scBlock += "  Direction  : "+$p[3]+"`n"
            $scBlock += "  Confidence : "+$p[5]+"%`n"
            $scBlock += "  Entry      : "+$sEntry+"  Stop: "+$scStop+"`n"
            $scBlock += "  Scalp T1   : "+$scT1+"  T2: "+$scT2+"`n"
            $scBlock += "  Note       : "+$p[6]+"`n"
        }
    } else { $scBlock += "  None fired above threshold`n" }

    # Momentum block
    $moBlock = "--- MOMENTUM SIGNALS (ride the wave) ---`n"
    if ($moSigs.Count -gt 0) {
        foreach ($s in $moSigs) {
            $p = $s.Split("|")
            $moBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $moBlock += "  Momentum   : MOMENTUM "+$p[4]+"S`n"
            $moBlock += "  Direction  : "+$p[3]+"`n"
            $moBlock += "  Confidence : "+$p[5]+"%`n"
            $moBlock += "  Entry      : "+$sEntry+"  Stop: "+$moStop+"`n"
            $moBlock += "  Mo T1      : "+$moT1+"  T2: "+$moT2+"`n"
            $moBlock += "  Note       : "+$p[6]+"`n"
        }
    } else { $moBlock += "  None fired above threshold`n" }

    # Swing block
    $swBlock = "--- SWING TRADE (2-10 day hold) ---`n"
    if ($swSigs.Count -gt 0) {
        foreach ($s in $swSigs) {
            $p = $s.Split("|")
            $swBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $swBlock += "  Direction  : "+$p[3]+"`n"
            $swBlock += "  Confidence : "+$p[5]+"%`n"
            $swBlock += "  Basis      : "+$p[6]+"`n"
        }
    } else { $swBlock += "  None fired above threshold`n" }

    # Price levels (only for consensus signals)
    $lvBlock = ""
    if ($consensus) {
        $lvBlock  = $DV+"`n"
        $lvBlock += "DIRECTION   : "+$dir+"`n"
        $lvBlock += "ENTRY       : "+$sEntry+"`n"
        $lvBlock += $DV+"`n"
        $lvBlock += "SCALP LEVELS (1-5 min):`n"
        $lvBlock += "  Stop       : "+$scStop+"  (0.3x ATR)`n"
        $lvBlock += "  T1         : "+$scT1+"`n"
        $lvBlock += "  T2         : "+$scT2+"`n"
        $lvBlock += "INTRADAY 0DTE LEVELS:`n"
        $lvBlock += "  Stop       : "+$sStop+"  (1x ATR)`n"
        $lvBlock += "  T1         : "+$sT1+"`n"
        $lvBlock += "  T2         : "+$sT2+"`n"
        $lvBlock += "  T3         : "+$sT3+"`n"
        $lvBlock += "MOMENTUM LEVELS:`n"
        $lvBlock += "  Stop       : "+$moStop+"  (0.8x ATR)`n"
        $lvBlock += "  T1         : "+$moT1+"  (1.5x ATR)`n"
        $lvBlock += "  T2         : "+$moT2+"  (2.5x ATR)`n"
        $lvBlock += "SWING LEVELS (2-5 days):`n"
        $lvBlock += "  Stop       : "+$swStop+"  (1.5x daily ATR)`n"
        $lvBlock += "  T1         : "+$swT1+"  (2x daily ATR)`n"
        $lvBlock += "  T2         : "+$swT2+"  (4x daily ATR)`n"
        $lvBlock += $DV+"`n"
        $lvBlock += "0DTE OPTIONS (exp "+$expDate+"):`n"
        $lvBlock += "  Trade Type : 0DTE "+$optType+"S`n"
        $lvBlock += "  Strike     : ~"+(fmt $strike)+"  (ATM)`n"
        $lvBlock += "  Delta      : 0.40-0.55 ATM`n"
        $lvBlock += "  Entry Rule : Confirm "+$dir+" at 9:35 AM open`n"
        $lvBlock += "  Exit Rule  : T1 OR 11:00 AM -- whichever first`n"
        $lvBlock += $DV+"`n"
    }

    # Assemble full message
    $msg  = $EQ+"`n"
    $msg += $sym+"  |  "+$alertHdr+"`n"
    $msg += $EQ+"`n"
    $msg += "PREV CLOSE  : "+(fmt $prevClose)+"`n"
    $msg += "LAST PRICE  : "+(fmt $curP)+"`n"
    $msg += "GAP         : "+$gapStr+"`n"
    $msg += "PM DATA     : "+$pmStr+"`n"
    $msg += $DV+"`n"
    $msg += "RSI(14)    : "+$rsi+"    RSI(1m): "+$rsi1m+"`n"
    $msg += "EMA9       : "+$ema9+"  EMA21: "+$ema21+"`n"
    $msg += "EMA50      : "+$ema50+"  ATR : "+$atr+"`n"
    $msg += "RVOL       : "+$rvol+"x   VWAP: "+$vwap+"`n"
    $msg += "BB Width   : "+$bbw+"%  VolSpike: "+$volSpike+"x`n"
    $msg += "Trend      : "+$trend+"   Daily: "+$dTrend+"`n"
    $msg += $DV+"`n"
    $msg += $odBlock
    $msg += $DV+"`n"
    $msg += $dtBlock
    $msg += $DV+"`n"
    $msg += $scBlock
    $msg += $DV+"`n"
    $msg += $moBlock
    $msg += $DV+"`n"
    $msg += $swBlock
    $msg += $lvBlock
    $msg += $EQ+"`n"
    $msg += "v5 | "+$nowET.ToString("HH:mm")+" ET | "+$sym

    Send-TG $msg

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
