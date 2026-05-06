import type { AppConfig } from "./config.js";
import type { StartupSignal } from "./types.js";

const PREFERRED_MAX_LENGTH = 1800;

export function formatDiscordMessage(signals: StartupSignal[], config: AppConfig, now = new Date()): string {
  const header = [
    `Early-stage startup intel for ${formatDate(now, config.timezone)}`,
    "",
    "Pre-seed, seed, and Series A. AI prioritized.",
    ""
  ];

  const footer = ["", "Source review:", `Tim He: ${config.timHeUrl}`];
  const blocks: string[] = [];

  for (const signal of signals.slice(0, config.maxItems)) {
    const nextBlock = formatSignalBlock(signal);
    const candidate = [...header, ...blocks, nextBlock, ...footer].join("\n");
    if (candidate.length > PREFERRED_MAX_LENGTH) break;
    blocks.push(nextBlock);
  }

  return [...header, ...blocks, ...footer].join("\n").replace(/—/g, "-");
}

function formatSignalBlock(signal: StartupSignal): string {
  const contactName = signal.founderOrCeo ? `${signal.founderOrCeo}, Founder/CEO` : "Founder/CEO contact search";
  return [
    signal.company,
    formatSignalLine(signal),
    contactName,
    signal.contactUrl,
    signal.service,
    `Angle: ${signal.outreachAngle}`
  ].join("\n");
}

function formatSignalLine(signal: StartupSignal): string {
  if (signal.signalType === "funding" && signal.amount) {
    return `${signal.amount} ${formatStage(signal.stage)}`;
  }

  return `${formatSignalType(signal.signalType)} (${formatStage(signal.stage)})`;
}

function formatSignalType(type: StartupSignal["signalType"]): string {
  const labels: Record<StartupSignal["signalType"], string> = {
    funding: "Funding",
    launch: "Launch signal",
    accelerator: "Accelerator signal",
    partnership: "Partnership signal",
    product: "Product signal",
    growth: "Growth signal"
  };

  return labels[type];
}

function formatStage(stage: StartupSignal["stage"]): string {
  const labels: Record<StartupSignal["stage"], string> = {
    "pre-seed": "Pre-seed",
    seed: "Seed",
    "series-a": "Series A"
  };

  return labels[stage];
}

function formatDate(date: Date, timezone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    timeZone: timezone
  }).format(date);
}
