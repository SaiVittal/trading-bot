#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListResourcesRequestSchema,
  ReadResourceRequestSchema,
  ListToolsRequestSchema,
  CallToolRequestSchema,
  McpError,
  ErrorCode,
} from "@modelcontextprotocol/sdk/types.js";
import { platformResources, resourceHandlers } from "./resources/handlers.js";
import { toolHandlers } from "./tools/handlers.js";
import { SERVER_INFO } from "./utils/constants.js";
import { logger } from "./utils/logger.js";

logger.info(`Initializing ${SERVER_INFO.NAME} (v${SERVER_INFO.VERSION})...`);

// 1. Initialize the MCP Server
const server = new Server(
  {
    name: SERVER_INFO.NAME,
    version: SERVER_INFO.VERSION,
  },
  {
    capabilities: {
      tools: {},
      resources: {},
    },
  }
);

// 2. Register handler to list available resources
server.setRequestHandler(ListResourcesRequestSchema, (async () => {
  logger.debug("Received ListResourcesRequestSchema handler call");
  return {
    resources: platformResources.map(res => ({
      uri: res.uri,
      name: res.name,
      mimeType: res.mimeType,
      description: res.description,
    })),
  };
}) as any);

// 3. Register handler to read resource content
server.setRequestHandler(ReadResourceRequestSchema, (async (request: any) => {
  const uri = request.params.uri;
  logger.debug(`Received ReadResourceRequestSchema handler call for URI: ${uri}`);
  try {
    const response = await resourceHandlers.readResource(uri);
    return {
      contents: response.contents.map(c => ({
        uri: c.uri,
        text: c.text,
        mimeType: c.mimeType,
      })),
    };
  } catch (error) {
    logger.error(`Error reading resource: ${(error as Error).message}`);
    throw new McpError(ErrorCode.InvalidRequest, (error as Error).message);
  }
}) as any);

// 4. Register handler to list available tools
server.setRequestHandler(ListToolsRequestSchema, (async () => {
  logger.debug("Received ListToolsRequestSchema handler call");
  return {
    tools: [
      {
        name: "get_architecture_overview",
        description: "Retrieve the trading intelligence platform system architectural overview",
        inputSchema: { type: "object", properties: {}, required: [] }
      },
      {
        name: "get_event_schema",
        description: "Retrieve schema definitions and payload examples for system events",
        inputSchema: {
          type: "object",
          properties: {
            eventType: {
              type: "string",
              description: "Specific event type to retrieve schema for (e.g. 'tick_received', 'candle_closed', 'signal_generated')"
            }
          },
          required: []
        }
      },
      {
        name: "get_engineering_standards",
        description: "Retrieve coding, style, quality, and Git flow standard conventions",
        inputSchema: {
          type: "object",
          properties: {
            category: {
              type: "string",
              enum: ["all", "backend", "frontend", "git"],
              description: "Filter standards by category"
            }
          },
          required: []
        }
      },
      {
        name: "get_repository_structure",
        description: "Retrieve the standard folder layout structure of the monorepo platform",
        inputSchema: { type: "object", properties: {}, required: [] }
      },
      {
        name: "get_runtime_pipeline",
        description: "Retrieve the step-by-step real-time pipeline event execution flow explanation",
        inputSchema: { type: "object", properties: {}, required: [] }
      },
      {
        name: "get_domain_models",
        description: "Retrieve core domain entity descriptions (Tick, Candle, Signal, Trade, etc.)",
        inputSchema: { type: "object", properties: {}, required: [] }
      },
      {
        name: "get_service_boundaries",
        description: "Retrieve the specific logical business boundaries of all modules in /services",
        inputSchema: { type: "object", properties: {}, required: [] }
      },
      {
        name: "get_backend_stack",
        description: "Retrieve technical details of the FastAPI / asyncio / Postgres / Redis backend stack",
        inputSchema: { type: "object", properties: {}, required: [] }
      },
      {
        name: "get_frontend_stack",
        description: "Retrieve technical details of the Next.js / Zustand / Tailwind frontend stack",
        inputSchema: { type: "object", properties: {}, required: [] }
      },
      {
        name: "get_observability_rules",
        description: "Retrieve guidelines for JSON logging, metrics collection, and tracing",
        inputSchema: { type: "object", properties: {}, required: [] }
      }
    ]
  };
}) as any);

// 5. Register handler to call/execute a tool
server.setRequestHandler(CallToolRequestSchema, (async (request: any) => {
  const { name, arguments: args } = request.params;
  logger.debug(`Received CallToolRequestSchema handler call for: ${name}`, args || {});

  try {
    switch (name) {
      case "get_architecture_overview":
        return await toolHandlers.get_architecture_overview();
      case "get_event_schema": {
        const parsedArgs = args as { eventType?: string };
        return await toolHandlers.get_event_schema(parsedArgs);
      }
      case "get_engineering_standards": {
        const parsedArgs = args as { category?: string };
        return await toolHandlers.get_engineering_standards(parsedArgs);
      }
      case "get_repository_structure":
        return await toolHandlers.get_repository_structure();
      case "get_runtime_pipeline":
        return await toolHandlers.get_runtime_pipeline();
      case "get_domain_models":
        return await toolHandlers.get_domain_models();
      case "get_service_boundaries":
        return await toolHandlers.get_service_boundaries();
      case "get_backend_stack":
        return await toolHandlers.get_backend_stack();
      case "get_frontend_stack":
        return await toolHandlers.get_frontend_stack();
      case "get_observability_rules":
        return await toolHandlers.get_observability_rules();
      default:
        logger.error(`Requested unknown tool: ${name}`);
        throw new McpError(ErrorCode.MethodNotFound, `Unknown tool: ${name}`);
    }
  } catch (error) {
    if (error instanceof McpError) {
      throw error;
    }
    logger.error(`Error executing tool ${name}: ${(error as Error).message}`);
    throw new McpError(ErrorCode.InternalError, (error as Error).message);
  }
}) as any);

// 6. Connect to Stdio Transport and start the server
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  logger.info("Trading Intelligence Platform Architecture MCP Server started successfully over stdio");
}

main().catch((error) => {
  logger.error("Fatal error in MCP Server bootstrapper:", error);
  process.exit(1);
});
