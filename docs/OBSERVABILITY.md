# Observability

The platform enforces observability as a primary operational concern to facilitate debugging, latency profiling, and real-time operational health checks.

## Logging Guidelines
- **Structured JSON Logging**: Every log entry must be written in structured JSON to enable easy indexing, parsing, and query filters.
- **Correlation ID Injection**: Generate a unique `correlation_id` at the HTTP router, WebSocket connection, or Task worker boundary. Propagate this ID through all async calls and include it in every log.
- **No Plain Print Statements**: Standard python `print()` or console logs are strictly prohibited. Use the structured application logger instead.

## Metrics (Prometheus & Grafana)
We export high-value metrics on `/metrics` endpoint for Prometheus scraping:
- `ticks_ingested_total`: Counter tracking volume of inbound ticks.
- `candle_aggregation_latency_seconds`: Histogram of aggregation duration.
- `signals_generated_total`: Counter of strategies triggering trade ideas.
- `signals_rejected_total`: Counter of signals rejected by the risk engine, broken down by reason.
- `alert_delivery_latency_seconds`: Time elapsed from signal generation to successful Telegram/Discord delivery.

## Tracing (OpenTelemetry)
- **Distributed Tracing**: Standardize trace scopes for every asynchronous pipeline execution.
- **Signal Lifecycle Trace**: Trace a signal from generation to delivery:
  `signal_generated` (span 1) -> `risk_validated` (span 2) -> `alert_sent` (span 3).
