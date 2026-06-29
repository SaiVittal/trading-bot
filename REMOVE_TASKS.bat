@echo off
echo Removing all TradingBot scheduled tasks...
schtasks /delete /tn "TradingBot_CrossAlerts"      /f 2>nul
schtasks /delete /tn "TradingBot_IntradaySignals"  /f 2>nul
schtasks /delete /tn "TradingBot_PreMarket_0903"   /f 2>nul
schtasks /delete /tn "TradingBot_PreMarket_0918"   /f 2>nul
schtasks /delete /tn "TradingBot_PreMarket_0934"   /f 2>nul
schtasks /delete /tn "TradingBot_OD_Run1"          /f 2>nul
schtasks /delete /tn "TradingBot_OD_Run2"          /f 2>nul
schtasks /delete /tn "TradingBot_OD_Run3"          /f 2>nul
echo All tasks removed.
pause
