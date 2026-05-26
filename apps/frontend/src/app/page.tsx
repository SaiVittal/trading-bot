"use client";

import { useEffect, useRef, useState } from "react";
import { 
  TrendingUp, 
  TrendingDown, 
  Activity, 
  Bell, 
  Terminal, 
  Zap, 
  ArrowUpRight, 
  Cpu, 
  Radio,
  Flame,
  Target,
  Compass,
  Sparkles,
  BarChart3,
  Search,
  Plus,
  Trash2,
  Bookmark
} from "lucide-react";

// Types
interface TickData {
  symbol: string;
  price: number;
  volume: number;
  timestamp: number;
}

interface CandleData {
  symbol: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  timestamp: number;
}

interface AlertData {
  symbol: string;
  action: "BUY" | "SELL";
  price: number;
  rsi: number;
  vwap: number;
  stc: string;
  stop: number;
  t1: number;
  t2: number;
  message: string;
  ai_insight: string;
  timestamp: number;
}

interface LogLine {
  text: string;
  type: "system" | "tick" | "candle" | "alert" | "error";
  time: string;
}

export default function Dashboard() {
  // State variables
  const [connected, setConnected] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState<string>("TSLA");
  const [watchlist, setWatchlist] = useState<string[]>([
    "TSLA", "AAPL", "NVDA", "SPY", "MSFT", "NBIS", "META", "ASML", "COST", "AMD", "QCOM", "MU", "SPX"
  ]);
  const [watchlistPrices, setWatchlistPrices] = useState<Record<string, number>>({});
  
  const [searchInput, setSearchInput] = useState<string>("");
  const [currentPrice, setCurrentPrice] = useState<number>(0);
  const [priceDiff, setPriceDiff] = useState<"up" | "down" | "flat">("flat");
  const [activeCandle, setActiveCandle] = useState<CandleData>({
    symbol: "TSLA", open: 0, high: 0, low: 0, close: 0, volume: 0, timestamp: 0
  });
  const [closedCandles, setClosedCandles] = useState<CandleData[]>([]);
  const [signals, setSignals] = useState<AlertData[]>([]);
  const [latestAIInsight, setLatestAIInsight] = useState<string>(
    "Awaiting real-time trade signals. OpenAI Quant Engine is fully armed and listening to active indicators..."
  );
  
  const [telemetry, setTelemetry] = useState<LogLine[]>([]);
  
  const [activeToast, setActiveToast] = useState<{
    id: string;
    platform: "telegram";
    action: "BUY" | "SELL";
    price: number;
    symbol: string;
  } | null>(null);

  // Refs for canvas and callbacks
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const closedCandlesRef = useRef<CandleData[]>([]);
  const activeCandleRef = useRef<CandleData>({
    symbol: "TSLA", open: 0, high: 0, low: 0, close: 0, volume: 0, timestamp: 0
  });
  const currentPriceRef = useRef<number>(0);
  const selectedSymbolRef = useRef<string>("TSLA");
  const wsRef = useRef<WebSocket | null>(null);

  // Synchronize dynamic refs to avoid stale closures in event loops
  useEffect(() => {
    selectedSymbolRef.current = selectedSymbol;
    
    // Redraw chart when active symbol shifts
    drawChart();
    
    // Reset active candle baseline
    const filteredClosed = closedCandles.filter(c => c.symbol === selectedSymbol);
    if (filteredClosed.length > 0) {
      const lastClosed = filteredClosed[filteredClosed.length - 1];
      setCurrentPrice(lastClosed.close);
      currentPriceRef.current = lastClosed.close;
      setActiveCandle({
        symbol: selectedSymbol, open: 0, high: 0, low: 0, close: 0, volume: 0, timestamp: 0
      });
    } else {
      const livePrice = watchlistPrices[selectedSymbol] || 0;
      setCurrentPrice(livePrice);
      currentPriceRef.current = livePrice;
      setActiveCandle({
        symbol: selectedSymbol, open: 0, high: 0, low: 0, close: 0, volume: 0, timestamp: 0
      });
    }
  }, [selectedSymbol]);

  useEffect(() => {
    closedCandlesRef.current = closedCandles;
    drawChart();
  }, [closedCandles]);

  useEffect(() => {
    activeCandleRef.current = activeCandle;
    drawChart();
  }, [activeCandle]);

  // Safe client telemetry logger
  const logSystem = (text: string, type: LogLine["type"]) => {
    const time = new Date().toLocaleTimeString();
    setTelemetry(prev => {
      const lines = [...prev, { text, type, time }];
      if (lines.length > 30) lines.shift();
      return lines;
    });
  };

  // Hydration safety line
  useEffect(() => {
    setTelemetry([
      { text: "Initializing dynamic multi-symbol trading core...", type: "system", time: new Date().toLocaleTimeString() }
    ]);
  }, []);

  // Animated popup dispatchers
  const triggerToasts = (signal: AlertData) => {
    const id = Math.random().toString();
    setActiveToast({
      id,
      platform: "telegram",
      action: signal.action,
      price: signal.price,
      symbol: signal.symbol
    });
  };

  // Dynamic Ticker Searched Subscription hook
  const handleSearchSubscribe = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const symbol = searchInput.toUpperCase().trim();
    if (!symbol) return;

    // 1. Add to local watchlist state if missing
    if (!watchlist.includes(symbol)) {
      setWatchlist(prev => [...prev, symbol]);
    }

    // 2. Switch main view focus to searched asset
    setSelectedSymbol(symbol);
    setSearchInput("");

    // 3. Emit subscription signal over standard WebSocket connection
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "search", symbol }));
      logSystem(`Subscribing to real-time ticker stream for searched asset: ${symbol}`, "system");
    } else {
      logSystem(`Cannot send subscription request. WebSockets offline. Ticker locally added.`, "error");
    }
  };

  const handleRemoveSymbol = (sym: string, e: React.MouseEvent) => {
    e.stopPropagation(); // Avoid triggering selected symbol change
    if (sym === "TSLA" && watchlist.length === 1) return; // Prevent empty watchlist
    
    const updated = watchlist.filter(s => s !== sym);
    setWatchlist(updated);
    
    if (selectedSymbol === sym) {
      setSelectedSymbol(updated[0] || "TSLA");
    }
    logSystem(`Removed asset ${sym} from local workspace watchlist.`, "system");
  };

  // WebSockets client implementation
  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;
    let reconnectAttempts = 0;

    const connect = () => {
      const envWsUrl = process.env.NEXT_PUBLIC_WS_URL;
      let wsUrl: string;

      if (envWsUrl) {
        let url = envWsUrl;
        if (url.startsWith("http://")) {
          url = url.replace("http://", "ws://");
        } else if (url.startsWith("https://")) {
          url = url.replace("https://", "wss://");
        }
        if (!url.includes("/api/v1/ws")) {
          const cleanUrl = url.endsWith("/") ? url.slice(0, -1) : url;
          url = `${cleanUrl}/api/v1/ws`;
        }
        wsUrl = url;
      } else {
        const wsProtocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss" : "ws";
        const wsHost = typeof window !== "undefined" ? window.location.hostname : "localhost";
        wsUrl = `${wsProtocol}://${wsHost}:8000/api/v1/ws`;
      }

      ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttempts = 0;
        setConnected(true);
        logSystem("Standard WebSocket connection handshake successful. Feed bound to Redis.", "system");

        // Restore subscriptions for active watchlist on reboot
        watchlist.forEach(sym => {
          ws.send(JSON.stringify({ type: "search", symbol: sym }));
        });
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          const channel = payload.channel;
          const data = payload.data;

          if (channel === "market:ticks") {
            const tick = data as TickData;
            
            // 1. Maintain watchlist prices state
            setWatchlistPrices(prev => ({ ...prev, [tick.symbol]: tick.price }));

            // 2. Process active chart ticking price updates
            if (tick.symbol === selectedSymbolRef.current) {
              if (currentPriceRef.current > 0) {
                if (tick.price > currentPriceRef.current) setPriceDiff("up");
                else if (tick.price < currentPriceRef.current) setPriceDiff("down");
              }

              currentPriceRef.current = tick.price;
              setCurrentPrice(tick.price);

              setActiveCandle(prev => {
                const open = prev.open === 0 ? tick.price : prev.open;
                const high = prev.high === 0 ? tick.price : Math.max(prev.high, tick.price);
                const low = prev.low === 0 ? tick.price : Math.min(prev.low, tick.price);
                const volume = parseFloat((prev.volume + tick.volume).toFixed(6));
                
                return {
                  symbol: tick.symbol,
                  open,
                  high,
                  low,
                  close: tick.price,
                  volume,
                  timestamp: tick.timestamp
                };
              });
            }

            logSystem(`[TICK] ${tick.symbol}: $${tick.price.toFixed(2)} (Shares: ${tick.volume})`, "tick");

          } else if (channel === "market:candles") {
            const candle = data as CandleData;
            logSystem(`[CANDLE] ${candle.symbol} Closed 5s: O:${candle.open} H:${candle.high} L:${candle.low} C:${candle.close}`, "candle");

            setClosedCandles(prev => {
              const updated = [...prev, candle];
              if (updated.length > 200) updated.shift();
              return updated;
            });

            if (candle.symbol === selectedSymbolRef.current) {
              setActiveCandle({
                symbol: candle.symbol, open: 0, high: 0, low: 0, close: 0, volume: 0, timestamp: 0
              });
            }

          } else if (channel === "signals:alerts") {
            const signal = data as AlertData;
            logSystem(`[ALERT] Strategy crossover fired: ${signal.message}`, "alert");

            setSignals(prev => [signal, ...prev].slice(0, 15));
            if (signal.symbol === selectedSymbolRef.current) {
              setLatestAIInsight(signal.ai_insight);
            }
            triggerToasts(signal);
          }
        } catch (e) {
          logSystem(`[ERROR] Processing WebSockets packet: ${(e as Error).message}`, "error");
        }
      };

      ws.onerror = () => {
        logSystem("WebSocket socket connection error identified.", "error");
      };

      ws.onclose = () => {
        setConnected(false);
        logSystem("WebSocket pipeline detached. Starting backoff reconnect...", "error");
        
        reconnectTimeout = setTimeout(() => {
          reconnectAttempts++;
          connect();
        }, Math.min(1000 * reconnectAttempts + 1000, 10000));
      };
    };

    connect();

    return () => {
      if (ws) ws.close();
      clearTimeout(reconnectTimeout);
    };
  }, []);

  // HTML5 Canvas chart renderer
  const drawChart = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    
    if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.scale(dpr, dpr);
    }

    ctx.clearRect(0, 0, width, height);

    // Filter closed candles by active focused symbol
    const candles = closedCandlesRef.current.filter(c => c.symbol === selectedSymbolRef.current);
    const active = activeCandleRef.current;
    
    if (active && active.symbol === selectedSymbolRef.current && active.open > 0) {
      candles.push(active);
    }

    if (candles.length === 0) {
      ctx.fillStyle = "rgba(148, 163, 184, 0.45)";
      ctx.font = "500 13px system-ui";
      ctx.textAlign = "center";
      ctx.fillText(`Aggregating real-time ${selectedSymbolRef.current} price indicators. Waiting for tick feed...`, width / 2, height / 2);
      return;
    }

    // Min/Max price boundaries
    let maxP = -Infinity;
    let minP = Infinity;
    candles.forEach(c => {
      maxP = Math.max(maxP, c.high);
      minP = Math.min(minP, c.low);
    });

    // EMA-5 overlay indicator
    const emaPoints: { idx: number; val: number }[] = [];
    const windowSize = 5;
    for (let i = 0; i < candles.length; i++) {
      if (i >= windowSize - 1) {
        const sum = candles.slice(i - windowSize + 1, i + 1).reduce((acc, c) => acc + c.close, 0);
        emaPoints.push({ idx: i, val: sum / windowSize });
      }
    }

    const priceRange = maxP - minP || 2;
    maxP += priceRange * 0.15;
    minP -= priceRange * 0.15;

    const padLeft = 10;
    const padRight = 75;
    const padTop = 30;
    const padBottom = 30;

    const cW = width - padLeft - padRight;
    const cH = height - padTop - padBottom;

    const getX = (idx: number) => {
      const cSize = cW / 15;
      return padLeft + idx * cSize + cSize / 2;
    };

    const getY = (price: number) => {
      return padTop + cH * (1 - (price - minP) / (maxP - minP));
    };

    // Draw horizontal grid lines
    ctx.strokeStyle = "rgba(255, 255, 255, 0.02)";
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = padTop + (cH * i) / 4;
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(width - padRight, y);
      ctx.stroke();

      const val = maxP - ((maxP - minP) * i) / 4;
      ctx.fillStyle = "rgba(148, 163, 184, 0.35)";
      ctx.font = "400 10px monospace";
      ctx.textAlign = "left";
      ctx.fillText(val.toFixed(2), width - padRight + 8, y + 3);
    }

    // Draw Candles
    const barWidth = Math.max((cW / 15) * 0.6, 6);
    candles.slice(-15).forEach((c, idx) => {
      const x = getX(idx);
      const yO = getY(c.open);
      const yC = getY(c.close);
      const yH = getY(c.high);
      const yL = getY(c.low);

      const bullish = c.close >= c.open;
      const themeColor = bullish ? "#10b981" : "#ef4444";
      const wickColor = bullish ? "rgba(16, 185, 129, 0.4)" : "rgba(239, 68, 68, 0.4)";

      // Draw Wick
      ctx.strokeStyle = wickColor;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x, yH);
      ctx.lineTo(x, yL);
      ctx.stroke();

      // Draw Candle Body
      ctx.fillStyle = themeColor;
      const bH = Math.abs(yC - yO) || 2;
      const yBody = Math.min(yO, yC);
      ctx.fillRect(x - barWidth / 2, yBody, barWidth, bH);

      // Pulse glow on active candle
      if (idx === Math.min(candles.length, 15) - 1 && activeCandleRef.current.open > 0) {
        ctx.shadowColor = themeColor;
        ctx.shadowBlur = 6;
        ctx.fillRect(x - barWidth / 2, yBody, barWidth, bH);
        ctx.shadowBlur = 0;
      }
    });

    // Draw EMA Indicator Line (Neon Purple)
    if (emaPoints.length > 0) {
      ctx.strokeStyle = "#c084fc";
      ctx.lineWidth = 2;
      ctx.shadowColor = "#c084fc";
      ctx.shadowBlur = 4;
      
      ctx.beginPath();
      emaPoints.slice(-15).forEach((pt, i) => {
        const x = getX(i);
        const y = getY(pt.val);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.shadowBlur = 0;
    }
  };

  // Filter alerts specifically for selected active symbol
  const activeAlerts = signals.filter(s => s.symbol === selectedSymbol);

  return (
    <div className="flex-1 relative overflow-hidden min-h-screen pb-12 bg-[#020617]">
      
      {/* Cybernetic Orbs */}
      <div className="absolute top-[-12vw] right-[-8vw] w-[50vw] h-[50vw] rounded-full bg-indigo-500 opacity-[0.14] blur-[150px] pointer-events-none"></div>
      <div className="absolute bottom-[-15vw] left-[-10vw] w-[50vw] h-[50vw] rounded-full bg-cyan-400 opacity-[0.12] blur-[150px] pointer-events-none"></div>

      {/* Main Core Container */}
      <div className="max-w-[1640px] mx-auto p-4 md:p-8 flex flex-col gap-6 relative z-10">
        
        {/* State-of-the-Art Header Panel */}
        <header className="flex flex-col md:flex-row justify-between md:items-center gap-4 p-6 bg-slate-900/65 border border-slate-800/80 backdrop-blur-3xl rounded-2xl shadow-2xl">
          <div className="flex items-center gap-4">
            <div className="relative">
              <span className={`flex h-4 w-4 rounded-full ${connected ? "bg-emerald-500" : "bg-rose-500"}`} />
              {connected && (
                <span className="animate-ping absolute inline-flex h-4 w-4 rounded-full bg-emerald-400 opacity-75 top-0" />
              )}
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
                Trading Intelligence <span className="font-extralight text-indigo-400">Platform</span>
              </h1>
              <p className="text-xs text-slate-400 font-mono">
                Asset Feed Scope: <span className="text-indigo-400 font-semibold">{selectedSymbol}</span> | Dynamic Multi-Symbol quote engine
              </p>
            </div>
          </div>
          
          <div className="flex flex-wrap items-center gap-2.5">
            <span className={`badge flex items-center gap-1.5 font-mono text-xs px-3.5 py-1.5 rounded-full ${
              connected 
                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" 
                : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
            }`}>
              <Radio size={12} className={connected ? "animate-pulse text-emerald-400" : ""} />
              {connected ? "LIVE MULTI-FEED CONNECTED" : "BROADCASTER DISCONNECTED"}
            </span>
            <span className="badge bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 px-3.5 py-1.5 rounded-full flex items-center gap-1.5 font-mono text-xs">
              <Cpu size={12} />
              OpenAI GPT-4o Insights Active
            </span>
          </div>
        </header>

        {/* Real-time Statistics Strip */}
        <section className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl backdrop-blur flex justify-between items-center">
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Active Asset Price ({selectedSymbol})</span>
              <span className={`font-mono text-2xl font-bold tracking-tight transition-colors duration-300 ${
                priceDiff === "up" ? "text-emerald-400" : priceDiff === "down" ? "text-rose-400" : "text-white"
              }`}>
                {currentPrice > 0 
                  ? `$${currentPrice.toLocaleString("en-US", { minimumFractionDigits: 2 })}` 
                  : (watchlistPrices[selectedSymbol] !== undefined
                      ? `$${watchlistPrices[selectedSymbol].toLocaleString("en-US", { minimumFractionDigits: 2 })}` 
                      : "Loading...")}
              </span>
            </div>
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center font-bold ${
              priceDiff === "up" ? "bg-emerald-500/10 text-emerald-400" : priceDiff === "down" ? "bg-rose-500/10 text-rose-400" : "bg-slate-800 text-slate-500"
            }`}>
              {priceDiff === "up" ? "▲" : priceDiff === "down" ? "▼" : "—"}
            </div>
          </div>

          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl backdrop-blur flex flex-col justify-center">
            <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Active Indicators Volatility</span>
            <span className="text-lg font-mono font-semibold text-slate-300 mt-1 flex items-center gap-2">
              <Activity size={14} className="text-cyan-400" />
              {activeAlerts.length > 0 ? "Momentum Signal Active" : "Stochastic Tracking"}
            </span>
          </div>

          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl backdrop-blur flex flex-col justify-center">
            <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Session VWAP Floor</span>
            <span className="text-lg font-mono font-semibold text-violet-400 mt-1 flex items-center gap-2">
              <BarChart3 size={14} className="text-violet-400" />
              ${activeAlerts.length > 0 && activeAlerts[0]?.vwap != null ? Number(activeAlerts[0].vwap).toFixed(2) : (currentPrice * 0.998).toFixed(2)}
            </span>
          </div>

          <div className="p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-xl backdrop-blur flex flex-col justify-center">
            <span className="text-[10px] uppercase font-bold text-indigo-400 tracking-wider flex items-center gap-1">
              <Sparkles size={10} className="animate-pulse" /> OpenAI Quant Sentiment
            </span>
            <span className="text-xs font-semibold text-indigo-200 mt-1 line-clamp-1">
              {activeAlerts.length > 0 ? `${activeAlerts[0].action} Signal Fired` : "Awaiting Strategy Crossover"}
            </span>
          </div>
        </section>

        {/* Dashboard Grid Layout */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          
          {/* Watchlist Sidebar Column (New Feature!) */}
          <section className="bg-slate-900/65 border border-slate-800/80 rounded-3xl shadow-2xl flex flex-col p-6 gap-5 backdrop-blur-3xl lg:col-span-1">
            <div className="flex flex-col gap-1.5">
              <h2 className="text-md font-semibold text-slate-100 flex items-center gap-2">
                <Bookmark size={16} className="text-indigo-400" />
                Live Watchlist
              </h2>
              <p className="text-xs text-slate-400">Search and manage active stock quotes</p>
            </div>

            {/* Dynamic Stock Search Form */}
            <form onSubmit={handleSearchSubscribe} className="relative flex items-center">
              <input
                type="text"
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                placeholder="Search ticker (e.g. AMD)..."
                className="w-full pl-10 pr-10 py-2.5 rounded-xl border border-slate-800/80 bg-slate-950/80 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/80 transition-colors"
              />
              <Search size={14} className="absolute left-3.5 text-slate-500" />
              <button 
                type="submit"
                className="absolute right-2.5 w-6 h-6 rounded-lg bg-indigo-500 hover:bg-indigo-600 flex items-center justify-center transition-colors text-white"
              >
                <Plus size={14} />
              </button>
            </form>

            {/* Watchlist Card Loop */}
            <div className="flex flex-col gap-2.5 overflow-y-auto max-h-[460px] pr-1">
              {watchlist.map(sym => {
                const active = sym === selectedSymbol;
                const price = watchlistPrices[sym];
                
                return (
                  <div
                    key={sym}
                    onClick={() => setSelectedSymbol(sym)}
                    className={`p-3.5 rounded-xl border transition-all duration-300 cursor-pointer flex justify-between items-center ${
                      active 
                        ? "bg-gradient-to-r from-indigo-500/15 via-purple-500/10 to-transparent border-indigo-500/50 shadow-indigo-500/5" 
                        : "bg-slate-950/30 border-slate-800/80 hover:bg-slate-950/50"
                    }`}
                  >
                    <div className="flex flex-col gap-0.5">
                      <span className={`font-mono text-sm font-extrabold tracking-wider ${
                        active ? "text-indigo-300" : "text-slate-300"
                      }`}>
                        {sym}
                      </span>
                      <span className="text-[9px] text-slate-500 uppercase font-mono">Stock Feed</span>
                    </div>

                    <div className="flex items-center gap-3 font-mono">
                      <span className={`text-xs font-semibold font-mono ${
                        active ? "text-cyan-400" : "text-slate-300"
                      }`}>
                        {price !== undefined ? `$${price.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "Loading..."}
                      </span>

                      {/* Remove Button for added tickers */}
                      <button
                        onClick={(e) => handleRemoveSymbol(sym, e)}
                        className="p-1.5 rounded-lg bg-slate-900/85 hover:bg-rose-500/10 border border-slate-800/40 hover:border-rose-500/20 text-slate-500 hover:text-rose-400 transition-all duration-300"
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
          
          {/* Chart Wrapper Column */}
          <div className="lg:col-span-2 flex flex-col gap-6">
            
            {/* HTML5 Candlestick Chart Card */}
            <section className="bg-slate-900/65 border border-slate-800/80 rounded-3xl shadow-2xl flex flex-col overflow-hidden backdrop-blur-3xl">
              <div className="p-6 border-b border-slate-800 flex justify-between items-center gap-4">
                <div className="flex flex-col">
                  <h2 className="text-md font-semibold text-slate-100 flex items-center gap-2">
                    <BarChart3 size={16} className="text-cyan-400" />
                    {selectedSymbol} Technical Chart
                  </h2>
                  <p className="text-xs text-slate-400 font-mono flex items-center gap-1">
                    5-second interval aggregates with EMA-5 Overlay
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                  <span className="text-[10px] font-bold text-cyan-400 tracking-wider uppercase">{selectedSymbol} STREAMING</span>
                </div>
              </div>

              {/* Chart canvas */}
              <div className="p-6 h-[380px] relative bg-gradient-to-b from-transparent to-slate-950/15">
                <canvas ref={canvasRef} className="w-full h-full" />
              </div>

              {/* Candle details grid */}
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-px bg-slate-800 border-t border-slate-800">
                {[
                  { label: "Open", val: activeCandle.open, color: "text-slate-300" },
                  { label: "High", val: activeCandle.high, color: "text-emerald-400" },
                  { label: "Low", val: activeCandle.low, color: "text-rose-400" },
                  { label: "Close", val: activeCandle.close, color: "text-slate-300" },
                  { label: "Volume (5s)", val: activeCandle.volume, color: "text-cyan-400", isVol: true }
                ].map((stat, i) => (
                  <div key={i} className="bg-slate-950/40 p-4 flex flex-col gap-1 items-center justify-center">
                    <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">{stat.label}</span>
                    <span className={`font-mono text-xs font-semibold ${stat.color}`}>
                      {stat.isVol ? stat.val.toLocaleString() : `$${stat.val.toFixed(2)}`}
                    </span>
                  </div>
                ))}
              </div>
            </section>

            {/* Glowing OpenAI AI Quant Insights Panel */}
            <section className="p-6 bg-gradient-to-r from-indigo-500/10 via-purple-500/10 to-transparent border border-indigo-500/30 rounded-3xl shadow-2xl backdrop-blur relative overflow-hidden">
              <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-500/10 rounded-full blur-2xl pointer-events-none"></div>
              
              <div className="flex gap-4 items-start relative z-10">
                <div className="w-10 h-10 bg-indigo-500/20 border border-indigo-500/40 rounded-xl flex items-center justify-center shadow-lg">
                  <Sparkles size={20} className="text-indigo-400 animate-pulse" />
                </div>
                <div className="flex flex-col gap-1.5 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-bold uppercase tracking-wider text-indigo-300">OpenAI Buying & Selling Insights ({selectedSymbol})</h3>
                    <span className="text-[9px] font-mono bg-indigo-500/20 text-indigo-300 border border-indigo-500/20 px-2 py-0.5 rounded-full uppercase">GPT-4o Realtime Analysis</span>
                  </div>
                  <p className="text-sm text-slate-100 italic leading-relaxed">
                    &ldquo;{latestAIInsight}&rdquo;
                  </p>
                </div>
              </div>
            </section>

          </div>

          {/* Structured Telegram Alerts Column */}
          <section className="bg-slate-900/65 border border-slate-800/80 rounded-3xl shadow-2xl flex flex-col overflow-hidden max-h-[670px] backdrop-blur-3xl lg:col-span-1">
            <div className="p-6 border-b border-slate-800 flex justify-between items-center">
              <div>
                <h2 className="text-md font-semibold text-slate-100 flex items-center gap-2">
                  <Bell size={16} className="text-indigo-400" />
                  Bot Alert Log
                </h2>
                <p className="text-xs text-slate-400">Telegram notification stream</p>
              </div>
              <span className="badge bg-amber-500/10 text-amber-400 border border-amber-500/20 font-mono text-xs px-2.5 py-1 rounded-full">
                {activeAlerts.length} Signals
              </span>
            </div>

            {/* Signal Feed */}
            <div className="flex-1 p-6 overflow-y-auto flex flex-col gap-4 min-h-[350px]">
              {activeAlerts.length === 0 ? (
                <div className="flex flex-col items-center justify-center text-center gap-3 h-full min-h-[280px]">
                  <div className="w-12 h-12 bg-slate-950/80 border border-slate-800 rounded-full flex items-center justify-center text-xl animate-bounce">
                    📡
                  </div>
                  <div className="flex flex-col gap-1">
                    <p className="text-sm font-semibold text-slate-300">Awaiting {selectedSymbol} Signals</p>
                    <p className="text-xs text-slate-500 max-w-[210px] mx-auto leading-relaxed">
                      Crossovers trigger when price breaks across VWAP overlays alongside matching RSI filters.
                    </p>
                  </div>
                </div>
              ) : (
                activeAlerts.map((sig, i) => (
                  <div 
                    key={i} 
                    className={`p-4 rounded-xl border animate-slide-in flex flex-col gap-3 shadow-lg transition-transform duration-300 hover:scale-[1.01] ${
                      sig.action === "BUY" 
                        ? "bg-emerald-500/5 border-emerald-500/15" 
                        : "bg-rose-500/5 border-rose-500/15"
                    }`}
                  >
                    {/* Header */}
                    <div className="flex justify-between items-center">
                      <span className={`text-[10px] font-extrabold tracking-wider px-3 py-0.5 rounded-full flex items-center gap-1 ${
                        sig.action === "BUY" ? "bg-emerald-500 text-slate-950" : "bg-rose-500 text-white"
                      }`}>
                        {sig.action === "BUY" ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                        {sig.action} SIGNAL
                      </span>
                      <span className="text-[10px] font-mono text-slate-400">{new Date(sig.timestamp * 1000).toLocaleTimeString()}</span>
                    </div>

                    {/* Formatted Slack Alert code snippet */}
                    <div className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg">
                      <div className="text-[11px] font-mono text-slate-300 whitespace-pre-wrap select-all leading-relaxed break-all">
                        {sig.message}
                      </div>
                    </div>

                    {/* Indicators Pills Grid */}
                    <div className="grid grid-cols-3 gap-2 text-[10px] font-mono mt-1 text-slate-400">
                      <div className="bg-slate-950/30 p-2 border border-slate-800/50 rounded flex flex-col gap-0.5 items-center">
                        <span className="text-slate-500 text-[8px] uppercase">RSI</span>
                        <span className={`font-semibold ${sig.action === "BUY" ? "text-emerald-400" : "text-rose-400"}`}>
                          {sig.rsi != null ? Number(sig.rsi).toFixed(1) : "N/A"}
                        </span>
                      </div>
                      <div className="bg-slate-950/30 p-2 border border-slate-800/50 rounded flex flex-col gap-0.5 items-center">
                        <span className="text-slate-500 text-[8px] uppercase">VWAP</span>
                        <span className="font-semibold text-slate-300">
                          {sig.vwap != null ? `$${Number(sig.vwap).toFixed(2)}` : "N/A"}
                        </span>
                      </div>
                      <div className="bg-slate-950/30 p-2 border border-slate-800/50 rounded flex flex-col gap-0.5 items-center">
                        <span className="text-slate-500 text-[8px] uppercase">Stop Loss</span>
                        <span className="font-semibold text-amber-500">
                          {sig.stop != null ? `$${Number(sig.stop).toFixed(2)}` : "N/A"}
                        </span>
                      </div>
                    </div>

                    {/* Targets Grid */}
                    <div className="grid grid-cols-2 gap-2 text-[10px] font-mono text-slate-400">
                      <div className="bg-slate-950/30 p-2 border border-slate-800/50 rounded flex justify-between items-center px-3">
                        <span className="text-slate-500 flex items-center gap-1"><Target size={8} /> T1:</span>
                        <span className="font-semibold text-emerald-400">
                          {sig.t1 != null ? `$${Number(sig.t1).toFixed(2)}` : "N/A"}
                        </span>
                      </div>
                      <div className="bg-slate-950/30 p-2 border border-slate-800/50 rounded flex justify-between items-center px-3">
                        <span className="text-slate-500 flex items-center gap-1"><Target size={8} /> T2:</span>
                        <span className="font-semibold text-cyan-400">
                          {sig.t2 != null ? `$${Number(sig.t2).toFixed(2)}` : "N/A"}
                        </span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>

        </div>

        {/* Real-time System Telemetry Logs */}
        <section className="bg-slate-900/65 border border-slate-800/80 rounded-3xl shadow-2xl flex flex-col overflow-hidden backdrop-blur-3xl">
          <div className="p-4 border-b border-slate-800 flex items-center gap-2">
            <Terminal size={14} className="text-cyan-400 animate-pulse" />
            <h2 className="text-xs font-semibold tracking-wider uppercase text-slate-400">System Telemetry Log Feed</h2>
          </div>
          <div className="bg-slate-950/70 p-5 h-40 overflow-y-auto font-mono text-xs flex flex-col gap-1.5">
            {telemetry.map((line, i) => (
              <div key={i} className="flex gap-3 leading-relaxed">
                <span className="text-slate-500 select-none">[{line.time}]</span>
                <span className={
                  line.type === "system" ? "text-cyan-400" :
                  line.type === "tick" ? "text-slate-500" :
                  line.type === "candle" ? "text-violet-400" :
                  line.type === "alert" ? "text-emerald-400 font-semibold" : "text-rose-400"
                }>
                  {line.text}
                </span>
              </div>
            ))}
          </div>
        </section>

      </div>

      {/* Telegram Animated Alerts Toasts */}
      <div className="fixed bottom-6 right-6 flex flex-col gap-3 z-50 pointer-events-none">
        {activeToast && (
          <div className="w-[325px] p-4 bg-slate-950/95 border border-slate-800 rounded-xl shadow-2xl flex flex-col gap-2.5 transition-all duration-300 animate-slide-in pointer-events-auto border-l-4 border-l-sky-500">
            <div className="flex items-center gap-2">
              <span className="text-base">✈️</span>
              <span className="text-xs font-extrabold tracking-wide uppercase text-sky-400">
                Telegram Alerts System
              </span>
            </div>
            <div className="text-xs text-slate-200">
              <b className={activeToast.action === "BUY" ? "text-emerald-400" : "text-rose-400"}>
                {activeToast.action === "BUY" ? "🟢 QUANT BUY TRIGGER" : "🔴 QUANT SELL TRIGGER"}
              </b><br />
              Crossover trigger for {activeToast.symbol} executed successfully at ${activeToast.price.toFixed(2)}.<br />
              <span className="text-[10px] text-slate-400 block mt-1 font-mono">
                Channel: @TradingPlatformAlerts
              </span>
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
