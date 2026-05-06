import { getConfig } from "./config.js";
import { postToDiscord, openDiscordForLogin } from "./discord.js";
import { extractSignal, rankSignals } from "./extract.js";
import { formatDiscordMessage } from "./format.js";
import { fetchAllSources } from "./sources.js";
import type { RawNewsItem } from "./types.js";

const RECENT_HOURS = 48;

async function main(): Promise<void> {
  const command = process.argv[2] ?? "preview";
  const config = getConfig();

  if (command === "login") {
    await openDiscordForLogin(config);
    return;
  }

  if (command !== "preview" && command !== "post") {
    throw new Error(`Unknown command "${command}". Use login, preview, or post.`);
  }

  const rawItems = await fetchAllSources(config.sources);
  const recentItems = rawItems.filter(isRecent);
  const signals = rankSignals(
    recentItems.flatMap((item) => {
      const signal = extractSignal(item);
      return signal ? [signal] : [];
    }),
    config.maxItems
  );

  if (signals.length === 0) {
    console.log(`No eligible pre-seed, seed, or Series A startup signals found in the last ${RECENT_HOURS} hours.`);
    return;
  }

  const message = formatDiscordMessage(signals, config);

  if (command === "preview" || config.dryRun) {
    console.log(message);
    if (command === "post" && config.dryRun) {
      console.log("\nDRY_RUN=true, so nothing was posted.");
    }
    return;
  }

  await postToDiscord(config, message);
  console.log("Posted startup intel to Discord.");
}

function isRecent(item: RawNewsItem): boolean {
  if (!item.pubDate) return true;
  const publishedAt = new Date(item.pubDate).getTime();
  if (Number.isNaN(publishedAt)) return true;
  return Date.now() - publishedAt <= RECENT_HOURS * 60 * 60 * 1000;
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`ERROR: ${message}`);
  process.exitCode = 1;
});
