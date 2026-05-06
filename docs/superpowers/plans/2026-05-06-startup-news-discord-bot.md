# Startup News Discord Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Node.js/TypeScript bot that collects early-stage startup news, formats a concise Discord post, and posts it through a logged-in Playwright browser on Google Cloud.

**Architecture:** The bot is split into small modules: config, RSS fetching, filtering/extraction, LinkedIn contact fallback, formatting, and Discord Playwright posting. The command entrypoint supports preview, login, and post modes so the VM can be tested before scheduling.

**Tech Stack:** Node.js 20+, TypeScript, tsx, Vitest, Playwright Chromium, fast-xml-parser, dotenv.

---

## File Structure

- `package.json`: npm scripts and dependencies.
- `tsconfig.json`: TypeScript configuration.
- `.gitignore`: ignore dependencies, env files, build output, and Playwright profile.
- `.env.example`: safe example configuration.
- `src/config.ts`: read and validate runtime configuration.
- `src/types.ts`: shared `StartupSignal` and source types.
- `src/sources.ts`: fetch RSS feeds and parse items.
- `src/extract.ts`: identify eligible pre-seed, seed, and Series A startup signals.
- `src/linkedin.ts`: create exact or fallback LinkedIn contact URLs.
- `src/format.ts`: build the Discord message.
- `src/discord.ts`: login helper and Discord posting through Playwright.
- `src/cli.ts`: command entrypoint for `preview`, `post`, and `login`.
- `tests/extract.test.ts`: filtering and extraction tests.
- `tests/linkedin.test.ts`: LinkedIn URL tests.
- `tests/format.test.ts`: Discord message formatting tests.
- `docs/google-cloud-setup.md`: plain-English Google Cloud VM setup and daily scheduler instructions.

---

### Task 1: Project Scaffold

**Files:**
- Create: `package.json`
- Create: `tsconfig.json`
- Create: `.gitignore`
- Create: `.env.example`

- [ ] **Step 1: Create npm and TypeScript configuration**

Create `package.json` with scripts:

```json
{
  "name": "startup-news",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "login": "tsx src/cli.ts login",
    "preview": "tsx src/cli.ts preview",
    "post": "tsx src/cli.ts post",
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "setup": "npm install && npx playwright install --with-deps chromium"
  },
  "dependencies": {
    "dotenv": "^16.4.7",
    "fast-xml-parser": "^4.5.1",
    "playwright": "^1.49.1"
  },
  "devDependencies": {
    "@types/node": "^22.10.2",
    "tsx": "^4.19.2",
    "typescript": "^5.7.2",
    "vitest": "^2.1.8"
  }
}
```

Create `tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "outDir": "dist"
  },
  "include": ["src", "tests"]
}
```

Create `.gitignore`:

```gitignore
node_modules/
dist/
.env
playwright-profile/
test-results/
coverage/
```

Create `.env.example`:

```env
DISCORD_CHANNEL_URL=https://discord.com/channels/1227423505726050386/1365337100861575188
TIMEZONE=America/Costa_Rica
MAX_ITEMS=7
DRY_RUN=true
HEADLESS=true
PLAYWRIGHT_PROFILE_DIR=./playwright-profile
```

- [ ] **Step 2: Install dependencies**

Run: `npm install`

Expected: a `package-lock.json` file is created and install exits with code 0.

- [ ] **Step 3: Commit scaffold**

Run:

```bash
git add package.json package-lock.json tsconfig.json .gitignore .env.example
git commit -m "Add Node TypeScript scaffold"
```

---

### Task 2: Config And Types

**Files:**
- Create: `src/types.ts`
- Create: `src/config.ts`

- [ ] **Step 1: Write shared types**

Create `src/types.ts`:

```ts
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
```

- [ ] **Step 2: Write config reader**

Create `src/config.ts`:

```ts
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
```

- [ ] **Step 3: Typecheck**

Run: `npm run typecheck`

Expected: TypeScript compiles without errors.

---

### Task 3: Extraction And LinkedIn Helpers

**Files:**
- Create: `src/linkedin.ts`
- Create: `src/extract.ts`
- Create: `tests/linkedin.test.ts`
- Create: `tests/extract.test.ts`

- [ ] **Step 1: Write failing tests for LinkedIn fallback URLs**

Create `tests/linkedin.test.ts` with tests for exact URL and search fallback.

- [ ] **Step 2: Implement `src/linkedin.ts`**

Implement `buildContactUrl({ company, founderOrCeo, exactLinkedInUrl })`.

- [ ] **Step 3: Write failing extraction tests**

Create `tests/extract.test.ts` with cases for seed, pre-seed, Series A, rejected Series B, AI prioritization, and useful launch signals with clear stage.

- [ ] **Step 4: Implement `src/extract.ts`**

Implement `extractSignal(item)` and `rankSignals(signals, maxItems)`.

- [ ] **Step 5: Run tests**

Run: `npm run test`

Expected: LinkedIn and extraction tests pass.

---

### Task 4: RSS Sources And Formatting

**Files:**
- Create: `src/sources.ts`
- Create: `src/format.ts`
- Create: `tests/format.test.ts`

- [ ] **Step 1: Implement RSS fetching**

Create `fetchSource(source)` and `fetchAllSources(sources)`.

- [ ] **Step 2: Write formatter tests**

Create tests confirming the message includes header, company blocks, Tim He source review link, no em dashes, and stays under the configured length.

- [ ] **Step 3: Implement formatter**

Create `formatDiscordMessage(signals, config)`.

- [ ] **Step 4: Run tests and typecheck**

Run:

```bash
npm run test
npm run typecheck
```

Expected: both pass.

---

### Task 5: CLI And Discord Playwright

**Files:**
- Create: `src/discord.ts`
- Create: `src/cli.ts`

- [ ] **Step 1: Implement Discord login and post helpers**

Create `openDiscordForLogin(config)` and `postToDiscord(config, message)`.

- [ ] **Step 2: Implement CLI**

Support:

```bash
npm run login
npm run preview
npm run post
```

- [ ] **Step 3: Verify preview mode**

Run: `npm run preview`

Expected: prints a Discord message or a clear "no eligible items found" message.

---

### Task 6: Google Cloud Setup Docs

**Files:**
- Create: `README.md`
- Create: `docs/google-cloud-setup.md`

- [ ] **Step 1: Write README**

Document local commands, what the bot does, and the plain-English setup flow.

- [ ] **Step 2: Write Google Cloud setup guide**

Document installing Node.js, cloning repo, setting `.env`, running `npm run login`, and installing a `systemd` timer.

- [ ] **Step 3: Final validation**

Run:

```bash
npm run test
npm run typecheck
npm run preview
```

Expected: tests and typecheck pass. Preview prints a valid message or a clean skip reason.

- [ ] **Step 4: Commit and push**

Run:

```bash
git add .
git commit -m "Build startup news Discord bot"
git push origin-droz main
```
