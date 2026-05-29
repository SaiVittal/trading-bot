# ============================================================
# Trading Bot Signal Runner  v4
# Signals : Opening Drive (Intraday) | 0DTE | Swing
# Branch  : fix/entry-price-t1-t2-t3
#
# RULE: No price variable ever goes inside a double-quoted string.
#       All prices use fmt() + pure concatenation to prevent
#       PowerShell re-parsing the leading "$" as a variable.
# ============================================================

param(
    [string[]]$Tickers = @("TSLA","NBIS","COST","MSFT","IBM","ORCL","APP","NOW"),
    [string]$ApiKey    = "PKUVZN3EUNDEDFIIWS3NHWMKXK",
    [string]$ApiSecret = "2otPyywguF8Xn2mgCwgLifEbP9s8RrbLF9mjVn95EjXo",
    [string]$TgToken   = "8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw",
    [string]$TgChat    = "5979887660"
)

$ErrorActionPreference = "SilentlyContinue"
$hdr     = @{ "APCA-API-KEY-ID" = $ApiKey; "APCA-API-SECRET-KEY" = $ApiSecret }
$baseUrl = "https://data.alpaca.markets"

# ════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ════════════════════════════════════════════════════

# fmt : always returns "$NNN.NN" via concatenation -- NEVER use inside ""
function fmt([double]$v) { return "$" + [Math]::Round($v,2).ToString("F2") }
function pct([double]$v) { return [Math]::Round($v,1).ToString() + "%" }

function Get-Bars([string]$s,[string]$tf,[string]$start,[int]$lim=100) {
    $url = $baseUrl+"/v2/stocks/"+$s+"/bars?timeframe="+$tf+"&start="+$start+"&limit="+$lim+"&feed=iex&sort=asc"
    try { return @((Invoke-RestMethod -Uri $url -Headers $hdr).bars) } catch { return @() }
}

function Get-LastPrice([string]$s) {
    try { return [Math]::Round([double](Invoke-RestMethod -Uri ($baseUrl+"/v2/stocks/"+$s+"/trades/latest?feed=iex") -Headers $hdr).trade.p,2) }
    catch {
        try { return [Math]::Round([double](Invoke-RestMethod -Uri ($baseUrl+"/v2/stocks/"+$s+"/bars/latest?feed=iex") -Headers $hdr).bar.c,2) }
        catch { return 0.0 }
    }
}

function Calc-EMA([double[]]$a,[int]$p) {
    if ($a.Count -eq 0) { return 0.0 }
    $k = 2.0/([Math]::Min($p,$a.Count)+1.0); $e=$a[0]
    for ($i=1;$i -lt $a.Count;$i++){$e=$a[$i]*$k+$e*(1.0-$k)}
    return [Math]::Round($e,2)
}

function Calc-RSI([double[]]$c,[int]$p=14) {
    if ($c.Count -lt ($p+1)){return 50.0}
    $ag=0.0;$al=0.0
    for ($i=1;$i -le $p;$i++){$d=$c[$i]-$c[$i-1];if($d -gt 0){$ag+=$d}else{$al+=[Math]::Abs($d)}}
    $ag/=$p;$al/=$p
    for ($i=($p+1);$i -lt $c.Count;$i++){
        $d=$c[$i]-$c[$i-1]
        if($d -gt 0){$ag=($ag*($p-1)+$d)/$p;$al=$al*($p-1)/$p}
        else{$al=($al*($p-1)+[Math]::Abs($d))/$p;$ag=$ag*($p-1)/$p}
    }
    if($al -eq 0){return 100.0}
    return [Math]::Round(100.0-100.0/(1.0+$ag/$al),1)
}

function Calc-ATR([object[]]$bars,[int]$p=14) {
    if ($bars.Count -lt 2){return 0.0}
    $trs=@()
    for ($i=1;$i -lt $bars.Count;$i++){
        $hl=$bars[$i].h-$bars[$i].l
        $hpc=[Math]::Abs($bars[$i].h-$bars[$i-1].c)
        $lpc=[Math]::Abs($bars[$i].l-$bars[$i-1].c)
        $trs+=[Math]::Max($hl,[Math]::Max($hpc,$lpc))
    }
    $n=[Math]::Min($p,$trs.Count)
    return [Math]::Round(($trs|Select-Object -Last $n|Measure-Object -Sum).Sum/$n,2)
}

