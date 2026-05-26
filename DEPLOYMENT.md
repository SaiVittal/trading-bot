# 🚀 Production Cloud Deployment Manual: Trading Bot Platform

This guide provides a comprehensive, step-by-step roadmap to publish the entire Trading Bot Platform monorepo to production-grade cloud environments entirely on free serverless tiers:

1. **Database Layer**: Neon (Serverless PostgreSQL)
2. **Event Bus & Cache Layer**: Upstash (Serverless Redis)
3. **Backend Service**: Koyeb (Serverless Container with WebSocket support)
4. **Frontend Dashboard**: Vercel (Edge Serverless Layer via CLI)

---

## 🗺️ Architectural Topology

```
                  ┌────────────────────────────────────────┐
                  │          Public Web Browser            │
                  │   https://[frontend].vercel.app        │
                  └──────────────────┬─────────────────────┘
                                     │
                                     ├─────────────────────────────────────────┐
                               HTTPS │                                     WSS │ HTTPS
                                     ▼                                         ▼
                  ┌────────────────────────────────────────┐    ┌────────────────────────────────────────┐
                  │             Vercel Edge                │    │               Koyeb App                │
                  │      (Next.js Frontend Dashboard)      │    │       (FastAPI Monolith Backend)       │
                  └────────────────────────────────────────┘    └──────────────────┬─────────────────────┘
                                                                                   │
                                              ┌────────────────────────────────────┼────────────────────────────────────┐
                                              ▼                                    ▼                                    ▼
                                 ┌────────────────────────┐           ┌────────────────────────┐           ┌────────────────────────┐
                                 │       Neon.tech        │           │      Upstash.com       │           │     Alpaca Markets     │
                                 │ (Serverless Postgres)  │           │   (Serverless Redis)   │           │   (Live Quote Stream)  │
                                 └────────────────────────┘           └────────────────────────┘           └────────────────────────┘
```

---

## 🛠️ Step 1: Provision the Serverless Database (Neon.tech)

Neon provides a fully managed, serverless PostgreSQL database that matches production requirements and offers a generous free tier.

