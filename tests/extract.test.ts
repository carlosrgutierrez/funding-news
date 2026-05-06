import { describe, expect, test } from "vitest";
import { extractSignal, rankSignals } from "../src/extract.js";
import type { RawNewsItem, StartupSignal } from "../src/types.js";

function item(title: string, description: string): RawNewsItem {
  return {
    title,
    description,
    link: "https://example.com/article",
    pubDate: new Date().toUTCString(),
    sourceName: "Example Feed"
  };
}

describe("extractSignal", () => {
  test("extracts a seed funding signal", () => {
    const signal = extractSignal(
      item(
        "Acme AI raises $4M seed round",
        "Acme AI builds AI agents for finance teams. Founder Jane Founder said the funding will support early customers."
      )
    );

    expect(signal).toMatchObject({
      company: "Acme AI",
      signalType: "funding",
      stage: "seed",
      amount: "$4M",
      founderOrCeo: "Jane Founder",
      industry: "AI",
      sourceUrl: "https://example.com/article"
    });
    expect(signal?.contactUrl).toContain("Jane%20Founder%20Acme%20AI");
  });

  test("extracts pre-seed funding", () => {
    const signal = extractSignal(item("BrightOps closes $1.5M pre-seed", "BrightOps helps logistics teams manage warehouse workflows."));

    expect(signal?.stage).toBe("pre-seed");
    expect(signal?.amount).toBe("$1.5M");
  });

  test("extracts Series A funding", () => {
    const signal = extractSignal(item("CareFlow secures $12M Series A", "CareFlow automates patient intake for clinics."));

    expect(signal?.stage).toBe("series-a");
    expect(signal?.amount).toBe("$12M");
  });

  test("rejects Series B funding", () => {
    const signal = extractSignal(item("ScaleBase raises $40M Series B", "ScaleBase builds sales software."));

    expect(signal).toBeNull();
  });

  test("keeps useful launch signals only when stage is clear", () => {
    const signal = extractSignal(item("Nova launches AI analyst after seed round", "Nova helps operators turn spreadsheets into forecasts."));

    expect(signal).toMatchObject({
      company: "Nova",
      signalType: "launch",
      stage: "seed"
    });
  });

  test("rejects generic trend articles", () => {
    const signal = extractSignal(item("Why AI agents are changing work", "Investors are debating the future of automation."));

    expect(signal).toBeNull();
  });
});

describe("rankSignals", () => {
  test("prioritizes AI signals first and respects max items", () => {
    const signals: StartupSignal[] = [
      signal("LogiCo", "seed", "Logistics"),
      signal("AgentOps", "seed", "AI"),
      signal("HealthDesk", "series-a", "Healthcare")
    ];

    const ranked = rankSignals(signals, 2);

    expect(ranked.map((entry) => entry.company)).toEqual(["AgentOps", "HealthDesk"]);
  });
});

function signal(company: string, stage: StartupSignal["stage"], industry: string): StartupSignal {
  return {
    company,
    signalType: "funding",
    stage,
    amount: "$4M",
    contactUrl: "https://www.linkedin.com/search/results/people/?keywords=test",
    service: "Builds useful software",
    industry,
    sourceUrl: "https://example.com",
    sourceName: "Example",
    outreachAngle: "Seed-stage teams usually need customer proof, early distribution, and sharper ICP clarity",
    confidence: "medium"
  };
}
