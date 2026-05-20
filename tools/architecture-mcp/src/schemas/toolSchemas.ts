import { z } from "zod";

// Schema for get_event_schema tool arguments
export const GetEventSchemaArgs = z.object({
  eventType: z.string().optional().describe("Specific event type to retrieve schema for (e.g., 'tick_received', 'candle_closed', 'signal_generated')")
});

// Schema for get_engineering_standards tool arguments
export const GetEngineeringStandardsArgs = z.object({
  category: z.enum(["all", "backend", "frontend", "git"]).default("all").describe("Filter standards by category")
});

// Empty schema for tools that do not require arguments
export const EmptyArgsSchema = z.object({});
