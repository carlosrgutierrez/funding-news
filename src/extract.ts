import { buildContactUrl } from "./linkedin.js";
import type { RawNewsItem, SignalType, Stage, StartupSignal } from "./types.js";

const LATE_STAGE_PATTERN = /\b(series\s+[b-z]|ipo|acquir|acquisition|public company)\b/i;
const FUNDING_PATTERN = /\b(raises?|raised|funding|secures?|secured|closes?|closed)\b|\$\s?\d/i;
const USEFUL_SIGNAL_PATTERNS: Array<[SignalType, RegExp]> = [
  ["launch", /\b(launches?|launched|debuts?|unveils?)\b/i],
  ["accelerator", /\b(accelerator|demo day|y combinator|yc)\b/i],
  ["partnership", /\b(partners?|partnership|teams up)\b/i],
  ["product", /\b(new product|product|platform)\b/i],
  ["growth", /\b(hiring|expands?|growth)\b/i]
];

export function extractSignal(item: RawNewsItem): StartupSignal | null {
  const text = cleanText(`${item.title}. ${item.description}`);
  if (LATE_STAGE_PATTERN.test(text)) return null;

  const stage = extractStage(text);
  if (!stage) return null;

  const signalType = extractSignalType(text);
  if (!signalType) return null;

  const company = extractCompany(item.title);
  if (!company) return null;

  const founderOrCeo = extractFounderOrCeo(text);
  const industry = detectIndustry(text);
  const service = extractService(item.description, company);

  return {
    company,
    signalType,
    stage,
    amount: signalType === "funding" ? extractAmount(text) : undefined,
    founderOrCeo,
    contactUrl: buildContactUrl({ company, founderOrCeo }),
    service,
    industry,
    sourceUrl: item.link,
    sourceName: item.sourceName,
    outreachAngle: buildOutreachAngle(stage, industry, signalType),
    confidence: founderOrCeo ? "medium" : "low"
  };
}

export function rankSignals(signals: StartupSignal[], maxItems: number): StartupSignal[] {
  return [...signals]
    .sort((left, right) => score(right) - score(left) || left.company.localeCompare(right.company))
    .slice(0, maxItems);
}

function score(signal: StartupSignal): number {
  let total = 0;
  if (signal.industry?.toLowerCase() === "ai") total += 100;
  if (signal.stage === "series-a") total += 30;
  if (signal.stage === "seed") total += 20;
  if (signal.stage === "pre-seed") total += 10;
  if (signal.signalType === "funding") total += 5;
  return total;
}

function extractStage(text: string): Stage | null {
  if (/\bpre[-\s]?seed\b/i.test(text)) return "pre-seed";
  if (/\bseries\s+a\b/i.test(text)) return "series-a";
  if (/\bseed\b/i.test(text)) return "seed";
  return null;
}

function extractSignalType(text: string): SignalType | null {
  if (FUNDING_PATTERN.test(text)) return "funding";
  for (const [type, pattern] of USEFUL_SIGNAL_PATTERNS) {
    if (pattern.test(text)) return type;
  }
  return null;
}

function extractAmount(text: string): string | undefined {
  const match = text.match(/\$\s?\d+(?:\.\d+)?\s?(?:M|B|million|billion)?/i);
  if (!match) return undefined;
  return match[0].replace(/\s+/g, "").replace(/million/i, "M").replace(/billion/i, "B");
}

function extractFounderOrCeo(text: string): string | undefined {
  const founderMatch = text.match(/\bFounder\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b/);
  if (founderMatch?.[1]) return founderMatch[1];

  const ceoMatch = text.match(/\bCEO\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b/);
  if (ceoMatch?.[1]) return ceoMatch[1];

  return undefined;
}

function detectIndustry(text: string): string | undefined {
  if (/\b(ai|artificial intelligence|llm|agent|agents)\b/i.test(text)) return "AI";
  if (/\b(health|clinic|patient|provider|medical)\b/i.test(text)) return "Healthcare";
  if (/\b(finance|fintech|bank|payments?)\b/i.test(text)) return "Fintech";
  if (/\b(logistics|warehouse|supply chain)\b/i.test(text)) return "Logistics";
  return undefined;
}

function extractService(description: string, company: string): string {
  const cleaned = cleanText(description);
  const firstSentence = cleaned.split(/(?<=[.!?])\s+/)[0] ?? "";
  if (firstSentence.length >= 20) return trimLength(firstSentence, 120);
  return `${company} is an early-stage startup with a recent outreach-relevant signal`;
}

function extractCompany(title: string): string | null {
  const cleaned = cleanText(title);
  const beforeVerb = cleaned.split(/\s+(raises?|raised|secures?|secured|closes?|closed|launches?|launched|debuts?|unveils?|partners?|expands?)\b/i)[0];
  const candidate = beforeVerb.replace(/^[^A-Za-z0-9]+/, "").trim();
  if (!candidate || candidate.length > 60) return null;
  return candidate;
}

function buildOutreachAngle(stage: Stage, industry: string | undefined, signalType: SignalType): string {
  if (stage === "pre-seed") {
    return "Pre-seed teams usually need sharper customer proof, early design partners, and a clear first market";
  }

  if (stage === "seed") {
    return "Seed-stage teams usually need customer proof, early distribution, and sharper ICP clarity";
  }

  if (industry === "AI") {
    return "Series A AI teams usually need repeatable distribution and enterprise pipeline, not another model story";
  }

  if (signalType === "launch") {
    return "Series A launch moments usually need fast feedback loops, repeatable demand, and clearer positioning";
  }

  return "Series A teams usually need repeatable sales, stronger hiring, and cleaner positioning";
}

function cleanText(value: string): string {
  return value
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#8217;/g, "'")
    .replace(/&#8220;|&#8221;/g, "\"")
    .replace(/\s+/g, " ")
    .trim();
}

function trimLength(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 3).trim()}...`;
}
