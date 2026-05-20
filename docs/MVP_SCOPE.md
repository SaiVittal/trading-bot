# MVP Scope

This document specifies the exact scope boundaries for the Initial Minimum Viable Product (MVP) of the Trading Intelligence Platform.

## Included
- **Live Market Data Ingestion**: High-throughput WebSocket ingestion of ticks.
- **Candle Aggregation**: Runtime rolling buffer management generating 1m, 5m, 15m, 1h, and 1d candles.
- **Core Indicators**:
  - Exponential Moving Average (EMA)
  - Relative Strength Index (RSI)
  - Moving Average Convergence Divergence (MACD)
  - Volume Weighted Average Price (VWAP)
  - Average True Range (ATR)
- **Signal Engine**: Strategy-based signal generation, cooldown enforcement, and suppression.
- **Risk Validation Layer**: Volatility evaluation, minimum Risk/Reward check, news suppression, and maximum signals-per-day filters.
- **Telegram Alerts**: Instant delivery of risk-filtered trading alerts with entry, stop loss, and target levels.
- **WebSocket Dashboard**: Live reactive UI displaying active candles, technical overlays, and signals.
- **Paper Trading**: Virtual trade simulation tracking execution prices and portfolio updates.
- **PostgreSQL Persistence**: Long-term storage of aggregated candles, generated signals, alerts, and performance metrics.

## Excluded
- **Automated Broker Execution**: Live order placement via actual broker APIs (Interactive Brokers, Binance, etc.).
- **Advanced ML Prediction**: Direct AI-based future price regression or predictive deep learning model execution.
- **Multi-Region Deployments**: Geographically distributed clusters.
- **Kubernetes / Complex Orchestration**: Premature scaling via K8s, Istio, or service meshes.
- **Massive Distributed Architecture**: Message brokers (Kafka, RabbitMQ) at Sprint 0.
