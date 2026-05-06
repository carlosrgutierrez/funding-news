import { XMLParser } from "fast-xml-parser";
import type { FeedSource, RawNewsItem } from "./types.js";

const parser = new XMLParser({
  ignoreAttributes: false,
  cdataPropName: "cdata"
});

type RssItem = {
  title?: string;
  description?: string;
  link?: string;
  pubDate?: string;
};

type RssDocument = {
  rss?: {
    channel?: {
      item?: RssItem | RssItem[];
    };
  };
};

export async function fetchSource(source: FeedSource): Promise<RawNewsItem[]> {
  const response = await fetch(source.url, {
    headers: {
      "User-Agent": "startup-news-discord-bot/0.1"
    }
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch ${source.name}: HTTP ${response.status}`);
  }

  return parseRss(await response.text(), source.name);
}

export async function fetchAllSources(sources: FeedSource[]): Promise<RawNewsItem[]> {
  const settled = await Promise.allSettled(sources.map((source) => fetchSource(source)));
  return settled.flatMap((result) => (result.status === "fulfilled" ? result.value : []));
}

export function parseRss(xml: string, sourceName: string): RawNewsItem[] {
  const parsed = parser.parse(xml) as RssDocument;
  const rawItems = parsed.rss?.channel?.item;
  const items = Array.isArray(rawItems) ? rawItems : rawItems ? [rawItems] : [];

  return items
    .filter((item) => item.title && item.link)
    .map((item) => ({
      title: String(item.title),
      description: String(item.description ?? ""),
      link: String(item.link),
      pubDate: item.pubDate ? String(item.pubDate) : undefined,
      sourceName
    }));
}
