# Production Deployment Guide: Trading Bot Platform

This guide outlines step-by-step instructions to deploy the Trading Bot Platform monorepo entirely for free on production-grade cloud services:
1. **Frontend**: Vercel (Free Serverless)
2. **Backend**: Koyeb or Render (Free Container Hosting)
3. **Database**: Neon (Free Serverless Postgres)
4. **Cache/PubSub**: Upstash (Free Serverless Redis)

---

## 🗺️ Architectural Topology

```
                  ┌──────────────────────┐
                  │     Web Browser      │
                  └──────────┬───────────┘
                             │
                             ├──────────────────────────┐
                       HTTPS │                      WSS │ HTTPS
                             ▼                          ▼
                  ┌──────────────────────┐    ┌──────────────────────┐
                  │       Vercel         │    │     Koyeb/Render     │
                  │  (Next.js Frontend)  │    │   (FastAPI Backend)  │
                  └──────────────────────┘    └──────────┬───────────┘
                                                         │
                                    ┌────────────────────┼────────────────────┐
                                    ▼                    ▼                    ▼
                        ┌──────────────────────┐ ┌──────────────┐ ┌──────────────────────┐
                        │      Neon.tech       │ │   Upstash    │ │   Alpaca Real-time   │
                        │ (Serverless Postgres)│ │(Serverless   │ │      WebSockets      │
                        └──────────────────────┘ │   Redis)     │ └──────────────────────┘
                                                 └──────────────┘
```

---

## 🛠️ Step 1: Provision Cloud Databases (Free & Fully Managed)

### A. Serverless Postgres Database (Neon.tech)
1. Go to [Neon.tech](https://neon.tech/) and sign up for a free tier account.
2. Create a new project called `trading-platform`.
3. In the **Connection Details** dropdown on your dashboard, copy the PostgreSQL connection details. You will need:
   - Host name (e.g., `ep-cool-snowflake-123456.us-east-2.aws.neon.tech`)
   - Database name (`neondb` or `trading_platform`)
   - Username
   - Password
   - Port (`5432`)

### B. Serverless Redis Cache (Upstash)
1. Go to [Upstash.com](https://upstash.com/) and create a free tier account.
2. Click **Create Database** under the Redis tab.
3. Name your database `trading-redis` and select your preferred region.
4. Once created, copy the **Redis Connection URL** under the *Node.js* or *Web* tab (it looks like `redis://default:password@your-endpoint.upstash.io:31234`).
   > **Note**: For secure transport, you can use `rediss://...` (with double `s`) if SSL/TLS is supported.

---

## 🚀 Step 2: Deploy the FastAPI Backend to Koyeb (Recommended)

Koyeb is the ideal platform for running background loops and WebSockets because its free tier container does not go to sleep, ensuring uninterrupted real-time trade ingestion.

### A. Connection & Build Configuration
1. Push your monorepo code to your personal GitHub repository.
2. Create a free account on [Koyeb.com](https://www.koyeb.com/).
3. Click **Create Service** and authorize/link your GitHub repository.
4. Configure the service parameters in Koyeb:
   - **Repository Subdirectory**: `apps/backend` (this ensures Koyeb only builds the FastAPI component)
   - **Builder**: Select `Docker` (Koyeb will automatically build from the optimized `apps/backend/Dockerfile`)
   - **Port**: `8000`
   - **Instance Size**: `Free`

### B. Environment Variables Setup
Under the **Environment Variables** tab, add all keys from your local `.env`:

| Key | Value Source | Description |
|---|---|---|
| `POSTGRES_HOST` | Neon | Neon Host Name |
| `POSTGRES_USER` | Neon | Neon Username |
| `POSTGRES_PASSWORD` | Neon | Neon Password |
| `POSTGRES_PORT` | Neon | `5432` |
| `POSTGRES_DB` | Neon | Neon Database Name |
| `REDIS_URL` | Upstash | Upstash Redis connection URL |
| `OPENAI_API_KEY` | OpenAI | Your OpenAI API Key for real-time trade narrative insights |
| `ALPACA_API_KEY_ID` | Alpaca | Your Alpaca Live or Sandbox API Key ID |
| `ALPACA_API_SECRET_KEY` | Alpaca | Your Alpaca API Secret Key |
| `TELEGRAM_BOT_TOKEN` | Telegram | Telegram Bot token |
| `TELEGRAM_CHAT_ID` | Telegram | Telegram Chat ID |
| `TELEGRAM_ALERT_COOLDOWN`| Config | Cooldown between alerts in seconds (default `120`) |
| `CORS_ORIGINS` | Wildcard | `*` (or set explicitly to your Vercel URL after Step 3) |

5. Click **Deploy**. Koyeb will compile, build, and deploy the Docker container and expose a secure HTTPS URL (e.g. `https://trading-platform-backend.koyeb.app`).
6. Copy this URL.

---

## 🎨 Step 3: Deploy the Next.js Frontend to Vercel

Vercel is the premier platform for Next.js and has an incredibly fast, secure free edge serverless layer.

1. Go to [Vercel.com](https://vercel.com/) and sign up with GitHub.
2. Click **Add New** -> **Project**.
3. Select your Personal GitHub Repository.
4. In the project setup window:
   - **Framework Preset**: `Next.js`
   - **Root Directory**: `apps/frontend` (this ensures Vercel only targets the frontend codebase)
5. **Environment Variables**:
   Under the Environment Variables section, add the target WebSocket URL:
   - **Key**: `NEXT_PUBLIC_WS_URL`
   - **Value**: `wss://trading-platform-backend.koyeb.app/api/v1/ws` 
     *(Replace `trading-platform-backend.koyeb.app` with your actual Koyeb/Render app URL, replacing the `https://` prefix with `wss://` for secure WebSockets)*
6. Click **Deploy**! Vercel will automatically build the Next.js static and serverless files and serve them.

---

## ✅ Step 4: Verification Checklist

1. **Load Page**: Navigate to your production Vercel URL (e.g., `https://trading-bot-platform.vercel.app`).
2. **WebSocket Handshake**: Verify the green banner in the top-right header: **"LIVE MULTI-FEED CONNECTED"**.
3. **Data Streaming**: Watch the chart populate with real-time candles compiled from the active Alpaca stream.
4. **Alert Triggering**: Perform a test buy/sell trigger or wait for a strategy threshold to cross, and verify the Telegram channel receives a beautifully formatted HTML message detailing entry, stop, and price targets!
