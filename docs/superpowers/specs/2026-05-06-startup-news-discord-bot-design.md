# Startup News Discord Bot Design

## Goal

Build a standalone GitHub project for `carlosrgutierrez/startup-news` that runs on an Oracle Cloud VM and posts a daily Discord update through Playwright. The bot should help Carlos track useful early-stage startup signals for outreach, with AI companies prioritized.

The Discord channel is:

`https://discord.com/channels/1227423505726050386/1365337100861575188`

The bot must not use a Discord webhook. Carlos is not an admin or owner of the channel, so posting happens by automating Discord in a logged-in Playwright browser profile.

## Scope

The bot reports only companies that are clearly in one of these stages:

- pre-seed
- seed
- Series A

Series B, Series C, late-stage, IPO, acquisition-only, and big-company news are out of scope.

The bot includes funding announcements and other outreach-useful signals when the company stage is clear:

- launch
- accelerator or demo day
- major partnership
- new product
- hiring or growth announcement

The bot excludes low-value noise:

- generic AI trend articles
- opinion pieces
- layoffs
- lawsuits
- executive drama
- pure product reviews

## Sources

Version 1 starts with RSS/news sources for reliability:

- `https://techcrunch.com/feed/`
- `https://techcrunch.com/category/startups/feed/`

The source list should be configurable so more RSS feeds can be added later without changing the core logic.

Tim He's LinkedIn profile is included as a review/reference source in the final post:

`https://www.linkedin.com/in/timhe2000/`

Version 1 does not automate LinkedIn login or scrape LinkedIn directly. LinkedIn automation is brittle and can trigger anti-bot checks.

## Architecture

The project is a standalone Node.js TypeScript repo.

Primary modules:

- `src/sources/`: fetch and parse RSS/news feeds.
- `src/extract/`: normalize feed items into structured startup signals.
- `src/linkedin/`: resolve contact URLs. Use exact LinkedIn profile URLs when confidently found, otherwise fallback to LinkedIn people search URLs.
- `src/format/`: build the Discord message.
- `src/discord/`: use Playwright to post into Discord with a persistent browser profile.
- `scripts/`: setup helpers and Oracle VM scheduling docs.

Daily flow:

1. Fetch recent RSS/news items.
2. Filter to pre-seed, seed, and Series A companies.
3. Keep funding items and outreach-useful non-funding signals.
4. Prioritize AI companies when more eligible items exist than the daily cap.
5. Normalize company, stage, founder/CEO, contact link, service, industry, source URL, and outreach angle.
6. Format one compact Discord post.
7. Open Discord through Playwright and post the message.

## Data Model

Normalized items use this shape:

```ts
type StartupSignal = {
  company: string;
  signalType: "funding" | "launch" | "accelerator" | "partnership" | "product" | "growth";
  stage: "pre-seed" | "seed" | "series-a";
  amount?: string;
  founderOrCeo?: string;
  contactUrl: string;
  service: string;
  industry?: string;
  sourceUrl: string;
  sourceName: string;
  outreachAngle: string;
  confidence: "high" | "medium" | "low";
};
```

LinkedIn contact rules:

- If a reliable exact LinkedIn profile URL is found, use it.
- If the founder or CEO name is known but exact profile is not reliable, use:
  `https://www.linkedin.com/search/results/people/?keywords=<name company>`
- If the founder or CEO name is missing, use:
  `https://www.linkedin.com/search/results/people/?keywords=<company founder CEO>`

## Discord Output

The bot posts one compact message in a TechWeek-style format adapted for news.

Example:

```text
Early-stage startup intel for May 6

Pre-seed, seed, and Series A. AI prioritized.

Exa
$17M Series A
Will Bryk, Founder
https://www.linkedin.com/search/results/people/?keywords=Will%20Bryk%20Exa
Search API for AI apps that need web-scale retrieval
Angle: Series A AI infrastructure usually means converting developer interest into repeatable enterprise pipeline

Company
$4M Seed
Founder Name, Founder
Contact URL
Plain-language service description
Angle: Seed-stage teams usually need customer proof, early distribution, and sharper ICP clarity

Source review:
Tim He: https://www.linkedin.com/in/timhe2000/
```

Rules:

- One Discord message by default.
- Target 5 to 7 companies.
- Keep the message under Discord limits, preferably under 1800 characters.
- No em dashes.
- No hype or flattery.
- Plain-language service descriptions.
- Every company gets an outreach angle.
- Exact LinkedIn profile when confident, fallback search URL otherwise.

For non-funding signals, the second line describes the signal instead of amount and round:

```text
Company
Launched AI hiring assistant
Founder Name, Founder
Contact URL
Service description
Angle: Seed-stage teams usually need customer proof, early distribution, and sharper ICP clarity
```

## Runtime And Operations

The bot runs on an Oracle Cloud VM. Ubuntu is the recommended operating system inside the VM because it is straightforward for Node.js, Playwright, and background scheduling.

Expected commands:

- `npm run setup`: install dependencies and Playwright browser dependencies.
- `npm run login`: open Discord in a persistent Playwright profile so Carlos can log in once.
- `npm run preview`: scrape and print the Discord message without posting.
- `npm run post`: scrape, format, open Discord, and post.
- `npm run test`: run unit tests for filtering, formatting, and LinkedIn fallback behavior.

Configuration lives in `.env`:

```env
DISCORD_CHANNEL_URL=https://discord.com/channels/1227423505726050386/1365337100861575188
TIMEZONE=America/Costa_Rica
MAX_ITEMS=7
DRY_RUN=false
```

Scheduling:

- Use a `systemd` service to run `npm run post`.
- Use a `systemd` timer to run the service weekdays around 5:00 AM Costa Rica time.
- Logs should be inspectable with `journalctl`.
- The VM must stay online and have network access.

## Verification

Before posting, the bot should verify:

- At least one eligible item was found.
- The formatted message is non-empty.
- The formatted message is below the configured length limit.
- The Discord channel URL is configured.
- Playwright can see the Discord message box.

After posting, the bot should verify that the posted message appears in the channel.

## Risks

Discord browser automation can break if Discord changes its UI or asks for verification. A persistent Playwright profile reduces repeated login friction, but the VM may still need occasional manual login or security confirmation.

RSS descriptions may not include founder or CEO names. The bot should degrade gracefully by using company-based LinkedIn search URLs instead of guessing exact profiles.

Non-funding signals can be ambiguous. Version 1 only includes them when pre-seed, seed, or Series A stage is clear from the source context.

## Out Of Scope For Version 1

- Discord webhook posting.
- Discord bot token integration.
- LinkedIn login automation.
- Late-stage funding coverage.
- A web dashboard.
- Database storage.
- Multi-channel posting.
