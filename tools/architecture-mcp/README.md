# Trading Intelligence Platform - Architecture Governance MCP Server

This is a production-grade Model Context Protocol (MCP) server engineered to serve as an **architecture governance and developer support layer** for AI-assisted workflows in Cursor / Anti Gravity.

It does **NOT** run trading logic or ingest WebSocket ticks. Instead, it acts as a single-source-of-truth metadata engine, allowing AI assistants to read engineering standards, event schemas, repository guidelines, and domain models dynamically.

---

## 🛠️ Setup & Installation

### 1. Install Node.js Dependencies
Navigate to the MCP server directory and install dependencies:
```bash
cd tools/architecture-mcp
npm install
```

### 2. Compile TypeScript
Compile the TypeScript code to standard ES modules (`build/index.js`):
```bash
npm run build
```

---

## 🚀 Exposed Model Context Protocol Features

### 📂 Resources
Exposes architectural design files under the `trading-docs://` URI scheme:
- `trading-docs://FOUNDATION_PROMPT.md` - Primary platform directives & constraints.
- `trading-docs://SYSTEM_OVERVIEW.md` - Block diagrams & internal communications.
- `trading-docs://EVENT_ARCHITECTURE.md` - Schema schemas & payloads JSON objects.
- `trading-docs://ENGINEERING_STANDARDS.md` - Strict FastAPI / Next.js conventions.
- `trading-docs://DOMAIN_MODEL.md` - Domain classes, entities glossary, relationships.
- `trading-docs://OBSERVABILITY.md` - Telemetry logging, trace spans, metrics maps.
- `trading-docs://MVP_SCOPE.md` - Lists features included/excluded in the MVP scope.

### ⚙️ Tools
Exposes 10 strongly-typed governance lookup tools:
1. `get_architecture_overview` - High-level system design summaries.
2. `get_event_schema` - Displays standard event metadata and examples (optional `eventType` filter).
3. `get_engineering_standards` - Returns styling and lint rules (optional `category` filter).
4. `get_repository_structure` - Detailed monorepo directories tree explanation.
5. `get_runtime_pipeline` - Event-driven data processing pipeline step-by-step description.
6. `get_domain_models` - Explains variables and roles for entities (Tick, Candle, Trade, etc.).
7. `get_service_boundaries` - Explains specific decoupled microservice modules in `services/`.
8. `get_backend_stack` - Python 3.12+, FastAPI, asyncio, Redis details.
9. `get_frontend_stack` - Next.js 14, TypeScript, zustand, Lightweight Charts details.
10. `get_observability_rules` - Standard telemetry JSON logs, Prometheus metric names.

---

## 💻 Integration with Cursor / Anti Gravity

To register this custom server as an architectural oracle inside Cursor:

1. Open your Cursor **Settings** -> **Features** -> **MCP**.
2. Click **+ Add New MCP Server**.
3. Fill out the configuration:
   - **Name**: `trading-platform-architecture`
   - **Type**: `command`
   - **Command**: `node /home/sai-vittal/Desktop/Trading-bot-platform/tools/architecture-mcp/build/index.js`
4. Click **Save**.

Now, whenever you prompt the AI in Cursor about writing new services, handlers, database entities, or frontends, the AI can query this MCP server to fetch structural boundaries and follow engineering guidelines perfectly!
