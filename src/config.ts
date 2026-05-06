import "dotenv/config";
import path from "node:path";
import type { FeedSource } from "./types.js";

export type AppConfig = {
  discordChannelUrl: string;
  timezone: string;
  maxItems: number;
  dryRun: boolean;
  headless: boolean;
  profileDir: string;
  sources: FeedSource[];
  timHeUrl: string;
};

const DEFAULT_SOURCES: FeedSource[] = [
  { name: "TechCrunch", url: "https://techcrunch.com/feed/" },
  { name: "TechCrunch Startups", url: "https://techcrunch.com/category/startups/feed/" }
];

function parseBoolean(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

function parsePositiveInteger(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 1) return fallback;
  return parsed;
}

export function getConfig(): AppConfig {
  return {
    discordChannelUrl: process.env.DISCORD_CHANNEL_URL ?? "",
    timezone: process.env.TIMEZONE ?? "America/Costa_Rica",
    maxItems: parsePositiveInteger(process.env.MAX_ITEMS, 7),
    dryRun: parseBoolean(process.env.DRY_RUN, false),
    headless: parseBoolean(process.env.HEADLESS, true),
    profileDir: path.resolve(process.env.PLAYWRIGHT_PROFILE_DIR ?? "./playwright-profile"),
    sources: DEFAULT_SOURCES,
    timHeUrl: "https://www.linkedin.com/in/timhe2000/"
  };
}
