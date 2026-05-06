type ContactInput = {
  company: string;
  founderOrCeo?: string;
  exactLinkedInUrl?: string;
};

function searchUrl(query: string): string {
  return `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(query)}`;
}

export function buildContactUrl(input: ContactInput): string {
  if (input.exactLinkedInUrl?.startsWith("https://www.linkedin.com/in/")) {
    return input.exactLinkedInUrl;
  }

  if (input.founderOrCeo) {
    return searchUrl(`${input.founderOrCeo} ${input.company}`);
  }

  return searchUrl(`${input.company} founder CEO`);
}
