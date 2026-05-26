# AI-Powered Trading Intelligence Platform

Welcome to the AI-Powered Trading Intelligence Platform, a production-grade modular monorepo designed for real-time market data ingestion, rolling candle aggregation, plugin-based technical indicator calculation, signal strategy evaluation, post-signal risk filtering, and external alert delivery.

---

## 📂 Repository Structure

The monorepo layout is strictly organized to promote decoupling, loose coupling, and simple service extraction:

```txt
trading-platform/
│
├── apps/                          # Deployable application shells
│   ├── frontend/                  # Next.js 15 real-time Web dashboard
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

#### 1. Register and Build the Governance MCP Server
To configure your workspace development support, install and compile the server:
```bash
cd tools/architecture-mcp
npm install
npm run build
```

#### 2. Configure Your IDE
Add the custom MCP server in Cursor under **Settings** -> **Features** -> **MCP**:
- **Name**: `trading-platform-architecture`
- **Type**: `command`
- **Command**: `node /home/sai-vittal/Desktop/Trading-bot-platform/tools/architecture-mcp/build/index.js`

---

## 🚀 Production Cloud Deployment (Free Tier)

For a stable, live production environment, we deploy the platform to fully managed free cloud services:
- **Frontend (Next.js)** is hosted on **Vercel** (Free serverless hosting).
- **Backend (FastAPI)** is hosted on **Koyeb** or **Render** (Free container service).
- **Postgres Database** is hosted on **Neon.tech** (Free serverless Postgres).
- **In-Memory Store (Redis)** is hosted on **Upstash** (Free serverless Redis).

For a complete step-by-step walkthrough, environment variable mapping, and database provisioning details, please refer to the [Production Deployment Guide (DEPLOYMENT.md)](./DEPLOYMENT.md).

---

## 🚀 Local Setup & Dev Runnable Guide

This guide details how to configure, boot, and run the entire trading platform stack locally.

### 📋 Prerequisites
Ensure you have the following installed on your machine:
* **Docker & Docker Compose** (Highly Recommended)
* **Node.js** (v18+) & **npm** (for local frontend work)
* **Python** (v3.12+) & **pip** (for local backend work)

---

### 🔑 Step 1: Configure Environment Variables
Open the root [`.env` configuration file](file:///home/sai-vittal/Desktop/Trading-bot-platform/.env) and populate your developer keys:

```ini
# Database & Cache (Docker Mapped)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=trading_platform
REDIS_URL=redis://redis:6379/0

# 🧠 OpenAI GPT-4o Insights Config (Required for AI insights)
OPENAI_API_KEY=sk-proj-YOUR_OPENAI_API_KEY_HERE

# 🦙 Alpaca Markets Real-Time WebSocket Config (Optional)
# If provided, triggers low-latency IEX WebSockets. Otherwise falls back to Yahoo.
ALPACA_API_KEY_ID=YOUR_ALPACA_API_KEY_ID_HERE
ALPACA_API_SECRET_KEY=YOUR_ALPACA_API_SECRET_KEY_HERE

# 📢 Telegram Bot Delivery Config (Optional)
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_FROM_BOTFATHER
TELEGRAM_CHAT_ID=-100YOUR_NUMERIC_CHAT_ID_HERE
```

---

### 🐳 Method A: One-Click Docker Compose (Recommended)
This is the simplest way to boot the complete ecosystem, including PostgreSQL, Redis, the FastAPI backend monolith, and the Next.js frontend dashboard, with a single command.

1. **Boot the complete stack**:
   ```bash
   docker compose up -d --build
   ```
2. **Access local services**:
   * **Real-time Next.js UI Dashboard**: [http://localhost:3000](http://localhost:3000)
   * **FastAPI Swagger REST Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)
   * **FastAPI Live WebSocket Feed**: `ws://localhost:8000/api/v1/ws`
   * **Postgres Database**: `localhost:5432`
   * **Redis Event Bus**: `localhost:6379`
3. **Inspect live service logs**:
   ```bash
   docker compose logs -f backend
   ```
4. **Shutdown the stack**:
   ```bash
   docker compose down
   ```

---

### 💻 Method B: Local Manual Development
Use this setup if you want to run the python and javascript servers directly in your terminal for hot-reloading development work.

#### 1. Spin up the Database & Cache Infrastructures (via Docker)
To avoid manual installations of Postgres and Redis, spin just them up in the background:
```bash
docker compose up -d postgres redis
```

#### 2. Run the FastAPI Python Backend
1. **Navigate to the backend directory and set up a virtual environment**:
   ```bash
   cd apps/backend
   python -m venv venv
   ```
2. **Activate the virtual environment**:
   * **Linux/macOS**: `source venv/bin/activate`
   * **Windows**: `venv\Scripts\activate`
3. **Install standard python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Boot the hot-reloading backend dev server**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

#### 3. Run the Next.js Frontend Dev Server
1. **Navigate to the frontend directory**:
   ```bash
   cd ../frontend
   ```
2. **Install node package modules**:
   ```bash
   npm install
   ```
3. **Launch the hot-reloading dev server**:
   ```bash
   npm run dev
   ```
4. Open your browser and navigate to [http://localhost:3000](http://localhost:3000) to view your premium trading dashboard!

---

### 📡 Verifying Operations in the UI
Once the dashboard loads:
1. Observe the **Live Watchlist** (TSLA, AAPL, NVDA, SPY, MSFT) actively ticking in the left sidebar.
2. Click any card on the watchlist to focus the chart, technical stats, and OpenAI GPT-4o Insights on that asset.
3. Type any ticker (e.g. `AMD`, `NFLX`, `COIN`) in the search input box and hit Enter. The stock will instantly be appended to the watchlist and streamed live!
4. Check the **Bot Alert Log** on the right to see formatted buy/sell crossover alert notifications and their Telegram routing confirmation toasts.
