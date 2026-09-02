# Exit if outside trading hours (10:00 AM - 3:45 PM ET, Mon-Fri)
$now = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Eastern Standard Time')
$dow = $now.DayOfWeek
if ($dow -eq 'Saturday' -or $dow -eq 'Sunday') { exit 0 }
$t = $now.TimeOfDay
if ($t -lt [TimeSpan]'09:00' -or $t -gt [TimeSpan]'15:45') { exit 0 }

& 'C:\Users\sdlr2\Downloads\trading-bot\scripts\run_signals.ps1' `
    -Tickers @('APP','TSLA','NVDA','QQQ','SPY','META','MSFT','AMZN','AAPL','INTC','NOW','HOOD','PLTR','NFLX','NBIS','AMD','GOOGL','DELL','NTAP','ZS','LULU','SNOW','CRM','AVGO','CRDO','HPE') `
    -ApiKey 'PKEQAQFOVYKIWW64RCEYJJD7N4' `
    -ApiSecret 'Hxc6xXX3VRc25t6mx1r3HEE9u5WzYVrucWjKVyw7j1u4' `
    -TelegramToken '8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw' `
    -ChatId '-4999357279'
