import { describe, expect, test } from "vitest";
import { buildContactUrl } from "../src/linkedin.js";

describe("buildContactUrl", () => {
  test("uses an exact LinkedIn profile when one is provided", () => {
    const url = buildContactUrl({
      company: "Acme AI",
      founderOrCeo: "Jane Founder",
      exactLinkedInUrl: "https://www.linkedin.com/in/jane-founder/"
    });

    expect(url).toBe("https://www.linkedin.com/in/jane-founder/");
  });

  test("builds a founder and company LinkedIn search when the name is known", () => {
    const url = buildContactUrl({
      company: "Acme AI",
      founderOrCeo: "Jane Founder"
    });

    expect(url).toBe("https://www.linkedin.com/search/results/people/?keywords=Jane%20Founder%20Acme%20AI");
  });

  test("builds a company founder CEO search when the name is missing", () => {
    const url = buildContactUrl({
      company: "Acme AI"
    });

    expect(url).toBe("https://www.linkedin.com/search/results/people/?keywords=Acme%20AI%20founder%20CEO");
  });
});
