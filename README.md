# AI-Powered Trading Intelligence Platform

Welcome to the AI-Powered Trading Intelligence Platform, a production-grade modular monorepo designed for real-time market data ingestion, rolling candle aggregation, plugin-based technical indicator calculation, signal strategy evaluation, post-signal risk filtering, and external alert delivery.

---

## 📂 Repository Structure

The monorepo layout is strictly organized to promote decoupling, loose coupling, and simple service extraction:

```txt
trading-platform/
│
├── apps/                          # Deployable application shells
│   ├── frontend/                  # Next.js 14 real-time Web dashboard
│   └── backend/                   # FastAPI Python backend monolith
│
├── services/                      # Isolated logical domain service packages
│   ├── market-data/               # Handles WebSocket tick feeds
│   ├── candle-engine/             # Aggregates price tick logs into OHLCV candles
│   ├── indicator-engine/          # Incremental indicator calculators (RSI, VWAP)
│   ├── signal-engine/             # Evaluation rules strategy solver
│   ├── risk-engine/               # Volatility and balance safety check boundaries
│   ├── alert-engine/              # External telegram and discord notification routers
│   └── ai-engine/                 # AI market context explanation generator
│
├── packages/                      # Highly reusable, decoupled shared modules
│   ├── shared-types/              # System-wide type mappings
│   ├── event-schemas/             # Versioned JSON and Pydantic event contracts
│   ├── common-utils/              # Logger, cryptography, and datetime converters
│   └── runtime-core/              # EventBus and RuntimeState structures
│
├── infra/                         # Docker, PostgreSQL, and Redis setups
├── tools/
│   └── architecture-mcp/          # Custom Architecture Governance MCP Server
│
└── docs/                          # Architectural documentation repository
```

---

## 🛠️ Architecture Governance via custom MCP Server

To ensure zero architectural drift during AI-assisted development (in Cursor / Anti Gravity), we have built an **Architecture Governance Model Context Protocol (MCP) Server** under `tools/architecture-mcp/`.

### Exposing Documents & Governance Tools
- **Resources**: Dynamic access to our architectural blueprint files (e.g. `docs/SYSTEM_OVERVIEW.md`, `docs/EVENT_ARCHITECTURE.md`) directly via `trading-docs://` URI protocol.
- **Tools**: Type-safe lookup commands (e.g., `get_architecture_overview`, `get_event_schema`, `get_engineering_standards`) enabling AI assistants to consistently generate conforming code.

---

## 🚀 Getting Started

### 1. Register and Build the Governance MCP Server
To configure your workspace development support, install and compile the server:
```bash
cd tools/architecture-mcp
npm install
npm run build
```

### 2. Configure Your IDE
Add the custom MCP server in Cursor under **Settings** -> **Features** -> **MCP**:
- **Name**: `trading-platform-architecture`
- **Type**: `command`
- **Command**: `node /home/sai-vittal/Desktop/Trading-bot-platform/tools/architecture-mcp/build/index.js`

### 3. Read the Guides
Examine our detailed documentation files inside the `docs/` folder to learn about engineering conventions and event flow designs.
