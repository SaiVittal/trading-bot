# Platform Foundation Blueprint

This document contains the master blueprint for the scalable, event-driven, AI-powered trading intelligence platform. It outlines the core architecture constraints and technology choices.

```markdown
You are a principal architect and senior staff engineer responsible for designing and implementing a scalable, event-driven, AI-powered trading intelligence platform.

The platform is NOT a high-frequency trading system.
The platform IS a real-time signal intelligence and analytics platform.

The primary objectives are:
- real-time market ingestion
- candle aggregation
- indicator computation
- signal generation
- risk filtering
- alert delivery
- AI-assisted analysis
- paper trading
- historical backtesting
- observability
- scalable architecture

The system must prioritize:
- async-first architecture
- event-driven communication
- modularity
- scalability
- observability
- fault tolerance
- maintainability
- testability
- clean architecture
- developer experience
- future service extraction
```

## Stack Architecture

### Backend Stack
- **Language**: Python 3.12+
- **Framework**: FastAPI
- **Asynchronous Loop**: asyncio & asyncpg
- **Database**: PostgreSQL (SQLAlchemy 2.0, Alembic)
- **Caching & Event Bus**: Redis & WebSockets
- **Deployment**: Docker Compose

### Frontend Stack
- **Language**: Next.js (TypeScript)
- **Styling**: TailwindCSS & shadcn/ui
- **State Management**: Zustand & TanStack Query
- **Charts**: Lightweight Charts (WebSockets)

## Core Domain Objects & Flow

### Core Flow
`Market Feed` -> `Tick Ingestion` -> `Candle Aggregation` -> `Indicator Engine` -> `Signal Engine` -> `Risk Validation Layer` -> `Alert Engine` -> `WebSocket Broadcast` -> `Dashboard UI`

### Domain Objects
- Tick, Candle, Indicator, Signal, Strategy, Alert, Trade, Portfolio, RuntimeState, SessionState

### Event Types
- `tick_received`, `candle_closed`, `indicator_updated`, `signal_generated`, `risk_validated`, `alert_created`, `alert_sent`, `trade_simulated`, `strategy_triggered`
