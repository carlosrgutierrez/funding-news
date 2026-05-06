import { describe, expect, test } from "vitest";
import { formatDiscordMessage } from "../src/format.js";
import type { AppConfig } from "../src/config.js";
import type { StartupSignal } from "../src/types.js";

const config: AppConfig = {
  discordChannelUrl: "https://discord.com/channels/1/2",
  timezone: "America/Costa_Rica",
  maxItems: 7,
  dryRun: true,
  headless: true,
  profileDir: "/tmp/profile",
  sources: [],
  timHeUrl: "https://www.linkedin.com/in/timhe2000/"
};

describe("formatDiscordMessage", () => {
  test("formats startup signals in the approved compact structure", () => {
    const message = formatDiscordMessage([signal("Acme AI", "seed", "$4M")], config, new Date("2026-05-06T12:00:00Z"));

    expect(message).toContain("Early-stage startup intel for May 6");
    expect(message).toContain("Pre-seed, seed, and Series A. AI prioritized.");
    expect(message).toContain("Acme AI");
    expect(message).toContain("$4M Seed");
    expect(message).toContain("Jane Founder, Founder/CEO");
    expect(message).toContain("https://www.linkedin.com/search/results/people/?keywords=Jane%20Founder%20Acme%20AI");
    expect(message).toContain("Angle: Seed-stage teams usually need customer proof");
    expect(message).toContain("Tim He: https://www.linkedin.com/in/timhe2000/");
  });

  test("does not include em dashes", () => {
    const message = formatDiscordMessage([signal("Acme AI", "seed", "$4M")], config, new Date("2026-05-06T12:00:00Z"));

    expect(message).not.toContain("—");
  });

  test("keeps the message below the preferred Discord length", () => {
    const signals = Array.from({ length: 10 }, (_, index) => signal(`Company ${index}`, "series-a", "$9M"));

    const message = formatDiscordMessage(signals, { ...config, maxItems: 7 }, new Date("2026-05-06T12:00:00Z"));

    expect(message.length).toBeLessThanOrEqual(1800);
  });
});

function signal(company: string, stage: StartupSignal["stage"], amount: string): StartupSignal {
  return {
    company,
    signalType: "funding",
    stage,
    amount,
    founderOrCeo: "Jane Founder",
    contactUrl: `https://www.linkedin.com/search/results/people/?keywords=Jane%20Founder%20${encodeURIComponent(company)}`,
    service: "Builds AI software for finance teams",
    industry: "AI",
    sourceUrl: "https://example.com",
    sourceName: "Example",
    outreachAngle: "Seed-stage teams usually need customer proof, early distribution, and sharper ICP clarity",
    confidence: "medium"
  };
}
