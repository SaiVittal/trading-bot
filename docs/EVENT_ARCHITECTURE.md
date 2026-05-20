# Event Architecture

The platform operates on a reactive, event-driven backbone designed around structured event payloads, versioned schemas, and highly resilient async event listeners.

## Core Events
- `tick_received`: Issued immediately upon ingestion of a new raw price/volume update.
- `candle_closed`: Issued when an active time-bucket (e.g., 5m) completes, indicating that candle data is finalized.
- `indicator_updated`: Fired once a plugin finishes its incremental math calculations for a symbol/timeframe.
- `signal_generated`: Triggered when technical rules of a Strategy match the current market profile.
- `risk_validated`: Dispatched when a signal passes all validation thresholds (stops, daily limits, news filters).
- `alert_sent`: Issued when external alert channels (Telegram/Discord) successfully process and transmit a notification.

## Generic Event Schema

All events flowing through the internal event bus and Redis Pub/Sub share a standard envelope:

```json
{
  "event_id": "uuid4",
  "event_type": "signal_generated",
  "timestamp": "2026-05-20T22:37:04Z",
  "symbol": "AAPL",
  "timeframe": "5m",
  "version": "1.0.0",
  "correlation_id": "uuid4",
  "payload": {}
}
```

### Event Payload Examples

#### 1. `tick_received`
```json
{
  "price": 178.45,
  "volume": 1200,
  "side": "buy",
  "source_timestamp": "2026-05-20T22:37:03.950Z"
}
```

#### 2. `candle_closed`
```json
{
  "open": 178.10,
  "high": 178.60,
  "low": 178.05,
  "close": 178.45,
  "volume": 45000,
  "start_time": "2026-05-20T22:35:00Z",
  "end_time": "2026-05-20T22:40:00Z"
}
```

## Event Principles

1. **Idempotent Consumers**: Every consumer is designed to process duplicate events safely without side-effects (e.g., matching signal IDs to prevent duplicate alerts).
2. **Retry-Safe Processing**: Event handoff is wrapped in structured async error boundaries. Unhandled exceptions trigger retry queues (exponential backoff) rather than crashing the loop.
3. **Correlation ID Propagation**: An event's `correlation_id` is passed downward through the entire processing tree (`tick_received` -> `candle_closed` -> `indicator_updated` -> `signal_generated` -> `risk_validated` -> `alert_sent`) to facilitate comprehensive observability and logging diagnostics.
