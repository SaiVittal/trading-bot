import * as fs from "fs/promises";
import * as path from "path";
import { fileURLToPath } from "url";
import { ENV_KEYS, PATH_DEFAULTS } from "../utils/constants.js";
import { logger } from "../utils/logger.js";

export class DocService {
  private docsDir: string;

  constructor() {
    // 1. Attempt to load from Environment Variable
    let derivedPath = process.env[ENV_KEYS.DOCS_DIR];

    if (derivedPath) {
      logger.info(`Docs directory loaded from environment variable: ${derivedPath}`);
    } else {
      // 2. Fallback 1: Check if running from repository root where "docs" exists
      const cwdDocs = path.join(process.cwd(), PATH_DEFAULTS.DOCS_DIR_NAME);
      
      // 3. Fallback 2: Calculate path relative to compile output
      const __filename = fileURLToPath(import.meta.url);
      const __dirname = path.dirname(__filename);
      // build/services/docService.js is 4 levels deep from workspace root
      const compiledDocs = path.resolve(__dirname, "..", "..", "..", "..", PATH_DEFAULTS.DOCS_DIR_NAME);

      // Select compile fallback or cwd if it matches
      derivedPath = compiledDocs;
      logger.debug(`Environment variable not set. Deriving relative path: ${derivedPath}`);
    }

    this.docsDir = derivedPath;
  }

  /**
   * Sets custom docs directory path dynamically (e.g. for testing)
   */
  public setDocsDirectory(customPath: string): void {
    logger.warn(`Manually overriding docs directory path to: ${customPath}`);
    this.docsDir = customPath;
  }

  /**
   * Get the absolute path for a doc filename safely
   */
  private getFilePath(filename: string): string {
    const cleanFilename = path.basename(filename); // Prevent directory traversal attacks
    const resolvedPath = path.join(this.docsDir, cleanFilename);
    logger.debug(`Resolving filepath for ${filename} -> ${resolvedPath}`);
    return resolvedPath;
  }

  /**
   * Read the raw content of a markdown document
   */
  public async readDoc(filename: string): Promise<string> {
    const filePath = this.getFilePath(filename);
    try {
      logger.info(`Reading documentation file: ${filename}`);
      return await fs.readFile(filePath, "utf-8");
    } catch (error) {
      logger.error(`Failed to read documentation file ${filename}: ${(error as Error).message}`);
      throw new Error(`Failed to read documentation file ${filename}: ${(error as Error).message}`);
    }
  }

  /**
   * Lists all files in the docs directory
   */
  public async listDocs(): Promise<string[]> {
    try {
      logger.info(`Scanning docs directory: ${this.docsDir}`);
      const files = await fs.readdir(this.docsDir);
      const filtered = files.filter(f => f.endsWith(".md"));
      logger.debug(`Found ${filtered.length} markdown documents`);
      return filtered;
    } catch (error) {
      logger.error(`Failed to list documentation directory: ${(error as Error).message}`);
      throw new Error(`Failed to list documentation directory: ${(error as Error).message}`);
    }
  }
}

export const docService = new DocService();
