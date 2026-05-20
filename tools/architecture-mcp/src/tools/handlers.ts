import { docService } from "../services/docService.js";
import { logger } from "../utils/logger.js";

export interface ToolResponse {
  content: Array<{
    type: "text";
    text: string;
  }>;
}

/**
 * Handlers for each of the 10 custom tools
 */
export const toolHandlers = {
  /**
   * 1. get_architecture_overview
   */
  async get_architecture_overview(): Promise<ToolResponse> {
    logger.info("Executing tool: get_architecture_overview");
    const overview = await docService.readDoc("SYSTEM_OVERVIEW.md");
    return {
      content: [
        {
          type: "text",
          text: `## Architecture Overview\n\n${overview}`
        }
      ]
    };
  },

  /**
   * 2. get_event_schema
   */
  async get_event_schema(args: { eventType?: string }): Promise<ToolResponse> {
    logger.info("Executing tool: get_event_schema", args);
    const content = await docService.readDoc("EVENT_ARCHITECTURE.md");
    
    if (args.eventType) {
      // Basic extraction logic for specific event schema
      const regex = new RegExp(`(###\\s+\\d+\\.\\s+\`?${args.eventType}\`?[\\s\\S]*?)(?=###|##|$)`, "i");
      const match = content.match(regex);
      if (match && match[1]) {
        logger.debug(`Successfully extracted matching event schema for: ${args.eventType}`);
        return {
          content: [
            {
              type: "text",
              text: `## Event Schema for: ${args.eventType}\n\n${match[1].trim()}`
            }
          ]
        };
      }
      logger.warn(`Could not extract matching event schema for: ${args.eventType}. Returning full document.`);
    }

    return {
      content: [
        {
          type: "text",
          text: content
        }
      ]
    };
  },

  /**
   * 3. get_engineering_standards
   */
  async get_engineering_standards(args: { category?: string }): Promise<ToolResponse> {
    logger.info("Executing tool: get_engineering_standards", args);
    const content = await docService.readDoc("ENGINEERING_STANDARDS.md");
    const category = args.category || "all";

    if (category !== "all") {
      let regex: RegExp;
      if (category === "backend") {
        regex = /(## Backend Standards[\s\\S]*?)(?=##|$)/;
      } else if (category === "frontend") {
        regex = /(## Frontend Standards[\s\\S]*?)(?=##|$)/;
      } else {
        regex = /(## Git[\s\\S]*?)(?=##|$)/;
      }

      const match = content.match(regex);
      if (match && match[1]) {
        logger.debug(`Successfully extracted filtered standards for category: ${category}`);
        return {
          content: [
            {
              type: "text",
              text: `## Engineering Standards - ${category.toUpperCase()}\n\n${match[1].trim()}`
            }
          ]
        };
      }
      logger.warn(`Failed to extract standards category: ${category}. Returning all standards.`);
    }

    return {
      content: [
        {
          type: "text",
          text: content
        }
      ]
    };
  },

  /**
   * 4. get_repository_structure
   */
  async get_repository_structure(): Promise<ToolResponse> {
    logger.info("Executing tool: get_repository_structure");
    const tree = `
trading-platform/
│
├── apps/                          # Application deployment shells
│   ├── frontend/                  # Next.js web dashboard
│   └── backend/                   # FastAPI monolith bootstrapper
│
├── services/                      # Decoupled domain business log modules
│   ├── market-data/               # Ingests third-party feed ticks
│   ├── candle-engine/             # Aggregates raw ticks into candles
│   ├── indicator-engine/          # Incremental mathematics solver (RSI, EMA, etc.)
│   ├── signal-engine/             # Evaluation engine matching technical patterns
│   ├── risk-engine/               # Post-signal gating & parameters check
│   ├── alert-engine/              # External message routing (Telegram/Discord)
│   └── ai-engine/                 # Dynamic market context generation
│
├── packages/                      # Highly reusable modular code libraries
│   ├── shared-types/              # Standard type definitions & interfaces
│   ├── event-schemas/             # Unified Event schemas (Pydantic / TS contracts)
│   ├── common-utils/              # Helpers: logger, crypto, datetime converters
│   └── runtime-core/              # EventBus and RuntimeState engines
│
├── infra/                         # Environment & third-party setup configurations
│   ├── docker/                    # Dockerfiles & environments configs
│   ├── nginx/                     # Reverse proxy & routing
│   ├── postgres/                  # Database migration & schema setup
│   └── redis/                     # Redis config
│
├── docs/                          # Architectural documentation repository
│
├── scripts/                       # Maintenance scripts and setup automation
├── tests/                         # End-to-end integration and regression suites
└── .github/                       # GitHub actions pipelines
`;
    return {
      content: [
        {
          type: "text",
          text: `## Repository Structure\n\n\`\`\`txt\n${tree.trim()}\n\`\`\``
        }
      ]
    };
  },

  /**
   * 5. get_runtime_pipeline
   */
  async get_runtime_pipeline(): Promise<ToolResponse> {
    logger.info("Executing tool: get_runtime_pipeline");
    const pipeline = `
The Core Data Flow follows an async-first pipeline:

1. **Market Feed**: External WebSocket feeds push high-frequency ticks.
2. **Tick Ingestion**: \`market-data\` service converts ticks into standard models and emits \`tick_received\` event.
3. **Candle Aggregator**: \`candle-engine\` maintains rolling buffers, updates active candle stats, and fires \`candle_closed\` at bucket boundaries.
4. **Indicator Engine**: \`indicator-engine\` incrementally updates indicators for that symbol/timeframe, firing \`indicator_updated\`.
5. **Signal Engine**: \`signal-engine\` runs strategy logic against indicators. If a pattern is matched, a \`signal_generated\` event is fired.
6. **Risk Validation Layer**: \`risk-engine\` processes the signal against daily limits, news windows, and volatility boundaries. Successful signals fire \`risk_validated\`.
7. **Alert Engine**: \`alert-engine\` formats and routes the alert to Telegram/Discord, emitting \`alert_sent\`.
8. **Dashboard Stream**: Real-time events are broadcasted over user WebSockets for UI display.
`;
    return {
      content: [
        {
          type: "text",
          text: `## Runtime Pipeline\n${pipeline.trim()}`
        }
      ]
    };
  },

  /**
   * 6. get_domain_models
   */
  async get_domain_models(): Promise<ToolResponse> {
    logger.info("Executing tool: get_domain_models");
    const domain = await docService.readDoc("DOMAIN_MODEL.md");
    return {
      content: [
        {
          type: "text",
          text: domain
        }
      ]
    };
  },

  /**
   * 7. get_service_boundaries
   */
  async get_service_boundaries(): Promise<ToolResponse> {
    logger.info("Executing tool: get_service_boundaries");
    const boundaries = `
## Service Boundaries

All business logic must be isolated in service packages under \`services/\`. Services communicate only via event models to prevent spaghetti direct coupling.

### 1. \`market-data\`
- **Boundary**: Establishes network links to external price APIs/WebSockets.
- **Output**: Emits \`tick_received\` events.

### 2. \`candle-engine\`
- **Boundary**: Keeps sliding window lists of ticks in-memory.
- **Output**: Emits \`candle_closed\` events once time bounds conclude.

### 3. \`indicator-engine\`
- **Boundary**: Contains mathematical plugins (using numpy & pandas). Strictly does incremental computing.
- **Output**: Emits \`indicator_updated\` events.

### 4. \`signal-engine\`
- **Boundary**: Strategist state-machine evaluating triggers and cooldown trackers.
- **Output**: Emits \`signal_generated\` events.

### 5. \`risk-engine\`
- **Boundary**: Checks global variables like daily PnL, open drawdowns, and market hours.
- **Output**: Emits \`risk_validated\` events.

### 6. \`alert-engine\`
- **Boundary**: Telegram API, Discord Webhook connectors.
- **Output**: Emits \`alert_sent\` events.

### 7. \`ai-engine\`
- **Boundary**: Generates LLM explanations for alerts using current chart context.
`;
    return {
      content: [
        {
          type: "text",
          text: boundaries.trim()
        }
      ]
    };
  },

  /**
   * 8. get_backend_stack
   */
  async get_backend_stack(): Promise<ToolResponse> {
    logger.info("Executing tool: get_backend_stack");
    const stack = `
## Backend Stack Details

- **Python 3.12+**: Leveraging modern type annotations, advanced pattern matching, and speed improvements.
- **FastAPI**: Supercharged async web server. Uses Pydantic v2 for lightning-fast parsing.
- **asyncio & asyncpg**: Native asynchronous event loop coupled with asyncpg for optimal PostgreSQL execution speeds.
- **SQLAlchemy 2.0**: Utilizing modern \`async_session\` with declarative type mappings.
- **Alembic**: Database migrations management.
- **Redis**: Acts as event broker, WebSocket session registry, and low-latency cache for fast state engines.
- **Docker Compose**: Containerized execution mirroring local production builds.
`;
    return {
      content: [
        {
          type: "text",
          text: stack.trim()
        }
      ]
    };
  },

  /**
   * 9. get_frontend_stack
   */
  async get_frontend_stack(): Promise<ToolResponse> {
    logger.info("Executing tool: get_frontend_stack");
    const stack = `
## Frontend Stack Details

- **Next.js & TypeScript**: Secure, compiler-verified React framework with robust routing.
- **Zustand**: Fast, boilerplate-free state manager for real-time WebSocket bindings.
- **TanStack Query (React Query)**: Handles declarative client-side caching, fetching, and background refetching.
- **TailwindCSS & shadcn/ui**: Modern, accessible UI styling framework with pre-styled component blocks.
- **Lightweight Charts (TradingView)**: Performant HTML5 canvas engine specifically engineered for real-time price updates and technical indicator rendering.
`;
    return {
      content: [
        {
          type: "text",
          text: stack.trim()
        }
      ]
    };
  },

  /**
   * 10. get_observability_rules
   */
  async get_observability_rules(): Promise<ToolResponse> {
    logger.info("Executing tool: get_observability_rules");
    const observability = await docService.readDoc("OBSERVABILITY.md");
    return {
      content: [
        {
          type: "text",
          text: observability
        }
      ]
    };
  }
};
