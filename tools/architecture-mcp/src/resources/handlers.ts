import { docService } from "../services/docService.js";
import { PROTOCOLS, MIME_TYPES } from "../utils/constants.js";
import { logger } from "../utils/logger.js";

export interface ResourceInfo {
  uri: string;
  name: string;
  mimeType: string;
  description: string;
}

export interface ResourceReadResponse {
  contents: Array<{
    uri: string;
    mimeType: string;
    text: string;
  }>;
}

// Available platform resources constructed dynamically using constants
export const platformResources: ResourceInfo[] = [
  {
    uri: `${PROTOCOLS.DOCS}//FOUNDATION_PROMPT.md`,
    name: "Platform Foundation Blueprint",
    mimeType: MIME_TYPES.MARKDOWN,
    description: "The primary architectural vision, constraints, and scope directives."
  },
  {
    uri: `${PROTOCOLS.DOCS}//SYSTEM_OVERVIEW.md`,
    name: "System Architectural Overview",
    mimeType: MIME_TYPES.MARKDOWN,
    description: "System diagram, pipelines, tech stack list, and internal communication overview."
  },
  {
    uri: `${PROTOCOLS.DOCS}//EVENT_ARCHITECTURE.md`,
    name: "Event Architecture & Schemas",
    mimeType: MIME_TYPES.MARKDOWN,
    description: "Event models, JSON payloads, and idempotency guarantees."
  },
  {
    uri: `${PROTOCOLS.DOCS}//ENGINEERING_STANDARDS.md`,
    name: "Engineering & Git Standards",
    mimeType: MIME_TYPES.MARKDOWN,
    description: "FastAPI conventions, frontend rules, and branch guidelines."
  },
  {
    uri: `${PROTOCOLS.DOCS}//DOMAIN_MODEL.md`,
    name: "Domain Entities Model",
    mimeType: MIME_TYPES.MARKDOWN,
    description: "Domain classes, entities relationships, and glossary definitions."
  },
  {
    uri: `${PROTOCOLS.DOCS}//OBSERVABILITY.md`,
    name: "Observability & Metrics Plan",
    mimeType: MIME_TYPES.MARKDOWN,
    description: "JSON logs structures, Prometheus endpoints, and OpenTelemetry spans."
  },
  {
    uri: `${PROTOCOLS.DOCS}//MVP_SCOPE.md`,
    name: "MVP Scope Boundaries",
    mimeType: MIME_TYPES.MARKDOWN,
    description: "Listing of features strictly included or excluded in MVP phases."
  }
];

/**
 * Handlers for MCP resource retrieval
 */
export const resourceHandlers = {
  /**
   * List all available architectural resources
   */
  listResources(): ResourceInfo[] {
    logger.info("Listing all available governance resources");
    return platformResources;
  },

  /**
   * Read the content of a resource dynamically based on URI
   */
  async readResource(uri: string): Promise<ResourceReadResponse> {
    logger.info(`Received request to read resource: ${uri}`);
    const url = new URL(uri);
    
    const expectedProtocol = PROTOCOLS.DOCS.replace(":", "");
    if (url.protocol !== PROTOCOLS.DOCS) {
      logger.error(`Unsupported resource protocol requested: ${url.protocol} (expected: ${PROTOCOLS.DOCS})`);
      throw new Error(`Unsupported resource protocol: ${url.protocol}`);
    }

    const filename = url.pathname.replace(/^\/\//, ""); // Strip leading slashes
    const resource = platformResources.find(r => r.uri.endsWith(filename));

    if (!resource) {
      logger.error(`Requested resource filename not matching platforms: ${filename}`);
      throw new Error(`Requested resource not found: ${filename}`);
    }

    const content = await docService.readDoc(filename);

    return {
      contents: [
        {
          uri: resource.uri,
          mimeType: resource.mimeType,
          text: content
        }
      ]
    };
  }
};