function Calc-VWAP([object[]]$bars) {
    $tpv=0.0;$tv=0.0
    foreach($b in $bars){$tp=($b.h+$b.l+$b.c)/3.0;$tpv+=$tp*$b.v;$tv+=$b.v}
    if($tv -eq 0){return 0.0}
    return [Math]::Round($tpv/$tv,2)
}

function Send-TG([string]$msg) {
    $body='{"chat_id":"'+$TgChat+'","text":'+($msg|ConvertTo-Json)+'}'
    try{Invoke-RestMethod -Uri ("https://api.telegram.org/bot"+$TgToken+"/sendMessage") -Method POST -Body $body -ContentType "application/json"|Out-Null}
    catch{Write-Host ("TG-ERR: "+$_.Exception.Message)}
    Start-Sleep -Milliseconds 900
}

# ════════════════════════════════════════════════════
#  DATE / TIME ANCHORS
# ════════════════════════════════════════════════════

$nowUtc      = [DateTime]::UtcNow.ToUniversalTime()
$todayDate   = $nowUtc.ToString("yyyy-MM-dd")
$nowET       = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($nowUtc,"Eastern Standard Time")
$sessionOpen = [DateTime]::Parse($todayDate+"T13:30:00Z").ToUniversalTime()
$dailyStart  = "2026-01-01T00:00:00Z"   # enough history for swing analysis
$sessionSt   = $todayDate+"T13:30:00Z"
$pmStart     = $todayDate+"T09:00:00Z"
$expDate     = $todayDate.Substring(5,2)+"/"+$todayDate.Substring(8,2)

$EQ = "=" * 46
$DV = "-" * 44

Write-Host ("=== Signal Run @ "+$nowET.ToString("HH:mm")+" ET  "+$todayDate+" ===")

$summary = @()

# ════════════════════════════════════════════════════
#  PER-TICKER LOOP
# ════════════════════════════════════════════════════