1. **Sign Up**: Visit [Neon.tech](https://neon.tech/) and register for a free account.
2. **Create Project**:
   * **Project Name**: `trading-platform`
   * **PostgreSQL Version**: `16` (Recommended)
   * **Region**: Select the region closest to you or closest to Koyeb's deployment region (e.g., `N. Virginia (us-east-1)` or `Frankfurt (eu-central-1)`).
3. **Obtain Connection Details**:
   * On your Neon dashboard, locate the **Connection Details** box.
   * Toggle the view to **Connection String** and copy it. It will look like this:
     ```text
     postgresql://neondb_owner:npg_xYz123@ep-cool-snowflake-12345.us-east-2.aws.neon.tech/neondb?sslmode=require
     ```
   * Save this connection string. You will split it into variables for your backend environment config:
     * **`POSTGRES_HOST`**: `ep-cool-snowflake-12345.us-east-2.aws.neon.tech`
     * **`POSTGRES_USER`**: `neondb_owner`
     * **`POSTGRES_PASSWORD`**: `npg_xYz123`
     * **`POSTGRES_DB`**: `neondb`
     * **`POSTGRES_PORT`**: `5432`

---

## ⚡ Step 2: Provision the Serverless Event Bus (Upstash Redis)

Upstash offers serverless Redis designed for low-latency Web use-cases and is the backbone of the real-time event pipeline.

1. **Sign Up**: Visit [Upstash.com](https://upstash.com/) and register for a free account.
2. **Create Redis Database**:
   * Click **Create Database** under the Redis tab.
   * **Name**: `trading-redis`
   * **Region**: Select the same region chosen for Neon (e.g., `us-east-1`) to minimize cross-regional latency.
3. **Obtain Connection URL**:
   * Scroll down to the **Connect to your database** section.
   * Under the **Redis Connect URL** tab, copy the primary connection string. It will look like this:
     ```text
     redis://default:abc123xyz789@us1-cool-panda-12345.upstash.io:6379
     ```
   * **For Secure SSL Connection**: Change the prefix from `redis://` to `rediss://` (note the double `s` at the end) to encrypt the Redis transport:
     ```text
     rediss://default:abc123xyz789@us1-cool-panda-12345.upstash.io:6379/0
     ```

---

## 🐳 Step 3: Deploy the FastAPI Backend to Koyeb

Koyeb is a modern container hosting platform. Its free tier containers do not go to sleep, which makes it perfect for running the continuous Alpaca WebSocket data ingestion loop.

### A. Code Repository Setup
1. Verify all code changes are pushed to your personal GitHub repository:
   `https://github.com/slr1429/Trading_Bot.git`

### B. Koyeb App Creation & Configuration
1. **Sign Up**: Create a free account at [Koyeb.com](https://www.koyeb.com/).
2. **Create Service**:
   * Click **Create Service** on the dashboard.
   * Select **GitHub** as your deployment source and authorize/link your repository.
3. **Configure Build parameters**:
   * **Repository Subdirectory**: `apps/backend` (This is critical: it tells Koyeb to isolate the backend folder).
   * **Builder**: Select **Docker** (Koyeb will read our custom `apps/backend/Dockerfile` and compile the container).
   * **Instance Size**: `Free`
   * **Port**: `8000` (FastAPI default port)
   * **Path**: `/api/v1/health` (For Koyeb's integrated HTTP healthchecks)

### C. Environment Variables Mapping
In the Koyeb service setup, add the following environment variables (matches your local keys):

| Key | Value Source | Description |
|---|---|---|
| `POSTGRES_HOST` | Neon Dashboard | e.g. `ep-cool-snowflake-12345.us-east-2.aws.neon.tech` |
| `POSTGRES_USER` | Neon Dashboard | e.g. `neondb_owner` |
| `POSTGRES_PASSWORD` | Neon Dashboard | e.g. `npg_xYz123` |
| `POSTGRES_PORT` | Neon Dashboard | `5432` |
| `POSTGRES_DB` | Neon Dashboard | e.g. `neondb` |
| `REDIS_URL` | Upstash Dashboard | e.g. `rediss://default:abc123xyz789@...:6379/0` |
| `OPENAI_API_KEY` | OpenAI | Your OpenAI secret key (starts with `sk-proj-...`) |
| `ALPACA_API_KEY_ID` | Alpaca Markets | Your live or sandbox Alpaca key ID |
| `ALPACA_API_SECRET_KEY` | Alpaca Markets | Your live or sandbox Alpaca secret key |
| `TELEGRAM_BOT_TOKEN` | Telegram BotFather | Bot token (e.g. `8618742280:AAHb...`) |
| `TELEGRAM_CHAT_ID` | Telegram | Target chat or channel ID (e.g. `-5129364719`) |
| `TELEGRAM_ALERT_COOLDOWN`| Configuration | Default to `120` (in seconds) |
| `MIN_CONFIDENCE` | Configuration | Default to `55` |
| `CORS_ORIGINS` | Configuration | Set to `*` to allow frontend cross-origin handshakes |

4. **Deploy**: Click **Deploy**. Koyeb will build the Docker container and deploy it.
5. **Get Backend Domain**: Once the service is healthy, copy the public URL generated by Koyeb (e.g., `https://trading-platform-backend-[username].koyeb.app`).

---

## 🎨 Step 4: Deploy the Next.js Frontend to Vercel

Since we have already authenticated your Vercel CLI session locally, we can deploy the Next.js frontend directly from your terminal!

### A. Pre-Deployment Configuration
We must supply the environment variable `NEXT_PUBLIC_WS_URL` to Vercel at build time so that Next.js compiles the WebSocket routing dynamically.
1. Take your public Koyeb backend domain (e.g., `https://trading-platform-backend.koyeb.app`) and convert the prefix from `https://` to `wss://`:
   * **Backend URL**: `https://trading-platform-backend.koyeb.app`
   * **WebSocket URL**: `wss://trading-platform-backend.koyeb.app/api/v1/ws`

### B. Trigger CLI Deployment
Run the following commands in the workspace terminal:

1. **Initialize & Link Project**:
   ```bash
   npx vercel --cwd apps/frontend
   ```
   * *When prompted to set up and deploy*: Say **Yes** (`Y`).
   * *Select Scope*: Select your personal scope (`saivittal`).
   * *Link to an existing project*: Say **No** (`N`).
   * *Project Name*: `trading-platform-frontend`
   * *Directory location*: `./`
   * *Modify settings*: Say **No** (`N`). Vercel will auto-detect Next.js.

2. **Add the WebSocket Environment Variable**:
   ```bash
   npx vercel env add NEXT_PUBLIC_WS_URL production
   ```
   * When prompted for the value, paste your secure WebSocket URL:
     `wss://[koyeb-app-name].koyeb.app/api/v1/ws`

3. **Deploy to Production**:
   ```bash
   npx vercel --prod --cwd apps/frontend
   ```
   Vercel will compile your Next.js application, bake in the environment variables, and upload the static edge layers! It will return a live production URL (e.g., `https://trading-platform-frontend.vercel.app`).

---

## ✅ Step 5: Verification & Launch Check

1. **Load Dashboard**: Open your live Vercel URL in your browser.
2. **Handshake Check**: Observe the indicator in the top-right header: **"LIVE MULTI-FEED CONNECTED"** (rendered in emerald-green). This confirms the browser has successfully established a secure WebSocket handshake with your container on Koyeb.
3. **Candle Aggregator**: Watch real-time quotes populate the stock lists and the candlestick chart redraw dynamically as Alpaca streams trades.
4. **Signal Pipeline**: When technical strategies cross minimum confidence, verify the console displays quants insights and Telegram dispatches beautifully formatted alerts detailing your exact entry, stop-loss, and multi-timeframe price targets!
