import { ENV_KEYS } from "./constants.js";

export type LogLevel = "DEBUG" | "INFO" | "WARN" | "ERROR";

const LOG_LEVEL_VALUES: Record<LogLevel, number> = {
  DEBUG: 0,
  INFO: 1,
  WARN: 2,
  ERROR: 3,
};

class StderrLogger {
  private getSelectedLevel(): number {
    const envLevel = (process.env[ENV_KEYS.LOG_LEVEL] || "INFO").toUpperCase() as LogLevel;
    return LOG_LEVEL_VALUES[envLevel] !== undefined ? LOG_LEVEL_VALUES[envLevel] : 1;
  }

  private formatMessage(level: LogLevel, message: string, ...args: any[]): string {
    const timestamp = new Date().toISOString();
    const formattedArgs = args.length > 0 ? ` ${args.map(a => typeof a === "object" ? JSON.stringify(a) : a).join(" ")}` : "";
    return `[${timestamp}] [${level}] ${message}${formattedArgs}`;
  }

  public debug(message: string, ...args: any[]): void {
    if (this.getSelectedLevel() <= LOG_LEVEL_VALUES.DEBUG) {
      console.error(this.formatMessage("DEBUG", message, ...args));
    }
  }

  public info(message: string, ...args: any[]): void {
    if (this.getSelectedLevel() <= LOG_LEVEL_VALUES.INFO) {
      console.error(this.formatMessage("INFO", message, ...args));
    }
  }

  public warn(message: string, ...args: any[]): void {
    if (this.getSelectedLevel() <= LOG_LEVEL_VALUES.WARN) {
      console.error(this.formatMessage("WARN", message, ...args));
    }
  }

  public error(message: string, ...args: any[]): void {
    if (this.getSelectedLevel() <= LOG_LEVEL_VALUES.ERROR) {
      console.error(this.formatMessage("ERROR", message, ...args));
    }
  }
}

export const logger = new StderrLogger();
