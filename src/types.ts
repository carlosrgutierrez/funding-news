export type SignalType = "funding" | "launch" | "accelerator" | "partnership" | "product" | "growth";
export type Stage = "pre-seed" | "seed" | "series-a";
export type Confidence = "high" | "medium" | "low";

export type FeedSource = {
  name: string;
  url: string;
};

export type RawNewsItem = {
  title: string;
  description: string;
  link: string;
  pubDate?: string;
  sourceName: string;
};

export type StartupSignal = {
  company: string;
  signalType: SignalType;
  stage: Stage;
  amount?: string;
  founderOrCeo?: string;
  contactUrl: string;
  service: string;
  industry?: string;
  sourceUrl: string;
  sourceName: string;
  outreachAngle: string;
  confidence: Confidence;
};