foreach ($sym in $Tickers) {
    Write-Host ("`n--- "+$sym+" ---")

    # ── Daily bars ────────────────────────────────────────────
    $daily = @(Get-Bars $sym "1Day" $dailyStart 90)
    if ($daily.Count -lt 10) { Write-Host ($sym+": insufficient data"); continue }

    [double]$prevClose = [Math]::Round([double]$daily[-1].c,2)
    [double]$avgVol20  = ($daily|Select-Object -Last 20|ForEach-Object{[double]$_.v}|Measure-Object -Average).Average
    [double]$atrDaily  = Calc-ATR $daily 14
    [double[]]$dailyC  = @($daily|ForEach-Object{[double]$_.c})

    # Daily EMAs for swing context
    [double]$dEMA9  = Calc-EMA $dailyC 9
    [double]$dEMA21 = Calc-EMA $dailyC 21
    [double]$dEMA50 = Calc-EMA $dailyC 50
    [double]$dRSI   = Calc-RSI $dailyC 14

    # ── Last trade price ─────────────────────────────────────
    [double]$curP = Get-LastPrice $sym
    if ($curP -eq 0.0) { $curP = $prevClose }

    # ── Premarket bars ────────────────────────────────────────
    $pmBars = @(Get-Bars $sym "1Min" $pmStart 300)
    [bool]$hasPM   = ($pmBars.Count -gt 0)
    [double]$pmO   = if ($hasPM){[Math]::Round([double]$pmBars[0].o,2)}else{0.0}
    [double]$pmH   = if ($hasPM){($pmBars|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum}else{0.0}
    [double]$pmVol = if ($hasPM){($pmBars|ForEach-Object{[double]$_.v}|Measure-Object -Sum).Sum}else{0.0}
    $pmStr         = if ($hasPM){"O="+$pmO+"  H="+[Math]::Round($pmH,2)+"  Vol="+[int]$pmVol}else{"No premarket activity"}

    # ── Gap ───────────────────────────────────────────────────
    [double]$gapRef = if ($hasPM -and $pmO -gt 0){$pmO}else{$curP}
    [double]$gapPct = if ($prevClose -gt 0){[Math]::Round(($gapRef-$prevClose)/$prevClose*100.0,2)}else{0.0}
    $gapStr         = if ($gapPct -ge 0){"+" + $gapPct + "%"}else{$gapPct.ToString()+"%"}

    # ── Intraday session bars ─────────────────────────────────
    $idays        = @(Get-Bars $sym "5Min" $sessionSt 80)
    [bool]$hasID  = ($idays.Count -ge 2)

    # ── Blended closes (30 daily + intraday) for RSI/EMA ─────
    [double[]]$idC  = if ($hasID){@($idays|ForEach-Object{[double]$_.c})}else{@()}
    [double[]]$pmC2 = if ($hasPM){@($pmBars|ForEach-Object{[double]$_.c})}else{@()}
    [double[]]$blend= @($dailyC|Select-Object -Last 30)+$(if($hasID){$idC}elseif($hasPM){$pmC2}else{@()})

    # ── Intraday indicators ───────────────────────────────────
    [double]$iATR = if ($idays.Count -ge 14){Calc-ATR $idays 14}else{0.0}
    [double]$atr  = if ($iATR -gt ($atrDaily*0.3)){$iATR}else{$atrDaily}
    [double]$rsi  = Calc-RSI  $blend 14
    [double]$ema9 = Calc-EMA  $blend 9
    [double]$ema21= Calc-EMA  $blend 21
    [double]$ema50= Calc-EMA  $blend 50
    [double]$vwap = if ($hasID){Calc-VWAP $idays}elseif($hasPM){Calc-VWAP $pmBars}else{$prevClose}

    # RVOL (paced)
    $minsElapsed  = ($nowUtc - $sessionOpen).TotalMinutes
    $fracEl       = [Math]::Max([Math]::Min($minsElapsed/390.0,1.0),0.001)
    [double]$todayVol = if ($hasID){($idays|ForEach-Object{[double]$_.v}|Measure-Object -Sum).Sum}
                        elseif($hasPM){$pmVol}else{0.0}
    [double]$rvol = if($avgVol20 -gt 0 -and $todayVol -gt 0){[Math]::Round(($todayVol/$fracEl)/$avgVol20,2)}else{0.0}

    [bool]$abvEMA9 = ($curP -gt $ema9)
    [bool]$abvVWAP = ($curP -gt $vwap -and $vwap -gt 0)
    [string]$trend = if($ema9 -gt $ema21 -and $ema21 -gt $ema50){"UP"}elseif($ema9 -lt $ema21){"DOWN"}else{"NEUTRAL"}

    # Daily trend context for swing
    [string]$dTrend = if($dEMA9 -gt $dEMA21 -and $dEMA21 -gt $dEMA50){"UPTREND"}
                      elseif($dEMA9 -lt $dEMA21){"DOWNTREND"}else{"SIDEWAYS"}

    Write-Host ("  Price="+$curP+"  RSI="+$rsi+"  EMA9="+$ema9+"  ATR="+$atr+"  RVOL="+$rvol+"x  VWAP="+$vwap+"  Trend="+$trend)
    Write-Host ("  Daily: dRSI="+$dRSI+"  dEMA9="+$dEMA9+"  dTrend="+$dTrend)

    # ════════════════════════════════════════════════════
    #  STRATEGY ENGINE -- signals as pipe-delimited strings
    #  TYPE | ID | NAME | DIR | CONF | ACTION
    #  TYPE: OD = Opening Drive  |  0DTE  |  SWING
    # ════════════════════════════════════════════════════
    $raw = [System.Collections.Generic.List[string]]::new()

    # ── A. OPENING DRIVE SIGNALS ─────────────────────────────

    # S19A: Gap Breakout Opening Drive
    if ($gapPct -gt 3.0 -and $rvol -gt 1.2 -and $rsi -gt 52 -and $abvVWAP) {
        [int]$c=70
        if($gapPct -gt 10){$c+=10};if($gapPct -gt 20){$c+=5};if($gapPct -gt 50){$c+=5}
        if($rvol -gt 3.0){$c+=8};if($rvol -gt 8.0){$c+=5}
        if($rsi -gt 60){$c+=5};if($hasPM){$c+=2}
        $c=[Math]::Min($c,98)
        $raw.Add("OD|S19A|Opening Drive Gap Breakout|LONG|"+$c+"|Gap="+$gapPct+"% RVOL="+$rvol+"x. Enter on first 5-min pullback to VWAP.")
    }

    # S19B: Opening Drive Pullback Entry
    if ($gapPct -gt 2.0 -and $abvVWAP -and $rsi -gt 46 -and $rsi -lt 68 -and $rvol -gt 1.0) {
        [int]$c=65;if($rvol -gt 2.0){$c+=5};if($hasPM){$c+=3}
        $raw.Add("OD|S19B|Opening Drive Pullback|LONG|"+$c+"|Pullback to VWAP "+$vwap+". Buy the dip during opening drive window 9:30-9:50 AM.")
    }

    # S08: Opening Range Breakout
    if ($hasID -and $idays.Count -ge 2) {
        [double]$orH=($idays|Select-Object -First 2|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum
        [double]$orL=($idays|Select-Object -First 2|ForEach-Object{[double]$_.l}|Measure-Object -Minimum).Minimum
        if ($curP -gt $orH -and $rvol -gt 1.2) {
            [int]$c=72;if($rvol -gt 2.0){$c+=6};if($rvol -gt 5.0){$c+=4}
            $raw.Add("OD|S08|Opening Range Breakout|LONG|"+$c+"|Broke ORB high "+[Math]::Round($orH,2)+" with RVOL="+$rvol+"x. Momentum continuation.")
        } elseif ($curP -lt $orL -and $rvol -gt 1.2) {
            [int]$c=70;if($rvol -gt 2.0){$c+=6}
            $raw.Add("OD|S08|Opening Range Breakdown|SHORT|"+$c+"|Broke ORB low "+[Math]::Round($orL,2)+" with RVOL="+$rvol+"x.")
        }
    }

    # ── B. 0DTE SIGNALS ──────────────────────────────────────

    # S18: 9-EMA Cross Momentum (primary 0DTE setup)
    if ($atr -gt 0) {
        [double]$e9d=[Math]::Round([Math]::Abs($curP-$ema9)/$atr,2)
        if ($abvEMA9 -and $e9d -ge 0.3 -and $e9d -lt 5.0 -and $rsi -gt 48 -and $rsi -lt 80 -and $trend -eq "UP") {
            [int]$c=78;if($e9d -lt 1.5){$c+=5};if($rsi -gt 55 -and $rsi -lt 72){$c+=4}
            $c=[Math]::Min($c,95)
            $raw.Add("0DTE|S18|9-EMA Cross Momentum|LONG|CALL|"+$c+"|Price "+$e9d+"x ATR above EMA9 "+$ema9+". Enter 0DTE CALL on EMA9 retest.")
        } elseif (-not $abvEMA9 -and $e9d -ge 0.3 -and $e9d -lt 5.0 -and $rsi -gt 20 -and $rsi -lt 52 -and $trend -eq "DOWN") {
            [int]$c=75
            $raw.Add("0DTE|S18|9-EMA Cross Momentum|SHORT|PUT|"+$c+"|Price "+$e9d+"x ATR below EMA9 "+$ema9+". Enter 0DTE PUT on bounce to EMA9.")
        }
    }

    # S16: VWAP Momentum Thrust
    if ($abvVWAP -and $rvol -gt 1.5 -and $rsi -gt 52 -and $rsi -lt 76) {
        [int]$c=68;if($rvol -gt 3.0){$c+=7};if($rvol -gt 8.0){$c+=5}
        $raw.Add("0DTE|S16|VWAP Momentum Thrust|LONG|CALL|"+$c+"|RVOL="+$rvol+"x above VWAP "+$vwap+". Buy 0DTE CALL ATM.")
    }

    # S17: EMA9/EMA21 Squeeze
    if ($abvEMA9 -and $ema9 -gt $ema21 -and $rsi -gt 50 -and $rsi -lt 73 -and $rvol -gt 1.0) {
        [int]$c=65;if($rvol -gt 2.0){$c+=5}
        $raw.Add("0DTE|S17|EMA9-21 Trend Squeeze|LONG|CALL|"+$c+"|EMA9 "+$ema9+" > EMA21 "+$ema21+". Trend intact - 0DTE CALL.")
    }

    # S13: Momentum Breakout 0DTE
    if ($rsi -gt 60 -and $rvol -gt 2.0 -and $abvVWAP -and $gapPct -gt 2.0) {
        [int]$c=72;if($gapPct -gt 8){$c+=8};if($gapPct -gt 20){$c+=5};if($rvol -gt 5){$c+=5}
        $c=[Math]::Min($c,95)
        $raw.Add("0DTE|S13|Momentum Breakout|LONG|CALL|"+$c+"|RSI="+$rsi+" RVOL="+$rvol+"x gap="+$gapPct+"% breakout. 0DTE CALL momentum play.")
    }

    # S15: HOD Breakout
    if ($hasID) {
        [double]$hod=($idays|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum
        if ($curP -ge ($hod*0.999) -and $rvol -gt 1.5 -and $rsi -gt 55) {
            [int]$c=70;if($rvol -gt 3.0){$c+=7}
            $raw.Add("0DTE|S15|HOD Breakout|LONG|CALL|"+$c+"|Testing HOD "+[Math]::Round($hod,2)+" RVOL="+$rvol+"x. 0DTE CALL breakout.")
        }
    }

    # S01: VWAP Bounce
    if ($abvVWAP -and $rsi -gt 45 -and $rsi -lt 72) {
        [int]$c=60;if($rvol -gt 1.3){$c+=5}
        $raw.Add("0DTE|S01|VWAP Bounce|LONG|CALL|"+$c+"|Holding above VWAP "+$vwap+". 0DTE ATM CALL on VWAP touch.")
    }

    # S07: Overbought Fade (PUT setup)
    if ($rsi -gt 74 -and $rvol -gt 0.8) {
        [int]$c=65;if($rsi -gt 80){$c+=8};if($rsi -gt 85){$c+=5}
        $raw.Add("0DTE|S07|Overbought Exhaustion Fade|SHORT|PUT|"+$c+"|RSI="+$rsi+" overbought. 0DTE PUT -- wait for first red 5-min candle.")
    }

    # ── C. SWING SIGNALS (daily timeframe) ───────────────────

    # SW01: Swing Uptrend Continuation (daily EMA alignment + RSI dip-buy)
    if ($dTrend -eq "UPTREND" -and $dRSI -gt 40 -and $dRSI -lt 65 -and $curP -gt $dEMA21) {
        [int]$c=68;if($dRSI -gt 50){$c+=5};if($curP -gt $dEMA9){$c+=5}
        $raw.Add("SWING|SW01|Swing Uptrend Continuation|LONG|NA|"+$c+"|Daily EMA9 "+$dEMA9+" > EMA21 "+$dEMA21+" > EMA50 "+$dEMA50+". RSI="+$dRSI+" in buyable zone. Hold 3-5 days.")
    }

    # SW02: Swing Oversold Bounce (RSI<35 on daily, price near EMA50)
    if ($dRSI -lt 35 -and $curP -gt ($dEMA50 * 0.97) -and $curP -lt ($dEMA50 * 1.05)) {
        [int]$c=65;if($dRSI -lt 28){$c+=8}
        $raw.Add("SWING|SW02|Swing Oversold Bounce|LONG|NA|"+$c+"|Daily RSI="+$dRSI+" oversold near EMA50 "+$dEMA50+". Swing long entry -- target EMA21 "+$dEMA21+".")
    }

    # SW03: Swing Gap-and-Hold (gap>5% AND closes above dEMA9 on daily)
    if ($gapPct -gt 5.0 -and $curP -gt $dEMA9 -and $rvol -gt 1.5 -and $dRSI -gt 45) {
        [int]$c=72;if($gapPct -gt 15){$c+=8};if($gapPct -gt 30){$c+=5};if($rvol -gt 3){$c+=5}
        $c=[Math]::Min($c,95)
        $raw.Add("SWING|SW03|Swing Gap-and-Hold|LONG|NA|"+$c+"|Gap="+$gapPct+"% holding above dEMA9 "+$dEMA9+". Swing entry -- hold 2-4 days target +"+[Math]::Round($gapPct*0.5,1)+"% extension.")
    }

    # SW04: Swing Downtrend Short (daily downtrend, RSI bounce to sell)
    if ($dTrend -eq "DOWNTREND" -and $dRSI -gt 55 -and $dRSI -lt 70 -and $curP -lt $dEMA21) {
        [int]$c=65;if($dRSI -gt 60){$c+=5}
        $raw.Add("SWING|SW04|Swing Downtrend Short|SHORT|NA|"+$c+"|Daily downtrend. RSI="+$dRSI+" bounced to sell zone below EMA21 "+$dEMA21+". Swing short 2-3 days.")
    }

    # SW05: Swing Momentum Breakout (fresh 20-day high + volume)
    if ($daily.Count -ge 20) {
        [double]$high20 = ($daily|Select-Object -Last 20|ForEach-Object{[double]$_.h}|Measure-Object -Maximum).Maximum
        if ($curP -ge ($high20 * 0.998) -and $rvol -gt 1.5 -and $dRSI -gt 50) {
            [int]$c=75;if($rvol -gt 3){$c+=5};if($gapPct -gt 5){$c+=5}
            $raw.Add("SWING|SW05|Swing Momentum Breakout|LONG|NA|"+$c+"|At/near 20-day high "+[Math]::Round($high20,2)+". RVOL="+$rvol+"x breakout. Swing hold 5-10 days.")
        }
    }

    # ════════════════════════════════════════════════════
    #  FILTER & CONSENSUS (per signal type)
    # ════════════════════════════════════════════════════
    $fired   = @($raw|Where-Object{[int]($_.Split("|")[5]) -ge 55}|Sort-Object{[int]($_.Split("|")[5])} -Descending)
    $odSigs  = @($fired|Where-Object{$_.Split("|")[0] -eq "OD"})
    $dtSigs  = @($fired|Where-Object{$_.Split("|")[0] -eq "0DTE"})
    $swSigs  = @($fired|Where-Object{$_.Split("|")[0] -eq "SWING"})

    [int]$topOD  = if($odSigs.Count -gt 0){[int]($odSigs[0].Split("|")[5])}else{0}
    [int]$topDT  = if($dtSigs.Count -gt 0){[int]($dtSigs[0].Split("|")[5])}else{0}
    [int]$topSW  = if($swSigs.Count -gt 0){[int]($swSigs[0].Split("|")[5])}else{0}
    [int]$topAll = [Math]::Max($topOD,[Math]::Max($topDT,$topSW))

    [bool]$consensus = ($fired.Count -ge 2) -or ($topAll -ge 78)
    [int]$longCt  = @($fired|Where-Object{$_.Split("|")[3] -eq "LONG"}).Count
    [int]$shortCt = @($fired|Where-Object{$_.Split("|")[3] -eq "SHORT"}).Count
    [string]$dir  = if($longCt -ge $shortCt){"LONG"}else{"SHORT"}

    Write-Host ("  Sigs: OD="+$odSigs.Count+" 0DTE="+$dtSigs.Count+" SWING="+$swSigs.Count+"  TopConf="+$topAll+"%  Consensus="+$consensus+"  Dir="+$dir)

    # ════════════════════════════════════════════════════
    #  PRICE LEVELS (all via fmt -- no interpolation)
    # ════════════════════════════════════════════════════
    [double]$ep = $curP
    [double]$aU = if($atr -gt 0.01){$atr}else{$ep*0.01}

    [string]$sEntry=""; [string]$sStop=""; [string]$sT1=""; [string]$sT2=""; [string]$sT3=""
    [string]$swStop=""; [string]$swT1=""; [string]$swT2=""
    [string]$optType="CALL"; [string]$optDir="BUY CALLS"
    [int]$strike=[int]([Math]::Round($ep/5.0)*5)

    if ($dir -eq "LONG") {
        $sEntry  = fmt $ep
        $sStop   = fmt ($ep - $aU)
        $sT1     = fmt ($ep + $aU*1.0)
        $sT2     = fmt ($ep + $aU*2.0)
        $sT3     = fmt ($ep + $aU*3.0)
        # Swing targets use 2x and 4x ATR (wider)
        $swStop  = fmt ($ep - $atrDaily*1.5)
        $swT1    = fmt ($ep + $atrDaily*2.0)
        $swT2    = fmt ($ep + $atrDaily*4.0)
        $optType = "CALL"; $optDir = "BUY CALLS"
    } else {
        $sEntry  = fmt $ep
        $sStop   = fmt ($ep + $aU)
        $sT1     = fmt ($ep - $aU*1.0)
        $sT2     = fmt ($ep - $aU*2.0)
        $sT3     = fmt ($ep - $aU*3.0)
        $swStop  = fmt ($ep + $atrDaily*1.5)
        $swT1    = fmt ($ep - $atrDaily*2.0)
        $swT2    = fmt ($ep - $atrDaily*4.0)
        $optType = "PUT"; $optDir = "BUY PUTS"
    }

    # ════════════════════════════════════════════════════
    #  BUILD TELEGRAM MESSAGE
    #  ALL prices via + concatenation, ZERO price vars in ""
    # ════════════════════════════════════════════════════
    [string]$alertHdr = if($consensus){">>> "+$dir+" ALERT FIRES <<<"}else{"WATCH - below consensus"}

    # Opening Drive block
    $odBlock  = "--- OPENING DRIVE (Intraday) ---`n"
    if ($odSigs.Count -gt 0) {
        foreach ($s in $odSigs) {
            $p=$s.Split("|")
            $odBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $odBlock += "  Direction  : "+$p[3]+"`n"
            $odBlock += "  Confidence : "+$p[5]+"%`n"
            $odBlock += "  Action     : "+$p[6]+"`n"
        }
    } else { $odBlock += "  None fired above threshold`n" }

    # 0DTE block
    $dtBlock  = "--- 0DTE OPTIONS (exp today "+$expDate+") ---`n"
    if ($dtSigs.Count -gt 0) {
        foreach ($s in $dtSigs) {
            $p=$s.Split("|")
            [string]$optLabel = $p[4]   # CALL or PUT from index 4
            $dtBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $dtBlock += "  0DTE Type  : "+$optLabel+"S (0DTE "+$optLabel+"S exp "+$expDate+")`n"
            $dtBlock += "  Direction  : "+$p[3]+"`n"
            $dtBlock += "  Confidence : "+$p[5]+"%`n"
            $dtBlock += "  Action     : "+$p[6]+"`n"
        }
    } else { $dtBlock += "  None fired above threshold`n" }

    # Swing block
    $swBlock  = "--- SWING TRADE (2-10 day hold) ---`n"
    if ($swSigs.Count -gt 0) {
        foreach ($s in $swSigs) {
            $p=$s.Split("|")
            $swBlock += "["+$p[1]+"] "+$p[2]+"`n"
            $swBlock += "  Direction  : "+$p[3]+"`n"
            $swBlock += "  Confidence : "+$p[5]+"%`n"
            $swBlock += "  Basis      : "+$p[6]+"`n"
        }
    } else { $swBlock += "  None fired above threshold`n" }

    # Price levels block
    $lvBlock = ""
    if ($consensus) {
        $lvBlock  = $DV+"`n"
        $lvBlock += "DIRECTION   : "+$dir+"`n"
        $lvBlock += "ENTRY       : "+$sEntry+"  (confirm at market open)`n"
        $lvBlock += "STOP        : "+$sStop+"  (1x ATR hard stop)`n"
        $lvBlock += "TARGET 1    : "+$sT1+"`n"
        $lvBlock += "TARGET 2    : "+$sT2+"`n"
        $lvBlock += "TARGET 3    : "+$sT3+"`n"
        $lvBlock += "R:R         : 1:2 minimum`n"
        $lvBlock += $DV+"`n"
        $lvBlock += "0DTE OPTIONS (exp "+$expDate+"):`n"
        $lvBlock += "  Trade Type : 0DTE "+$optType+"S`n"
        $lvBlock += "  Strike     : ~"+( fmt $strike)+"  (ATM)`n"
        $lvBlock += "  Delta      : 0.40-0.55 ATM`n"
        $lvBlock += "  Entry Rule : Confirm "+$dir+" at 9:35 AM open`n"
        $lvBlock += "  Exit Rule  : T1 OR 11:00 AM whichever comes first`n"
        $lvBlock += $DV+"`n"
        $lvBlock += "SWING LEVELS (2-5 day hold):`n"
        $lvBlock += "  Entry      : "+$sEntry+"`n"
        $lvBlock += "  Swing Stop : "+$swStop+"  (1.5x daily ATR)`n"
        $lvBlock += "  Swing T1   : "+$swT1+"  (2x daily ATR)`n"
        $lvBlock += "  Swing T2   : "+$swT2+"  (4x daily ATR)`n"
    }

    # Full message assembly -- NO price var inside double-quotes
    $msg  = $EQ+"`n"
    $msg += $sym+"  |  "+$alertHdr+"`n"
    $msg += $EQ+"`n"
    $msg += "PREV CLOSE  : "+(fmt $prevClose)+"`n"
    $msg += "LAST PRICE  : "+(fmt $curP)+"`n"
    if ($hasPM) {
        $msg += "PM DATA     : "+$pmStr+"`n"
        $msg += "GAP         : "+$gapStr+"`n"
    } else {
        $msg += "PM DATA     : No premarket activity`n"
    }
    $msg += $DV+"`n"
    $msg += "RSI  : "+$rsi+"    TREND : "+$trend+"`n"
    $msg += "EMA9 : "+$ema9+"  EMA21 : "+$ema21+"`n"
    $msg += "EMA50: "+$ema50+"  ATR  : "+$atr+"`n"
    $msg += "RVOL : "+$rvol+"x   VWAP : "+$vwap+"`n"
    $msg += "Daily: dRSI="+$dRSI+"  dTrend="+$dTrend+"`n"
    $msg += $DV+"`n"
    $msg += $odBlock
    $msg += $DV+"`n"
    $msg += $dtBlock
    $msg += $DV+"`n"
    $msg += $swBlock
    $msg += $lvBlock
    $msg += $EQ+"`n"
    $msg += "Run: "+$nowET.ToString("HH:mm")+" ET  |  "+$sym

    Send-TG $msg

    # Summary row
    $statusStr = if($consensus){$dir+" ALERT (OD:"+$odSigs.Count+" 0DTE:"+$dtSigs.Count+" SW:"+$swSigs.Count+" top:"+$topAll+"%)"}else{"WATCH (OD:"+$odSigs.Count+" 0DTE:"+$dtSigs.Count+" SW:"+$swSigs.Count+")"}
    $summary += [PSCustomObject]@{
        Sym=$sym; Status=$statusStr; Price=$curP; Gap=$gapStr; RVOL=$rvol; RSI=$rsi
        T1=$sT1; T2=$sT2; T3=$sT3; SwT1=$swT1; SwT2=$swT2
    }
}

# ════════════════════════════════════════════════════
#  SUMMARY MESSAGE
# ════════════════════════════════════════════════════
$sumMsg  = $EQ+"`n"
$sumMsg += "MASTER SIGNAL SUMMARY"+"`n"
$sumMsg += $nowET.ToString("HH:mm")+" ET  "+$todayDate+"`n"
$sumMsg += $EQ+"`n"
foreach ($r in $summary) {
    $sumMsg += $r.Sym.PadRight(6)+" | "+$r.Status+"`n"
    $sumMsg += "  Price="+(fmt $r.Price)+"  Gap="+$r.Gap+"  RVOL="+$r.RVOL+"x  RSI="+$r.RSI+"`n"
    if ($r.T1 -ne "") {
        $sumMsg += "  Intraday: T1="+$r.T1+"  T2="+$r.T2+"  T3="+$r.T3+"`n"
        $sumMsg += "  Swing:    T1="+$r.SwT1+"  T2="+$r.SwT2+"`n"
    }
}
$sumMsg += $EQ
Send-TG $sumMsg

Write-Host ("`n=== Complete. "+$summary.Count+" tickers processed ===")
$summary | Format-Table Sym,Status,Price,Gap,RVOL,RSI,T1,T2 -AutoSize
