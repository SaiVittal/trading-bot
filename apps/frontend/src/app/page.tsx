"use client";

import { useEffect, useRef, useState } from "react";
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Bell,
  Terminal,
  Cpu,
  Radio,
  Target,
  Sparkles,
  BarChart3,
  Search,
  Plus,
  Trash2,
  Bookmark,
  Eye,
  EyeOff,
  X,
  Zap,
  Flame,
  AlertTriangle
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
  direction: "bullish" | "bearish";
  price: number;
  rsi: number;
  vwap: number;
  stc: string;
  stop: number;
  t1: number;
  t2: number;
  rr: number;
  exit_price: number;
  exit_note: string;
  trade_type: string;
  confidence: number;
  confidence_label: string;
  top_strategy: string;
  top_strategy_name: string;
  strategies_fired: string[];
  strategy_names: string[];
  consensus_bull: number;
  consensus_bear: number;
  conditions_met: string[];
  vol_regime: string;
  vol_rel: number;
  patterns: string[];
  expected_range: [number, number];
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
  // Authentication & Session state
  const [token, setToken] = useState<string | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [usernameInput, setUsernameInput] = useState("");
  const [emailInput, setEmailInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [authError, setAuthError] = useState("");
  const [isAuthLoading, setIsAuthLoading] = useState(false);
  
  // Custom UX/UI additions
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // Simulated landing page live states
  const [mockPrice, setMockPrice] = useState(172.40);
  const [mockDiff, setMockDiff] = useState<"up" | "down" | "flat">("up");
  const [mockHistory, setMockHistory] = useState<number[]>([170.2, 171.0, 170.8, 171.5, 171.2, 172.0, 171.8, 172.5, 172.2, 172.9]);
  const [mockUpvotes, setMockUpvotes] = useState(482);
  const [hasUpvoted, setHasUpvoted] = useState(false);
  const [spawnSparkles, setSpawnSparkles] = useState(false);

  // Shared base URL for all API calls
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  // Stale-data thresholds synced from backend market:status message (ms).
  // Defaults match backend Settings defaults until a status message arrives.
  const staleThresholdRef = useRef<{ marketHours: number; offHours: number }>({
    marketHours: 45_000,
    offHours:    1_800_000,
  });

  // State variables
  const [, setConnected] = useState(false);
  const [wsStatus, setWsStatus] = useState<"CONNECTING" | "CONNECTED" | "RECONNECTING" | "DISCONNECTED" | "STALE_DATA">("CONNECTING");
  const wsStatusRef = useRef<"CONNECTING" | "CONNECTED" | "RECONNECTING" | "DISCONNECTED" | "STALE_DATA">("CONNECTING");
  const [selectedSymbol, setSelectedSymbol] = useState<string>("TSLA");
  const [watchlist, setWatchlist] = useState<string[]>(["TSLA", "NBIS", "COST", "SPX", "APPLOVIN"]);
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
  const [telegramAlertsEnabled, setTelegramAlertsEnabled] = useState<boolean>(true);

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
  const lastTickTimeRef = useRef<number>(Date.now());
  // Track first-seen price per symbol this session for % change calculation
  const sessionOpenPricesRef = useRef<Record<string, number>>({});
  // Track latest VWAP from alert data per symbol
  const latestVwapRef = useRef<Record<string, number>>({});

  // Safely restore token on dynamic page hydrate
  useEffect(() => {
    if (typeof window !== "undefined") {
      const savedToken = localStorage.getItem("auth_token");
      if (savedToken) {
        Promise.resolve().then(() => setToken(savedToken));
      }
      
      // Watchlist is now server-side (DB + Redis) — no localStorage needed
    }
  }, []);

  // Synchronize dynamic refs to avoid stale closures in event loops
  useEffect(() => {
    if (!token) return;
    selectedSymbolRef.current = selectedSymbol;

    // Redraw chart when active symbol shifts
    drawChart();

    // Reset active candle baseline
    const filteredClosed = closedCandles.filter(c => c.symbol === selectedSymbol);
    if (filteredClosed.length > 0) {
      const lastClosed = filteredClosed[filteredClosed.length - 1];
      Promise.resolve().then(() => setCurrentPrice(lastClosed.close));
      currentPriceRef.current = lastClosed.close;
      Promise.resolve().then(() => setActiveCandle({
        symbol: selectedSymbol, open: 0, high: 0, low: 0, close: 0, volume: 0, timestamp: 0
      }));
    } else {
      const livePrice = watchlistPrices[selectedSymbol] || 0;
      Promise.resolve().then(() => setCurrentPrice(livePrice));
      currentPriceRef.current = livePrice;
      Promise.resolve().then(() => setActiveCandle({
        symbol: selectedSymbol, open: 0, high: 0, low: 0, close: 0, volume: 0, timestamp: 0
      }));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, token]);

  useEffect(() => {
    if (token) {
      closedCandlesRef.current = closedCandles;
      drawChart();
    }
  }, [closedCandles, token]);

  useEffect(() => {
    if (token) {
      activeCandleRef.current = activeCandle;
      drawChart();
    }
  }, [activeCandle, token]);

  const updateWsStatus = (status: "CONNECTING" | "CONNECTED" | "RECONNECTING" | "DISCONNECTED" | "STALE_DATA") => {
    wsStatusRef.current = status;
    setWsStatus(status);
  };

  // Safe client telemetry logger
  const logSystem = (text: string, type: LogLine["type"]) => {
    const time = new Date().toLocaleTimeString();
    setTelemetry(prev => {
      const lines = [...prev, { text, type, time }];
      if (lines.length > 30) lines.shift();
      return lines;
    });
  };

  // Mock live ticker & terminal simulation for landing page
  useEffect(() => {
    if (token) return;

    const interval = setInterval(() => {
      setMockPrice(prev => {
        const change = (Math.random() - 0.46) * 0.9;
        const next = parseFloat((prev + change).toFixed(2));
        setMockDiff(change > 0 ? "up" : "down");
        setMockHistory(hist => [...hist.slice(-11), next]);
        return next;
      });
    }, 2000);

    return () => clearInterval(interval);
  }, [token]);

  // Load initial Telegram alert status
  useEffect(() => {
    if (!token) return;
    const fetchTelegramStatus = async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/api/v1/alerts/telegram/status`, {
          headers: { "Authorization": `Bearer ${token}` }
        });
        if (response.ok) {
          const data = await response.json();
          setTelegramAlertsEnabled(data.enabled);
        }
      } catch (e) {
        console.error("Failed to fetch telegram status", e);
      }
    };
    fetchTelegramStatus();
  }, [token]);

  // On login: load user-specific watchlist from DB, then recent signals
  useEffect(() => {
    if (!token) return;

    const fetchWatchlist = async () => {
      try {
        const res = await fetch(`${apiBaseUrl}/api/v1/watchlist`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          const symbols: string[] = (data.items ?? []).map((i: { symbol: string }) => i.symbol);
          if (symbols.length > 0) {
            setWatchlist(symbols);
            // Keep selected symbol valid
            setSelectedSymbol(prev => (symbols.includes(prev) ? prev : symbols[0]));
            logSystem(`[REST] Loaded watchlist: ${symbols.join(", ")}`, "system");
          }
        }
      } catch (e) {
        console.error("Failed to fetch watchlist", e);
      }
    };

    const fetchRecentSignals = async () => {
      try {
        const res = await fetch(`${apiBaseUrl}/api/v1/alerts/signals/recent?limit=15`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data: AlertData[] = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            setSignals(data.sort((a, b) => b.timestamp - a.timestamp));
            const forSelected = data.find(a => a.symbol === selectedSymbol);
            if (forSelected?.ai_insight) setLatestAIInsight(forSelected.ai_insight);
            logSystem(`[REST] Loaded ${data.length} recent signal(s) from server`, "system");
          }
        }
      } catch (e) {
        console.error("Failed to fetch recent signals", e);
      }
    };

    // Load watchlist first so the correct symbols are ready before WS connects
    fetchWatchlist().then(() => fetchRecentSignals());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const handleTelegramToggle = async () => {
    if (!token) return;
    const nextState = !telegramAlertsEnabled;
    setTelegramAlertsEnabled(nextState);

    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/alerts/telegram/toggle`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({ enabled: nextState })
      });
      if (response.ok) {
        logSystem(`Telegram alerts successfully ${nextState ? "enabled" : "disabled"}.`, "system");
      } else {
        // Rollback state if api fails
        setTelegramAlertsEnabled(!nextState);
        logSystem("Failed to toggle Telegram alerts.", "error");
      }
    } catch {
      setTelegramAlertsEnabled(!nextState);
      logSystem("Failed to toggle Telegram alerts.", "error");
    }
  };

  const handleAuthSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError("");
    setIsAuthLoading(true);

    try {
      if (authMode === "login") {
        const formData = new URLSearchParams();
        formData.append("username", usernameInput);
        formData.append("password", passwordInput);

        const response = await fetch(`${apiBaseUrl}/api/v1/auth/login`, {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: formData,
        });

        let data;
        const contentType = response.headers.get("content-type");
        if (contentType && contentType.includes("application/json")) {
          data = await response.json();
        } else {
          const errorText = await response.text();
          console.error("Non-JSON Response received:", errorText);
          throw new Error("The API server returned an invalid response (likely offline or deploying). Please try again shortly.");
        }

        if (!response.ok) {
          throw new Error(data.detail || "Incorrect username or password.");
        }

        localStorage.setItem("auth_token", data.access_token);
        setToken(data.access_token);
        logSystem(`User logged in as ${usernameInput}`, "system");
        
        // Reset inputs
        setUsernameInput("");
        setPasswordInput("");
        setShowAuthModal(false);
      } else {
        const response = await fetch(`${apiBaseUrl}/api/v1/auth/register`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            username: usernameInput,
            email: emailInput,
            password: passwordInput,
          }),
        });

        let data;
        const contentType = response.headers.get("content-type");
        if (contentType && contentType.includes("application/json")) {
          data = await response.json();
        } else {
          const errorText = await response.text();
          console.error("Non-JSON Response received:", errorText);
          throw new Error("The API server returned an invalid response (likely offline or deploying). Please try again shortly.");
        }

        if (!response.ok) {
          throw new Error(data.detail || "Registration failed. Try another username/email.");
        }

        // Auto-login on success
        const formData = new URLSearchParams();
        formData.append("username", usernameInput);
        formData.append("password", passwordInput);

        const loginResponse = await fetch(`${apiBaseUrl}/api/v1/auth/login`, {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: formData,
        });

        let loginData;
        const loginContentType = loginResponse.headers.get("content-type");
        if (loginContentType && loginContentType.includes("application/json")) {
          loginData = await loginResponse.json();
        } else {
          throw new Error("Account created! But automated login received an invalid server response.");
        }

        if (!loginResponse.ok) {
          throw new Error("Account created! But automated login failed.");
        }

        localStorage.setItem("auth_token", loginData.access_token);
        setToken(loginData.access_token);
        logSystem(`User registered and authenticated: ${usernameInput}`, "system");
        
        // Reset inputs
        setUsernameInput("");
        setEmailInput("");
        setPasswordInput("");
        setShowAuthModal(false);
      }
    } catch (err: unknown) {
      const error = err as Error;
      setAuthError(error.message || "Authentication pipeline failure.");
    } finally {
      setIsAuthLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("auth_token");
    setToken(null);
    setConnected(false);
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    // Clear all session-scoped state and refs — watchlist reloads from DB on next login
    setWatchlist([]);
    setSelectedSymbol("TSLA");
    setWatchlistPrices({});
    setSignals([]);
    setClosedCandles([]);
    setCurrentPrice(0);
    setActiveCandle({ symbol: "TSLA", open: 0, high: 0, low: 0, close: 0, volume: 0, timestamp: 0 });
    setLatestAIInsight("Awaiting real-time trade signals. OpenAI Quant Engine is fully armed and listening to active indicators...");
    setActiveToast(null);
    sessionOpenPricesRef.current = {};
    latestVwapRef.current = {};
    closedCandlesRef.current = [];
    lastTickTimeRef.current = Date.now();
    setTelemetry([{ text: "Session terminated. Please authenticate to reconnect.", type: "system", time: new Date().toLocaleTimeString() }]);
  };

  // Hydration safety line
  useEffect(() => {
    Promise.resolve().then(() => {
      setTelemetry([
        { text: "Initializing dynamic multi-symbol trading core...", type: "system", time: new Date().toLocaleTimeString() }
      ]);
    });
  }, []);

  // Animated popup dispatchers (auto-dismiss after 5 seconds)
  const triggerToasts = (signal: AlertData) => {
    const id = Math.random().toString();
    setActiveToast({
      id,
      platform: "telegram",
      action: signal.action,
      price: signal.price,
      symbol: signal.symbol
    });
    setTimeout(() => setActiveToast(prev => prev?.id === id ? null : prev), 5000);
  };

  // ── Watchlist REST API helpers ────────────────────────────────
  const watchlistApi = async (method: "POST" | "DELETE", symbol: string) => {
    if (!token) return;
    try {
      await fetch(`${apiBaseUrl}/api/v1/watchlist/${encodeURIComponent(symbol)}`, {
        method,
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch (e) {
      logSystem(`Watchlist API error (${method} ${symbol}): ${e}`, "error");
    }
  };

  // Dynamic Ticker Searched Subscription hook
  const handleSearchSubscribe = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const symbol = searchInput.toUpperCase().trim();
    if (!symbol) return;

    // 1. Persist to DB via REST API (backend updates Redis + market feed)
    await watchlistApi("POST", symbol);

    // 2. Optimistic UI update while waiting for watchlist:sync confirmation
    if (!watchlist.includes(symbol)) {
      setWatchlist(prev => [...prev, symbol]);
    }

    // 3. Switch main view focus to searched asset
    setSelectedSymbol(symbol);
    setSearchInput("");

    // 4. Also signal via WebSocket so backend sends candle/alert history for new symbol
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "search", symbol }));
      logSystem(`Subscribed to real-time stream for: ${symbol}`, "system");
    }
  };

  const handleRemoveSymbol = async (sym: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (watchlist.length === 1) return; // Keep at least one symbol

    // 1. Optimistic UI update
    const updated = watchlist.filter(s => s !== sym);
    setWatchlist(updated);

    if (selectedSymbol === sym) {
      setSelectedSymbol(updated[0] || "TSLA");
    }

    // 2. Persist removal to DB via REST API
    await watchlistApi("DELETE", sym);

    // 3. Signal WebSocket for real-time unsubscription
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "remove", symbol: sym }));
    }
    logSystem(`Removed ${sym} from watchlist.`, "system");
  };

  // WebSockets client implementation
  useEffect(() => {
    if (!token) return;

    let ws: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;
    let pingInterval: NodeJS.Timeout;
    let reconnectAttempts = 0;
    // Prevents onclose from scheduling a reconnect after intentional cleanup
    let destroyed = false;
    // Prevents onerror + onclose from both scheduling a reconnect
    let reconnectPending = false;

    // Exponential backoff with jitter: 1s, 2s, 4s, 8s … capped at 30s
    // Jitter spreads clients so they don't all hammer the server at once after a restart
    const backoffMs = (attempt: number) => {
      const base = Math.min(1000 * Math.pow(2, attempt), 30000);
      const jitter = Math.random() * 1000;
      return Math.floor(base + jitter);
    };

    const scheduleReconnect = () => {
      if (destroyed || reconnectPending) return;
      reconnectPending = true;
      const delay = backoffMs(reconnectAttempts);
      logSystem(
        `WebSocket closed. Reconnecting in ${(delay / 1000).toFixed(1)}s… (attempt ${reconnectAttempts + 1})`,
        reconnectAttempts === 0 ? "system" : "error",
      );
      reconnectTimeout = setTimeout(() => {
        reconnectPending = false;
        reconnectAttempts++;
        connect();
      }, delay);
    };

    const connect = () => {
      if (destroyed) return;
      updateWsStatus(reconnectAttempts === 0 ? "CONNECTING" : "RECONNECTING");

      const envWsUrl = process.env.NEXT_PUBLIC_WS_URL;
      let wsUrl: string;

      if (envWsUrl) {
        let url = envWsUrl;
        if (url.startsWith("http://")) url = url.replace("http://", "ws://");
        else if (url.startsWith("https://")) url = url.replace("https://", "wss://");
        if (!url.includes("/api/v1/ws")) {
          const cleanUrl = url.endsWith("/") ? url.slice(0, -1) : url;
          url = `${cleanUrl}/api/v1/ws`;
        }
        wsUrl = url;
      } else {
        // Derive WS URL from apiBaseUrl — avoids hardcoded port 8000
        const wsBase = apiBaseUrl
          .replace(/^https:\/\//, "wss://")
          .replace(/^http:\/\//, "ws://")
          .replace(/\/$/, "");
        wsUrl = `${wsBase}/api/v1/ws`;
      }

      ws = new WebSocket(`${wsUrl}?token=${token}`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (destroyed) { ws.close(); return; }
        const wasReconnect = reconnectAttempts > 0;
        reconnectAttempts = 0;
        reconnectPending  = false;
        setConnected(true);
        updateWsStatus("CONNECTED");
        lastTickTimeRef.current = Date.now();
        logSystem(
          wasReconnect
            ? "WebSocket reconnected successfully. Feed restored."
            : "WebSocket connected. Feed bound to Redis.",
          "system",
        );

        // Keepalive ping every 20 s — prevents Render/proxy idle timeout
        clearInterval(pingInterval);
        pingInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 20000);
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          const channel = payload.channel;
          const data = payload.data;

          if (channel === "market:ticks") {
            const tick = data as TickData;
            lastTickTimeRef.current = Date.now();

            // 1. Record first-seen price as session open for % change
            if (!(tick.symbol in sessionOpenPricesRef.current)) {
              sessionOpenPricesRef.current[tick.symbol] = tick.price;
            }

            // 2. Maintain watchlist prices state
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
            logSystem(`[ALERT] ${signal.symbol} ${signal.action} | ${signal.top_strategy_name} | Conf: ${signal.confidence}/100 | R:R 1:${signal.rr}`, "alert");

            // Cache latest VWAP for this symbol for chart/stats display
            if (signal.vwap > 0) {
              latestVwapRef.current[signal.symbol] = signal.vwap;
            }

            setSignals(prev => [signal, ...prev].slice(0, 15));
            if (signal.symbol === selectedSymbolRef.current) {
              setLatestAIInsight(signal.ai_insight);
            }
            triggerToasts(signal);
          } else if (channel === "market:candles:history") {
            // Bulk seed chart with cached candles from backend — fires once on connect per symbol
            const { symbol: sym, candles: historicCandles } = data as { symbol: string; candles: CandleData[] };
            if (Array.isArray(historicCandles) && historicCandles.length > 0) {
              setClosedCandles(prev => {
                // Merge: remove any existing candles for this symbol then prepend historical batch
                const others = prev.filter(c => c.symbol !== sym);
                const merged = [...historicCandles, ...others].slice(0, 400);
                return merged;
              });
              logSystem(`[HISTORY] Loaded ${historicCandles.length} cached candles for ${sym}`, "system");
            }

          } else if (channel === "signals:alerts:history") {
            // Bulk seed signal panel with cached alerts — fires once on connect
            const historicAlerts = data as AlertData[];
            if (Array.isArray(historicAlerts) && historicAlerts.length > 0) {
              setSignals(prev => {
                // Merge: deduplicate by timestamp+symbol, keep newest first
                const existing = new Set(prev.map(a => `${a.symbol}_${a.timestamp}`));
                const incoming = historicAlerts.filter(
                  a => !existing.has(`${a.symbol}_${a.timestamp}`)
                );
                return [...prev, ...incoming]
                  .sort((a, b) => b.timestamp - a.timestamp)
                  .slice(0, 15);
              });
              // Update AI insight if the selected symbol has a cached alert
              const forSelected = historicAlerts.find(a => a.symbol === selectedSymbolRef.current);
              if (forSelected?.ai_insight) setLatestAIInsight(forSelected.ai_insight);
              logSystem(`[HISTORY] Loaded ${historicAlerts.length} cached signal(s) from backend`, "system");
            }

          } else if (channel === "watchlist:sync") {
            const symbols = data as string[];
            // Authoritative sync from DB — replace optimistic UI state
            if (Array.isArray(symbols) && symbols.length > 0) {
              setWatchlist(symbols);
              setSelectedSymbol(prev => (symbols.includes(prev) ? prev : symbols[0]));
            }
            logSystem(`Watchlist synced from server: ${symbols.join(", ")}`, "system");
          } else if (channel === "market:status") {
            const statusData = data as {
              status: string; feed: string; error?: string;
              stale_threshold_market_hours_ms?: number;
              stale_threshold_off_hours_ms?:    number;
            };
            // Sync stale-detection thresholds from backend config
            if (statusData.stale_threshold_market_hours_ms) {
              staleThresholdRef.current.marketHours = statusData.stale_threshold_market_hours_ms;
            }
            if (statusData.stale_threshold_off_hours_ms) {
              staleThresholdRef.current.offHours = statusData.stale_threshold_off_hours_ms;
            }
            logSystem(`[FEED STATUS] Source: ${statusData.feed} | Status: ${statusData.status.toUpperCase()} ${statusData.error ? '| Info: ' + statusData.error : ''}`, "system");
            
            if (statusData.status === "connecting") {
              updateWsStatus("CONNECTING");
            } else if (statusData.status === "reconnecting") {
              updateWsStatus("RECONNECTING");
            } else if (statusData.status === "disconnected") {
              updateWsStatus("DISCONNECTED");
            } else if (statusData.status === "connected") {
              updateWsStatus("CONNECTED");
            } else if (statusData.status === "stale") {
              updateWsStatus("STALE_DATA");
            }
          }
        } catch (e) {
          logSystem(`[ERROR] Processing WebSockets packet: ${(e as Error).message}`, "error");
        }
      };

      ws.onerror = (ev) => {
        // onerror is always followed by onclose — log here, reconnect in onclose
        logSystem(`WebSocket error (attempt ${reconnectAttempts + 1}): connection could not be established.`, "error");
        void ev;
      };

      ws.onclose = (ev) => {
        clearInterval(pingInterval);
        setConnected(false);
        // Code 1000 = normal closure (server restarted cleanly or logout)
        // Code 1008 = auth failure — do NOT reconnect, token is invalid
        if (destroyed) return;
        if (ev.code === 1008) {
          logSystem("WebSocket closed: authentication failed. Please log in again.", "error");
          updateWsStatus("DISCONNECTED");
          return;
        }
        updateWsStatus("RECONNECTING");
        scheduleReconnect();
      };
    };

    connect();

    // Client-side watchdog to detect stale ticks.
    // Threshold matches backend: 45s during market hours (9:30–16:00 ET), 5 min off-hours.
    // Without this alignment, the frontend shows false STALE_DATA on nights/weekends.
    const isMarketHours = () => {
      const now = new Date();
      const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
      const day = et.getDay(); // 0=Sun, 6=Sat
      if (day === 0 || day === 6) return false;
      const h = et.getHours(), m = et.getMinutes();
      const mins = h * 60 + m;
      return mins >= 9 * 60 + 30 && mins < 16 * 60;
    };
    const staleCheckInterval = setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        const elapsed = Date.now() - lastTickTimeRef.current;
        const threshold = isMarketHours()
          ? staleThresholdRef.current.marketHours
          : staleThresholdRef.current.offHours;
        if (elapsed > threshold) {
          updateWsStatus("STALE_DATA");
        } else if (wsStatusRef.current === "STALE_DATA" && elapsed <= threshold) {
          updateWsStatus("CONNECTED");
        }
      }
    }, 5000);

    return () => {
      destroyed = true;                // stops onclose from scheduling another reconnect
      clearTimeout(reconnectTimeout);
      clearInterval(pingInterval);
      clearInterval(staleCheckInterval);
      if (ws) ws.close(1000, "cleanup");
    };
  // wsStatus intentionally excluded: adding it to deps would restart the WS connection on every status change
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // HTML5 Canvas chart renderer — candlestick + volume sub-chart + EMA-9 + VWAP line
  function drawChart() {
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

    const sym = selectedSymbolRef.current;
    const allCandles = closedCandlesRef.current.filter(c => c.symbol === sym);
    const active = activeCandleRef.current;
    if (active && active.symbol === sym && active.open > 0) allCandles.push(active);

    if (allCandles.length === 0) {
      ctx.fillStyle = "rgba(148, 163, 184, 0.45)";
      ctx.font = "500 13px system-ui";
      ctx.textAlign = "center";
      ctx.fillText(`Aggregating real-time ${sym} price data. Waiting for tick feed...`, width / 2, height / 2);
      return;
    }

    // Show last 50 candles for meaningful history
    const MAX_BARS = 50;
    const candles = allCandles.slice(-MAX_BARS);
    const n = candles.length;

    // Layout: top 72% price chart, bottom 28% volume chart
    const padLeft = 4;
    const padRight = width < 480 ? 54 : 72;
    const padTop = 24;
    const volH = Math.floor(height * 0.22);
    const gapH = 8;
    const priceH = height - padTop - volH - gapH;
    const cW = width - padLeft - padRight;

    // Candle slot size
    const slotW = cW / n;
    const barW = Math.max(Math.floor(slotW * 0.65), 2);
    const getX = (i: number) => padLeft + i * slotW + slotW / 2;

    // Price bounds
    let maxP = -Infinity, minP = Infinity;
    candles.forEach(c => { maxP = Math.max(maxP, c.high); minP = Math.min(minP, c.low); });
    const priceRange = maxP - minP || maxP * 0.01 || 1;
    maxP += priceRange * 0.12;
    minP -= priceRange * 0.06;
    const getY = (p: number) => padTop + priceH * (1 - (p - minP) / (maxP - minP));

    // Volume bounds
    const maxVol = Math.max(...candles.map(c => c.volume), 1);
    const volTop = padTop + priceH + gapH;
    const getVolH = (v: number) => Math.max((v / maxVol) * (volH - 4), 1);

    // ── Grid lines ─────────────────────────────────────────────
    ctx.strokeStyle = "rgba(255,255,255,0.025)";
    ctx.lineWidth = 1;
    for (let i = 1; i < 5; i++) {
      const y = padTop + (priceH * i) / 5;
      ctx.beginPath(); ctx.moveTo(padLeft, y); ctx.lineTo(width - padRight, y); ctx.stroke();
      const val = maxP - ((maxP - minP) * i) / 5;
      ctx.fillStyle = "rgba(148,163,184,0.4)";
      ctx.font = "400 9px monospace";
      ctx.textAlign = "left";
      ctx.fillText(val.toFixed(2), width - padRight + 6, y + 3);
    }

    // ── VWAP line (from most recent alert for this symbol) ─────
    const vwapVal = latestVwapRef.current[sym];
    if (vwapVal && vwapVal >= minP && vwapVal <= maxP) {
      const yV = getY(vwapVal);
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "rgba(99,102,241,0.7)";
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(padLeft, yV); ctx.lineTo(width - padRight, yV); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(99,102,241,0.9)";
      ctx.font = "bold 9px monospace";
      ctx.fillText(`VWAP ${vwapVal.toFixed(2)}`, width - padRight + 6, yV - 2);
    }

    // ── Candles + Volume bars ──────────────────────────────────
    candles.forEach((c, i) => {
      const x = getX(i);
      const bullish = c.close >= c.open;
      const color = bullish ? "#10b981" : "#ef4444";
      const wickColor = bullish ? "rgba(16,185,129,0.5)" : "rgba(239,68,68,0.5)";
      const isLast = i === n - 1 && active.open > 0;

      const yO = getY(c.open), yC = getY(c.close);
      const yH = getY(c.high), yL = getY(c.low);

      // Wick
      ctx.strokeStyle = wickColor;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, yH); ctx.lineTo(x, yL); ctx.stroke();

      // Body
      const bodyH = Math.abs(yC - yO) || 1.5;
      const yBody = Math.min(yO, yC);
      if (isLast) {
        ctx.shadowColor = color; ctx.shadowBlur = 8;
      }
      ctx.fillStyle = color;
      ctx.fillRect(x - barW / 2, yBody, barW, bodyH);
      ctx.shadowBlur = 0;

      // Volume bar
      const vh = getVolH(c.volume);
      ctx.fillStyle = bullish ? "rgba(16,185,129,0.35)" : "rgba(239,68,68,0.35)";
      ctx.fillRect(x - barW / 2, volTop + (volH - vh), barW, vh);
    });

    // ── EMA-9 (true exponential) ───────────────────────────────
    if (candles.length >= 9) {
      const k = 2 / (9 + 1);
      const ema9: number[] = [];
      let emaVal = candles[0].close;
      for (let i = 0; i < candles.length; i++) {
        emaVal = candles[i].close * k + emaVal * (1 - k);
        if (i >= 8) ema9.push(emaVal);
      }
      ctx.strokeStyle = "#c084fc";
      ctx.lineWidth = 1.5;
      ctx.shadowColor = "#c084fc";
      ctx.shadowBlur = 3;
      ctx.beginPath();
      ema9.forEach((val, i) => {
        const x = getX(i + 8);
        const y = getY(val);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    // ── Price axis labels ──────────────────────────────────────
    // Current price tick on right axis
    const latestClose = candles[n - 1].close;
    const yCur = getY(latestClose);
    if (yCur >= padTop && yCur <= padTop + priceH) {
      ctx.fillStyle = latestClose >= candles[n - 1].open ? "#10b981" : "#ef4444";
      ctx.font = "bold 9px monospace";
      ctx.fillText(`$${latestClose.toFixed(2)}`, width - padRight + 6, yCur + 3);
    }

    // ── Legend ─────────────────────────────────────────────────
    ctx.font = "bold 9px monospace";
    ctx.fillStyle = "#c084fc"; ctx.fillText("● EMA-9", padLeft + 4, padTop - 6);
    if (vwapVal) {
      ctx.fillStyle = "#6366f1"; ctx.fillText("- - VWAP", padLeft + 60, padTop - 6);
    }
    ctx.fillStyle = "rgba(148,163,184,0.4)"; ctx.fillText("VOL", padLeft + 4, volTop + 10);
  }

  // Redraw chart dynamically on mobile orientation or viewport resize
  useEffect(() => {
    if (!token) return;
    const handleResize = () => {
      drawChart();
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, [token]);

  // Product Hunt Upvote click handler
  const handlePHUpvote = () => {
    if (hasUpvoted) {
      setMockUpvotes(prev => prev - 1);
      setHasUpvoted(false);
    } else {
      setMockUpvotes(prev => prev + 1);
      setHasUpvoted(true);
      setSpawnSparkles(true);
      setTimeout(() => setSpawnSparkles(false), 1200);
    }
  };

  // Render unauthenticated Marketing & Product Hunt Showcase Landing Page
  if (!token) {
    // Generate beautiful live-ticking SVG spline chart points for the sandboxed terminal mockup
    const maxMock = Math.max(...mockHistory);
    const minMock = Math.min(...mockHistory);
    const rangeMock = maxMock - minMock || 2;
    const svgWidth = 520;
    const svgHeight = 220;
    const svgPoints = mockHistory.map((val, idx) => {
      const x = (idx / (mockHistory.length - 1)) * svgWidth;
      const y = svgHeight - ((val - minMock) / rangeMock) * (svgHeight - 40) - 20;
      return `${x},${y}`;
    }).join(" ");

    return (
      <div className="relative min-h-screen bg-[#030712] text-slate-100 flex flex-col font-sans overflow-x-hidden">
        
        {/* Futuristic Cyber Glimmer Background spheres */}
        <div className="absolute top-[-10vw] right-[-15vw] w-[50vw] h-[50vw] rounded-full bg-indigo-650/10 blur-[130px] pointer-events-none z-0"></div>
        <div className="absolute bottom-[-15vw] left-[-15vw] w-[50vw] h-[50vw] rounded-full bg-cyan-500/10 blur-[130px] pointer-events-none z-0"></div>
        <div className="absolute top-[35vh] left-[25vw] w-[35vw] h-[35vw] rounded-full bg-purple-650/5 blur-[160px] pointer-events-none z-0"></div>

        {/* --- PREMIUM STYLED NAV BAR --- */}
        <nav className="sticky top-0 z-40 w-full bg-slate-950/60 border-b border-slate-900 backdrop-blur-xl transition-all duration-300">
          <div className="max-w-[1440px] mx-auto px-6 h-18 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-500 to-purple-650 flex items-center justify-center shadow-lg shadow-indigo-500/20">
                <Flame size={20} className="text-white animate-pulse" />
              </div>
              <span className="text-lg font-black tracking-tight text-white flex items-center gap-1.5">
                QUANT<span className="font-light text-indigo-400">INTELLIGENCE</span>
              </span>
            </div>

            {/* Nav Menu */}
            <div className="hidden md:flex items-center gap-8 text-xs uppercase tracking-wider font-bold text-slate-400">
              <a href="#features" className="hover:text-indigo-400 transition-colors">Key Engines</a>
              <a href="#sandbox" className="hover:text-indigo-400 transition-colors">Sandbox Terminal</a>
              <a href="#upvotes" className="hover:text-indigo-400 transition-colors">Community</a>
            </div>

            {/* PH Launch and CTA */}
            <div className="flex items-center gap-4">
              <div className="hidden sm:flex items-center gap-2 bg-[#ff5722]/10 border border-[#ff5722]/20 px-3.5 py-1.5 rounded-full text-xs font-extrabold text-[#ff5722] hover:bg-[#ff5722]/15 transition-all">
                <span className="w-1.5 h-1.5 rounded-full bg-[#ff5722] animate-ping" />
                PRODUCT HUNT #1
              </div>
              <button
                onClick={() => {
                  setAuthError("");
                  setAuthMode("login");
                  setShowAuthModal(true);
                }}
                className="px-5 py-2.5 bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white rounded-xl font-bold text-xs tracking-wide uppercase transition-all hover:scale-[1.03] shadow-lg shadow-indigo-500/20 active:scale-[0.98] cursor-pointer"
              >
                Launch App
              </button>
            </div>
          </div>
        </nav>

        {/* --- HERO SECTION --- */}
        <section className="relative z-10 max-w-[1240px] mx-auto px-6 pt-16 md:pt-24 pb-12 flex flex-col items-center text-center gap-6">
          
          {/* Custom Shiny PH badge */}
          <div className="inline-flex items-center gap-2.5 bg-gradient-to-r from-slate-900 to-slate-950 border border-slate-800/80 px-4 py-2 rounded-full text-xs font-bold text-slate-200 shadow-xl hover:border-indigo-500/30 transition-all select-none">
            <span className="w-2.5 h-2.5 rounded-full bg-[#ff5722] flex items-center justify-center text-[7px] text-white font-extrabold">▲</span>
            Featured on Product Hunt
            <span className="w-px h-3.5 bg-slate-800" />
            <span className="text-[#ff5722] font-black uppercase tracking-wider">#1 Product of the Day</span>
          </div>

          {/* Epic Main Headline */}
          <h1 className="text-4xl md:text-6xl lg:text-7xl font-extrabold tracking-tight text-white max-w-[980px] leading-[1.1] md:leading-[1.05]">
            Supercharge Your Trading with <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-cyan-400 bg-clip-text text-transparent">AI Quant Signals</span>
          </h1>

          {/* Detailed persuasive subtext */}
          <p className="text-sm md:text-lg text-slate-400 max-w-[760px] leading-relaxed font-light">
            A high-performance algorithmic stream scanning multi-symbol market feeds in real-time. Guided by strict technical crossover filters (VWAP, RSI, STC) and optimized with deep OpenAI GPT-4o intelligence. 
          </p>

          {/* Dynamic Action Buttons */}
          <div className="flex flex-wrap justify-center gap-4 mt-4">
            <button
              onClick={() => {
                setAuthError("");
                setAuthMode("register");
                setShowAuthModal(true);
              }}
              className="px-8 py-4 bg-indigo-500 hover:bg-indigo-600 text-white rounded-2xl font-extrabold text-sm tracking-wide uppercase transition-all shadow-xl shadow-indigo-500/20 hover:scale-[1.02] active:scale-[0.98] cursor-pointer flex items-center gap-2"
            >
              <Zap size={16} /> Get Started Free
            </button>
            <a
              href="#sandbox"
              className="px-8 py-4 bg-slate-900/60 hover:bg-slate-900/90 border border-slate-800 hover:border-slate-700 text-slate-200 rounded-2xl font-extrabold text-sm tracking-wide uppercase transition-all hover:scale-[1.02] active:scale-[0.98] flex items-center gap-2"
            >
              Explore Live Sandbox
            </a>
          </div>

          {/* Social Proof metrics */}
          <div className="grid grid-cols-3 gap-6 md:gap-12 mt-12 p-6 border-t border-b border-slate-900 max-w-[680px] w-full font-mono">
            <div className="flex flex-col gap-1 items-center">
              <span className="text-2xl md:text-3xl font-black text-white">&lt; 5ms</span>
              <span className="text-[10px] text-slate-500 uppercase tracking-widest">Signal Latency</span>
            </div>
            <div className="flex flex-col gap-1 items-center">
              <span className="text-2xl md:text-3xl font-black text-indigo-400">98.4%</span>
              <span className="text-[10px] text-slate-500 uppercase tracking-widest">Uptime Index</span>
            </div>
            <div className="flex flex-col gap-1 items-center">
              <span className="text-2xl md:text-3xl font-black text-cyan-400">14k+</span>
              <span className="text-[10px] text-slate-500 uppercase tracking-widest">Alerts Processed</span>
            </div>
          </div>
        </section>

        {/* --- LIVE MOCKUP SHOWCASE SANDBOX --- */}
        <section id="sandbox" className="relative z-10 max-w-[1240px] mx-auto px-6 py-16 flex flex-col gap-10">
          
          <div className="flex flex-col items-center text-center gap-2">
            <h2 className="text-2xl md:text-3xl font-bold tracking-tight text-white">
              The Real-Time Sandboxed Console
            </h2>
            <p className="text-xs text-slate-450 max-w-[520px]">
              Witness a live simulation of active stock volatility, technical charts, and GPT-4o analysis. Zero setup required.
            </p>
          </div>

          {/* High fidelity sandboxed dashboard layout */}
          <div className="bg-slate-900/40 border border-slate-800/80 backdrop-blur-3xl p-4 md:p-6 rounded-3xl shadow-2xl flex flex-col gap-6 relative overflow-hidden">
            
            {/* Ambient inner glow */}
            <div className="absolute top-0 left-0 w-full h-px bg-gradient-to-r from-transparent via-indigo-500/25 to-transparent" />

            {/* Sandbox Console Header */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 pb-4 border-b border-slate-800/80">
              <div className="flex items-center gap-3">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                </span>
                <div className="flex flex-col">
                  <span className="text-xs font-bold text-white uppercase tracking-wider">SANDBOX FEED</span>
                  <span className="text-[10px] text-slate-500 font-mono">Ticking Multi-Symbol Stream Simulator</span>
                </div>
              </div>

              {/* Badges strip */}
              <div className="flex items-center gap-2">
                <span className="bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 px-3 py-1 rounded-full text-[10px] font-mono tracking-wider">
                  📡 SYSTEM ONLINE
                </span>
                <span className="bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 px-3 py-1 rounded-full text-[10px] font-mono tracking-wider">
                  🤖 OPENAI CONNECTED
                </span>
              </div>
            </div>

            {/* Sandbox Dashboard Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

              {/* Ticker Sidebar */}
              <div className="flex flex-col gap-4 bg-slate-950/20 border border-slate-900 p-4 rounded-2xl">
                <span className="text-[10px] uppercase font-bold text-slate-400 tracking-wider">Active Watchlist</span>
                <div className="flex flex-col gap-2.5">
                  {[
                    { ticker: "TSLA", name: "Tesla Inc.", price: mockPrice, change: mockDiff === "up" ? "+1.85%" : "-1.12%" },
                    { ticker: "NVDA", name: "Nvidia Corp.", price: 928.30, change: "+2.40%" },
                    { ticker: "AMD", name: "Advanced Micro Devices", price: 174.15, change: "+0.80%" },
                    { ticker: "SPX", name: "S&P 500 Index", price: 5240.20, change: "-0.22%" }
                  ].map((x, i) => (
                    <div key={i} className={`p-3 rounded-xl border flex justify-between items-center transition-all ${i === 0 ? "bg-indigo-500/5 border-indigo-500/30" : "bg-slate-950/40 border-slate-900"}`}>
                      <div className="flex flex-col gap-0.5">
                        <span className="font-mono text-xs font-bold text-white">{x.ticker}</span>
                        <span className="text-[9px] text-slate-500 font-mono">{x.name}</span>
                      </div>
                      <div className="flex flex-col items-end gap-0.5">
                        <span className={`font-mono text-xs font-bold ${i === 0 ? (mockDiff === "up" ? "text-emerald-400 animate-pulse" : "text-rose-400 animate-pulse") : (x.change.startsWith("+") ? "text-emerald-400" : "text-rose-400")}`}>
                          ${x.price.toFixed(2)}
                        </span>
                        <span className={`text-[9px] font-mono font-bold ${x.change.startsWith("+") ? "text-emerald-500" : "text-rose-500"}`}>
                          {x.change}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Mock Interactive Spline Chart */}
              <div className="flex flex-col gap-4 bg-slate-950/20 border border-slate-900 p-4 rounded-2xl lg:col-span-2">
                <div className="flex justify-between items-center">
                  <span className="text-[10px] uppercase font-bold text-slate-400 tracking-wider">TSLA Technical Trend</span>
                  <span className="text-[10px] font-mono text-indigo-400 flex items-center gap-1.5">
                    <Activity size={10} className="animate-pulse" /> Live simulated spline (2s tick)
                  </span>
                </div>
                
                {/* SVG Live-ticking Spline Chart */}
                <div className="h-[220px] bg-slate-950/50 rounded-xl relative border border-slate-900/50 overflow-hidden flex items-end">
                  {/* Grid Lines */}
                  <div className="absolute inset-0 grid grid-rows-4 pointer-events-none opacity-20">
                    {[1, 2, 3].map(i => <div key={i} className="border-b border-slate-800 w-full" />)}
                  </div>
                  
                  {/* Neon Spline */}
                  <svg className="w-full h-full absolute inset-0 overflow-visible" preserveAspectRatio="none">
                    <defs>
                      <linearGradient id="splineGrad" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" stopColor="#6366f1" stopOpacity="0.4"/>
                        <stop offset="100%" stopColor="#6366f1" stopOpacity="0.0"/>
                      </linearGradient>
                    </defs>
                    {/* Area fill */}
                    <path
                      d={`M 0,220 L ${svgPoints} L 520,220 Z`}
                      fill="url(#splineGrad)"
                      className="transition-all duration-1000 ease-out"
                    />
                    {/* Line path */}
                    <polyline
                      fill="none"
                      stroke="#818cf8"
                      strokeWidth="2.5"
                      points={svgPoints}
                      className="transition-all duration-1000 ease-out"
                    />
                  </svg>

                  {/* Pulsing indicator node */}
                  <div 
                    className="absolute w-3.5 h-3.5 rounded-full bg-indigo-400 border border-white flex items-center justify-center shadow-lg transition-all duration-1000 ease-out z-10"
                    style={{
                      right: '0.5%',
                      bottom: `${((mockPrice - minMock) / rangeMock) * 80 + 10}%`,
                      transform: 'translateY(50%)'
                    }}
                  >
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75" />
                  </div>
                  
                  {/* Ticking pricing floating overlay */}
                  <div className="absolute top-3 left-3 bg-slate-950/80 border border-slate-800 px-3 py-1.5 rounded-lg flex items-center gap-2">
                    <span className="text-[10px] text-slate-500 uppercase font-mono">Current Vol:</span>
                    <span className={`text-xs font-mono font-black ${mockDiff === "up" ? "text-emerald-400" : "text-rose-400"}`}>
                      ${mockPrice.toFixed(2)}
                    </span>
                  </div>
                </div>
              </div>

            </div>

            {/* Sandbox Bottom Banner GPT insights */}
            <div className="p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl flex gap-3.5 items-start">
              <div className="w-8 h-8 rounded-lg bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center">
                <Sparkles size={16} className="text-indigo-400 animate-pulse" />
              </div>
              <div className="flex flex-col gap-0.5 flex-1">
                <span className="text-[9px] uppercase font-bold text-indigo-400 tracking-wider">GPT-4o Realtime Quant Analytics</span>
                <p className="text-xs text-indigo-200 font-light italic leading-relaxed">
                  {`"TSLA ticking upward at $${mockPrice.toFixed(2)}: Technical indicators show volume accumulation curling above standard VWAP baseline. RSI holding steady at 54, suggesting potential bullish continuation."`}
                </p>
              </div>
            </div>

          </div>
        </section>

        {/* --- SECTION: FEATURES / THE SIGNIFICANCE --- */}
        <section id="features" className="relative z-10 max-w-[1240px] mx-auto px-6 py-16 border-t border-slate-900 flex flex-col gap-12">
          
          <div className="flex flex-col items-center text-center gap-2">
            <h2 className="text-2xl md:text-3xl font-bold tracking-tight text-white">
              The Architecture of Alpha
            </h2>
            <p className="text-xs text-slate-450 max-w-[520px]">
              Engineered from the ground up for lightning fast analysis and bulletproof math crossovers.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            
            {/* Feature 1 */}
            <div className="p-6 bg-slate-900/30 border border-slate-900 hover:border-slate-800 rounded-2xl flex flex-col gap-4 backdrop-blur-md transition-all hover:scale-[1.01]">
              <div className="w-10 h-10 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
                <Target size={20} />
              </div>
              <h3 className="text-sm font-extrabold uppercase text-white tracking-wider">Triple Crossover Logic</h3>
              <p className="text-xs text-slate-400 leading-relaxed font-light">
                No guesses. Our pipeline evaluates **VWAP, RSI, and STC (Schaff Trend Cycle)** simultaneously. Crossovers only trigger alerts when mathematical consensus is met.
              </p>
            </div>

            {/* Feature 2 */}
            <div className="p-6 bg-slate-900/30 border border-slate-900 hover:border-slate-800 rounded-2xl flex flex-col gap-4 backdrop-blur-md transition-all hover:scale-[1.01]">
              <div className="w-10 h-10 rounded-xl bg-purple-550/10 border border-purple-500/20 flex items-center justify-center text-purple-400">
                <Cpu size={20} />
              </div>
              <h3 className="text-sm font-extrabold uppercase text-white tracking-wider">OpenAI GPT-4o Insights</h3>
              <p className="text-xs text-slate-400 leading-relaxed font-light">
                Contextual analytics. GPT-4o intercepts mathematical crossovers and writes human-readable, qualitative summaries, charting trends and key levels automatically.
              </p>
            </div>

            {/* Feature 3 */}
            <div className="p-6 bg-slate-900/30 border border-slate-900 hover:border-slate-800 rounded-2xl flex flex-col gap-4 backdrop-blur-md transition-all hover:scale-[1.01]">
              <div className="w-10 h-10 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400">
                <Bell size={20} />
              </div>
              <h3 className="text-sm font-extrabold uppercase text-white tracking-wider">Telegram Alert Dispatcher</h3>
              <p className="text-xs text-slate-400 leading-relaxed font-light">
                Instant delivery. Alerts dispatch immediately to a dedicated, high-performance Telegram channel with formatted entry levels, stop loss thresholds, and profit targets.
              </p>
            </div>

          </div>
        </section>

        {/* --- PRODUCT HUNT COMMUNITY CORNER --- */}
        <section id="upvotes" className="relative z-10 max-w-[1040px] mx-auto px-6 py-16 border-t border-slate-900 flex flex-col items-center gap-10">
          
          <div className="flex flex-col items-center text-center gap-2">
            <h2 className="text-2xl md:text-3xl font-extrabold text-white tracking-tight">
              Loved by the Quant Community
            </h2>
            <p className="text-xs text-slate-450 max-w-[500px]">
              Launched live on Product Hunt. Join hundreds of quantitative analysts monitoring market feeds with zero lag.
            </p>
          </div>

          <div className="flex flex-col lg:flex-row gap-8 items-center justify-between w-full p-6 md:p-8 bg-slate-900/20 border border-slate-800/80 rounded-3xl backdrop-blur-2xl">
            
            {/* Interactive Upvotes section */}
            <div className="flex flex-col gap-4 items-center text-center lg:items-start lg:text-left">
              <span className="text-xs uppercase font-extrabold font-mono tracking-widest text-[#ff5722] flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-[#ff5722] animate-ping" /> Community Backed
              </span>
              <h3 className="text-xl font-bold text-white max-w-[380px]">
                Support our daily pipeline launch with a single upvote!
              </h3>
              
              {/* Product Hunt Upvote Button */}
              <div className="relative mt-2">
                <button
                  onClick={handlePHUpvote}
                  className={`px-8 py-4 rounded-2xl text-sm font-black tracking-wide uppercase transition-all shadow-xl hover:scale-[1.04] active:scale-[0.97] cursor-pointer flex items-center gap-3.5 select-none ${hasUpvoted ? "bg-[#ff5722] text-white shadow-[#ff5722]/15" : "bg-slate-900 hover:bg-slate-950 border border-slate-800 text-[#ff5722]"}`}
                >
                  <span className="text-lg">▲</span> UPVOTE
                  <span className={`w-px h-4 ${hasUpvoted ? "bg-white/30" : "bg-slate-800"}`} />
                  <span className={hasUpvoted ? "text-white" : "text-slate-200"}>{mockUpvotes}</span>
                </button>

                {/* Pop celebration sparkles */}
                {spawnSparkles && (
                  <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-2xl bg-[#ff5722] opacity-75"></span>
                    <span className="absolute text-sm text-[#ff5722] -top-8 animate-bounce font-bold">✨ Upvoted! ✨</span>
                  </div>
                )}
              </div>
            </div>

            {/* Testimonials column */}
            <div className="flex flex-col gap-4 max-w-[480px]">
              
              <div className="p-4 bg-slate-950/60 border border-slate-900 rounded-2xl flex flex-col gap-2 relative">
                <div className="flex items-center justify-between text-[10px] font-mono text-slate-500">
                  <span className="font-extrabold text-indigo-400">@quant_pro_dev</span>
                  <span>Product Hunt Supporter</span>
                </div>
                <p className="text-xs text-slate-350 italic font-light leading-relaxed">
                  {"\"This tool is an absolute masterpiece for live monitoring! The real-time VWAP and RSI crossovers are insanely fast and the Telegram integration is rock solid.\""}
                </p>
              </div>

              <div className="p-4 bg-slate-950/60 border border-slate-900 rounded-2xl flex flex-col gap-2">
                <div className="flex items-center justify-between text-[10px] font-mono text-slate-500">
                  <span className="font-extrabold text-cyan-400">@algo_trader</span>
                  <span>Beta Tester</span>
                </div>
                <p className="text-xs text-slate-350 italic font-light leading-relaxed">
                  {"\"Absolutely love the glassmorphism aesthetic. It feels premium and high-end, far removed from standard developer-coded tools. Exceptional UI/UX!\""}
                </p>
              </div>

            </div>
          </div>
        </section>

        {/* --- COMPACT SLEEK FOOTER --- */}
        <footer className="mt-auto relative z-10 w-full bg-slate-950 border-t border-slate-900 py-6">
          <div className="max-w-[1440px] mx-auto px-6 flex flex-col sm:flex-row justify-between items-center gap-4 text-[10px] font-mono text-slate-550 uppercase tracking-widest">
            <span>&copy; 2026 QUANT INTELLIGENCE. ALL RIGHTS RESERVED.</span>
            <div className="flex gap-4">
              <a href="#features" className="hover:text-indigo-400">Privacy</a>
              <span>·</span>
              <a href="#sandbox" className="hover:text-indigo-400">Terms</a>
            </div>
          </div>
        </footer>

        {/* --- PREMIUM GLASSMORPHISM AUTH MODAL OVERLAY --- */}
        {showAuthModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/75 backdrop-blur-2xl transition-all duration-300 animate-fade-in">
            
            {/* Modal Card */}
            <div className="w-full max-w-md bg-slate-900/70 border border-slate-800/80 backdrop-blur-3xl p-8 rounded-3xl shadow-2xl relative z-10 flex flex-col gap-6 animate-slide-in">
              
              {/* Close Button */}
              <button
                onClick={() => setShowAuthModal(false)}
                className="absolute top-4 right-4 p-2 rounded-xl bg-slate-950/30 hover:bg-rose-500/10 border border-slate-850/60 hover:border-rose-500/20 text-slate-400 hover:text-rose-400 transition-all cursor-pointer"
              >
                <X size={14} />
              </button>

              <div className="text-center flex flex-col gap-2">
                <h1 className="text-2xl font-black tracking-tight text-white flex items-center justify-center gap-1.5">
                  QUANT<span className="font-extralight text-indigo-400">CONSOLE</span>
                </h1>
                <p className="text-xs text-slate-450">
                  {authMode === "login"
                    ? "Authenticate session to connect live quant telemetry streams"
                    : "Register your secure workspace account to start paper trading"}
                </p>
              </div>

              <form onSubmit={handleAuthSubmit} className="flex flex-col gap-4">
                
                {/* Robust Validation Error Display */}
                {authError && (
                  <div className="p-3.5 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-xs font-mono text-center leading-relaxed">
                    ⚠️ {authError}
                  </div>
                )}

                <div className="flex flex-col gap-1.5">
                  <label className="text-[10px] uppercase font-bold text-slate-400 tracking-wider">Username</label>
                  <input
                    type="text"
                    required
                    value={usernameInput}
                    onChange={e => setUsernameInput(e.target.value)}
                    placeholder="quant_trader"
                    className="w-full px-4 py-3 rounded-xl border border-slate-800/80 bg-slate-950/80 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 transition-colors"
                  />
                </div>

                {authMode === "register" && (
                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] uppercase font-bold text-slate-400 tracking-wider">Email Address</label>
                    <input
                      type="email"
                      required
                      value={emailInput}
                      onChange={e => setEmailInput(e.target.value)}
                      placeholder="trader@quant.bot"
                      className="w-full px-4 py-3 rounded-xl border border-slate-800/80 bg-slate-950/80 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 transition-colors"
                    />
                  </div>
                )}

                {/* Password field with Eye Toggle hide/show */}
                <div className="flex flex-col gap-1.5 relative">
                  <label className="text-[10px] uppercase font-bold text-slate-400 tracking-wider">Password</label>
                  <input
                    type={showPassword ? "text" : "password"}
                    required
                    value={passwordInput}
                    onChange={e => setPasswordInput(e.target.value)}
                    placeholder="••••••••••••"
                    className="w-full px-4 py-3 rounded-xl border border-slate-800/80 bg-slate-950/80 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 transition-colors pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3.5 bottom-3.5 text-slate-500 hover:text-slate-350 transition-colors cursor-pointer"
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>

                <button
                  type="submit"
                  disabled={isAuthLoading}
                  className="mt-2 w-full py-3.5 bg-indigo-500 hover:bg-indigo-600 disabled:bg-indigo-500/50 text-white rounded-xl font-bold text-xs uppercase tracking-wide transition-all shadow-lg shadow-indigo-500/10 flex items-center justify-center gap-2 cursor-pointer"
                >
                  {isAuthLoading ? (
                    <>
                      <span className="w-4 h-4 border-2 border-white/35 border-t-white rounded-full animate-spin" />
                      Authenticating Pipeline...
                    </>
                  ) : authMode === "login" ? (
                    "Authorize Session"
                  ) : (
                    "Create Quant Account"
                  )}
                </button>
              </form>

              <div className="text-center text-xs text-slate-450 border-t border-slate-850 pt-4">
                {authMode === "login" ? (
                  <>
                    First time logging in?{" "}
                    <button
                      type="button"
                      onClick={() => {
                        setAuthMode("register");
                        setAuthError("");
                      }}
                      className="text-indigo-400 hover:underline font-bold cursor-pointer"
                    >
                      Create secure account
                    </button>
                  </>
                ) : (
                  <>
                    Already have an active account?{" "}
                    <button
                      type="button"
                      onClick={() => {
                        setAuthMode("login");
                        setAuthError("");
                      }}
                      className="text-indigo-400 hover:underline font-bold cursor-pointer"
                    >
                      Sign in here
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        )}

      </div>
    );
  }

  // Filter alerts specifically for selected active symbol
  const activeAlerts = signals.filter(s => s.symbol === selectedSymbol);

  // Render Premium Dashboard for authenticated users
  return (
    <div className="flex-1 relative overflow-hidden min-h-screen pb-12 bg-[#020617]">

      {/* Futuristic cyber spotlights */}
      <div className="absolute top-[-12vw] right-[-8vw] w-[50vw] h-[50vw] rounded-full bg-indigo-500 opacity-[0.14] blur-[150px] pointer-events-none"></div>
      <div className="absolute bottom-[-15vw] left-[-10vw] w-[50vw] h-[50vw] rounded-full bg-cyan-400 opacity-[0.12] blur-[150px] pointer-events-none"></div>

      {/* Main Core Container */}
      <div className="max-w-[1640px] mx-auto p-4 md:p-8 flex flex-col gap-6 relative z-10">

        {/* State-of-the-Art Header Panel */}
        <header className="flex flex-col md:flex-row justify-between md:items-center gap-4 p-6 bg-slate-900/65 border border-slate-800/80 backdrop-blur-3xl rounded-2xl shadow-2xl">
          <div className="flex items-center gap-4">
            <div className="relative">
              <span className={`flex h-4 w-4 rounded-full ${
                  wsStatus === "CONNECTED"
                    ? "bg-emerald-500"
                    : wsStatus === "STALE_DATA"
                    ? "bg-amber-500"
                    : wsStatus === "CONNECTING" || wsStatus === "RECONNECTING"
                    ? "bg-blue-500"
                    : "bg-rose-500"
                }`} />
              {(wsStatus === "CONNECTED" || wsStatus === "STALE_DATA" || wsStatus === "CONNECTING" || wsStatus === "RECONNECTING") && (
                <span className={`animate-ping absolute inline-flex h-4 w-4 rounded-full opacity-75 top-0 ${
                    wsStatus === "CONNECTED"
                      ? "bg-emerald-400"
                      : wsStatus === "STALE_DATA"
                      ? "bg-amber-400"
                      : "bg-blue-400"
                  }`} />
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
                wsStatus === "CONNECTED"
                  ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                  : wsStatus === "STALE_DATA"
                  ? "bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse"
                  : wsStatus === "CONNECTING" || wsStatus === "RECONNECTING"
                  ? "bg-blue-500/10 text-blue-400 border border-blue-500/20 animate-pulse"
                  : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
              }`}>
              <Radio size={12} className={wsStatus === "CONNECTED" || wsStatus === "STALE_DATA" ? "animate-pulse" : ""} />
              {wsStatus === "CONNECTED" && "LIVE MULTI-FEED CONNECTED"}
              {wsStatus === "CONNECTING" && "ESTABLISHING HANDSHAKE..."}
              {wsStatus === "RECONNECTING" && "RECONNECTING FEED..."}
              {wsStatus === "DISCONNECTED" && "BROADCASTER DISCONNECTED"}
              {wsStatus === "STALE_DATA" && "FEED ACTIVE (STALE DATA)"}
            </span>
            <span className="badge bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 px-3.5 py-1.5 rounded-full flex items-center gap-1.5 font-mono text-xs">
              <Cpu size={12} />
              OpenAI GPT-4o Insights Active
            </span>
            <button
              onClick={handleLogout}
              className="badge bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/25 px-3.5 py-1.5 rounded-full flex items-center gap-1.5 font-mono text-xs cursor-pointer transition-colors"
            >
              Sign Out
            </button>
          </div>
        </header>

        {/* Real-time Status Alert Banner */}
        {wsStatus !== "CONNECTED" && (
          <div className="bg-amber-500/10 border border-amber-500/20 text-amber-300 px-5 py-4 rounded-2xl flex items-center justify-between backdrop-blur-3xl animate-pulse shadow-xl">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-amber-500/15 flex items-center justify-center border border-amber-500/20">
                <AlertTriangle size={18} className="text-amber-400 animate-bounce" />
              </div>
              <div>
                <span className="font-bold text-xs uppercase tracking-wider text-amber-300">Realtime Market Feed Status Alert</span>
                <p className="text-[11px] text-slate-350 mt-1 leading-normal font-light">
                  {wsStatus === "CONNECTING" && "Establishing connection to high-performance real-time servers..."}
                  {wsStatus === "RECONNECTING" && "WebSocket dropped. Attempting backoff reconnection. If deploying, this may take 30-60 seconds."}
                  {wsStatus === "DISCONNECTED" && "Disconnected from real-time price broker. Check your network or contact platform administrator."}
                  {wsStatus === "STALE_DATA" && "Market feed is online, but price quotes are currently stale/delayed. Waiting for incoming trade ticks..."}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-mono font-bold bg-amber-500/25 border border-amber-500/35 px-3 py-1 rounded-full text-amber-300">
                {wsStatus}
              </span>
            </div>
          </div>
        )}

        {/* Real-time Statistics Strip */}
        <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl backdrop-blur flex justify-between items-center">
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Active Asset Price ({selectedSymbol})</span>
              <span className={`font-mono text-2xl font-bold tracking-tight transition-colors duration-300 ${priceDiff === "up" ? "text-emerald-400" : priceDiff === "down" ? "text-rose-400" : "text-white"
                }`}>
                {currentPrice > 0
                  ? `$${currentPrice.toLocaleString("en-US", { minimumFractionDigits: 2 })}`
                  : (watchlistPrices[selectedSymbol] !== undefined
                    ? `$${watchlistPrices[selectedSymbol].toLocaleString("en-US", { minimumFractionDigits: 2 })}`
                    : "Loading...")}
              </span>
            </div>
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center font-bold ${priceDiff === "up" ? "bg-emerald-500/10 text-emerald-400" : priceDiff === "down" ? "bg-rose-500/10 text-rose-400" : "bg-slate-800 text-slate-500"
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
            <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Session VWAP ({selectedSymbol})</span>
            <span className="text-lg font-mono font-semibold text-violet-400 mt-1 flex items-center gap-2">
              <BarChart3 size={14} className="text-violet-400" />
              {activeAlerts.length > 0 && activeAlerts[0].vwap > 0
                ? `$${Number(activeAlerts[0].vwap).toFixed(2)}`
                : <span className="text-slate-500 text-sm">Awaiting alert</span>}
            </span>
          </div>

          <div className="p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-xl backdrop-blur flex flex-col justify-center">
            <span className="text-[10px] uppercase font-bold text-indigo-400 tracking-wider flex items-center gap-1">
              <Sparkles size={10} className="animate-pulse" /> Last Signal ({selectedSymbol})
            </span>
            <span className="text-xs font-semibold text-indigo-200 mt-1 line-clamp-1">
              {activeAlerts.length > 0
                ? `${activeAlerts[0].action} · ${activeAlerts[0].top_strategy_name || activeAlerts[0].action} · Conf: ${activeAlerts[0].confidence}/100`
                : "Awaiting strategy crossover"}
            </span>
          </div>
        </section>

        {/* Dashboard Grid Layout */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">

          {/* Watchlist Sidebar Column */}
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
            <div className="flex flex-col gap-2.5 overflow-y-auto max-h-[220px] lg:max-h-[460px] pr-1">
              {watchlist.map(sym => {
                const active = sym === selectedSymbol;
                const price = watchlistPrices[sym];
                const openPrice = sessionOpenPricesRef.current[sym];
                const pctChange = price !== undefined && openPrice && openPrice > 0
                  ? ((price - openPrice) / openPrice) * 100
                  : null;

                return (
                  <div
                    key={sym}
                    onClick={() => setSelectedSymbol(sym)}
                    className={`p-3.5 rounded-xl border transition-all duration-300 cursor-pointer flex justify-between items-center ${active
                        ? "bg-gradient-to-r from-indigo-500/15 via-purple-500/10 to-transparent border-indigo-500/50 shadow-indigo-500/5"
                        : "bg-slate-950/30 border-slate-800/80 hover:bg-slate-950/50"
                      }`}
                  >
                    <div className="flex flex-col gap-0.5">
                      <span className={`font-mono text-sm font-extrabold tracking-wider ${active ? "text-indigo-300" : "text-slate-300"}`}>
                        {sym}
                      </span>
                      {pctChange !== null ? (
                        <span className={`text-[9px] font-mono font-bold ${pctChange >= 0 ? "text-emerald-500" : "text-rose-500"}`}>
                          {pctChange >= 0 ? "+" : ""}{pctChange.toFixed(2)}% session
                        </span>
                      ) : (
                        <span className="text-[9px] text-slate-500 uppercase font-mono">Stock Feed</span>
                      )}
                    </div>

                    <div className="flex items-center gap-3 font-mono">
                      <span className={`text-xs font-semibold font-mono ${active ? "text-cyan-400" : "text-slate-300"}`}>
                        {price !== undefined ? `$${price.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "—"}
                      </span>

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
                    5s candles · EMA-9 · VWAP · Volume
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
              <div className="flex items-center gap-2">
                <button
                  onClick={handleTelegramToggle}
                  className={`px-2.5 py-1 rounded-full text-[9px] font-extrabold tracking-wider border cursor-pointer transition-all duration-300 shadow-md ${
                    telegramAlertsEnabled
                      ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/20 shadow-emerald-500/5"
                      : "bg-rose-500/10 text-rose-400 border-rose-500/20 hover:bg-rose-500/20 shadow-rose-500/5"
                  }`}
                >
                  {telegramAlertsEnabled ? "🟢 TELEGRAM ON" : "🔴 TELEGRAM OFF"}
                </button>
                <span className="badge bg-amber-500/10 text-amber-400 border border-amber-500/20 font-mono text-xs px-2.5 py-1 rounded-full">
                  {activeAlerts.length} Signals
                </span>
              </div>
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
                    className={`p-4 rounded-xl border flex flex-col gap-3 shadow-lg transition-transform duration-300 hover:scale-[1.01] ${sig.action === "BUY"
                        ? "bg-emerald-500/5 border-emerald-500/20"
                        : "bg-rose-500/5 border-rose-500/20"
                      }`}
                  >
                    {/* Header row */}
                    <div className="flex justify-between items-center">
                      <div className="flex items-center gap-2">
                        <span className={`text-[10px] font-extrabold tracking-wider px-2.5 py-0.5 rounded-full flex items-center gap-1 ${sig.action === "BUY" ? "bg-emerald-500 text-slate-950" : "bg-rose-500 text-white"}`}>
                          {sig.action === "BUY" ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                          {sig.action}
                        </span>
                        <span className="text-[9px] font-mono text-slate-400 bg-slate-900 border border-slate-800 px-2 py-0.5 rounded-full">
                          {sig.trade_type || "Intraday"}
                        </span>
                      </div>
                      <span className="text-[10px] font-mono text-slate-500">{new Date(sig.timestamp * 1000).toLocaleTimeString()}</span>
                    </div>

                    {/* Strategy name + confidence */}
                    <div className="flex items-center justify-between">
                      <div className="flex flex-col gap-0.5">
                        <span className="text-[10px] font-bold text-slate-300">[{sig.top_strategy}] {sig.top_strategy_name}</span>
                        <span className="text-[9px] text-slate-500 font-mono">
                          {sig.consensus_bull ?? 0}B / {sig.consensus_bear ?? 0}S · {(sig.strategies_fired ?? []).length} strategies
                        </span>
                      </div>
                      <div className={`text-xs font-black font-mono px-2 py-1 rounded-lg ${sig.confidence >= 75 ? "text-emerald-400 bg-emerald-500/10" : sig.confidence >= 60 ? "text-amber-400 bg-amber-500/10" : "text-slate-400 bg-slate-800"}`}>
                        {sig.confidence}/100
                      </div>
                    </div>

                    {/* Entry / Stop / T1 / T2 / R:R grid */}
                    <div className="grid grid-cols-2 gap-1.5 text-[10px] font-mono">
                      <div className="bg-slate-950/50 border border-slate-800/50 rounded-lg px-2.5 py-2 flex justify-between">
                        <span className="text-slate-500">Entry</span>
                        <span className="font-bold text-white">${Number(sig.price).toFixed(2)}</span>
                      </div>
                      <div className="bg-slate-950/50 border border-amber-500/20 rounded-lg px-2.5 py-2 flex justify-between">
                        <span className="text-slate-500">Stop</span>
                        <span className="font-bold text-amber-400">${Number(sig.stop).toFixed(2)}</span>
                      </div>
                      <div className="bg-slate-950/50 border border-emerald-500/20 rounded-lg px-2.5 py-2 flex justify-between">
                        <span className="text-slate-500 flex items-center gap-1"><Target size={8} /> T1</span>
                        <span className="font-bold text-emerald-400">${Number(sig.t1).toFixed(2)}</span>
                      </div>
                      <div className="bg-slate-950/50 border border-cyan-500/20 rounded-lg px-2.5 py-2 flex justify-between">
                        <span className="text-slate-500 flex items-center gap-1"><Target size={8} /> T2</span>
                        <span className="font-bold text-cyan-400">${Number(sig.t2).toFixed(2)}</span>
                      </div>
                    </div>

                    {/* R:R + VWAP + RSI row */}
                    <div className="grid grid-cols-3 gap-1.5 text-[10px] font-mono">
                      <div className="bg-slate-950/30 border border-slate-800/50 rounded p-1.5 flex flex-col items-center gap-0.5">
                        <span className="text-slate-500 text-[8px] uppercase">R:R</span>
                        <span className={`font-bold ${(sig.rr ?? 0) >= 2 ? "text-emerald-400" : "text-amber-400"}`}>1:{sig.rr ?? "—"}</span>
                      </div>
                      <div className="bg-slate-950/30 border border-slate-800/50 rounded p-1.5 flex flex-col items-center gap-0.5">
                        <span className="text-slate-500 text-[8px] uppercase">VWAP</span>
                        <span className="font-semibold text-indigo-400">{sig.vwap > 0 ? `$${Number(sig.vwap).toFixed(2)}` : "—"}</span>
                      </div>
                      <div className="bg-slate-950/30 border border-slate-800/50 rounded p-1.5 flex flex-col items-center gap-0.5">
                        <span className="text-slate-500 text-[8px] uppercase">RSI</span>
                        <span className={`font-semibold ${sig.action === "BUY" ? "text-emerald-400" : "text-rose-400"}`}>
                          {sig.rsi != null ? Number(sig.rsi).toFixed(1) : "—"}
                        </span>
                      </div>
                    </div>

                    {/* Exit note */}
                    {sig.exit_note && (
                      <div className="text-[9px] text-slate-400 bg-slate-950/40 border border-slate-800/40 rounded px-2.5 py-1.5 font-mono leading-relaxed">
                        📌 {sig.exit_note}
                      </div>
                    )}

                    {/* Top conditions met */}
                    {sig.conditions_met && sig.conditions_met.length > 0 && (
                      <div className="flex flex-col gap-1">
                        <span className="text-[8px] uppercase font-bold text-slate-500 tracking-wider">Conditions met</span>
                        {sig.conditions_met.slice(0, 3).map((c, ci) => (
                          <span key={ci} className="text-[9px] text-slate-400 font-mono flex items-center gap-1">
                            <span className="text-emerald-500">✓</span> {c}
                          </span>
                        ))}
                      </div>
                    )}
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
