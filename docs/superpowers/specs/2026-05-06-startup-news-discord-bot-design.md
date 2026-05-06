# Startup News Discord Bot Design

## Plain-English Summary

This project is a small robot that checks startup news once per day and posts a short report in Discord.

It looks only for very early companies:

- pre-seed
- seed
- Series A

It cares most about AI startups, but it can include other early startups if the news is useful for outreach.

The robot will run on an Oracle Cloud VM. A VM is just a computer in the cloud. Playwright will open Discord on that cloud computer, like a person opening Discord in a browser, and post the message into the channel.

The code will start in `https://github.com/drozrzd/startup-news`. Later, the repo can be transferred to Carlos.

## Goal

Build a standalone GitHub project that runs on an Oracle Cloud VM and posts a daily Discord update through Playwright. The bot should help Carlos track useful early-stage startup news for outreach, with AI companies prioritized.

Current build repo:

`https://github.com/drozrzd/startup-news`

Future handoff repo:

`https://github.com/carlosrgutierrez/startup-news`

The Discord channel is:

`https://discord.com/channels/1227423505726050386/1365337100861575188`

The bot must not use a Discord webhook. Carlos is not an admin or owner of the channel, so posting happens by opening Discord in Playwright with a logged-in browser profile.

Plain English: Carlos logs into Discord one time on the VM. After that, the bot reuses that login to post.

## Scope

The bot reports only companies that are clearly in one of these stages:

- pre-seed
- seed
- Series A

Series B, Series C, late-stage, IPO, acquisition-only, and big-company news are out of scope.

The bot includes funding announcements and other outreach-useful news when the company stage is clear:

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

Version 1 starts with RSS/news sources for reliability. RSS is a simple news feed that websites publish so software can read their latest articles.

- `https://techcrunch.com/feed/`
- `https://techcrunch.com/category/startups/feed/`

The source list should be configurable so more RSS feeds can be added later without changing the core logic.

Tim He's LinkedIn profile is included as a review/reference link in the final post:

`https://www.linkedin.com/in/timhe2000/`

Version 1 does not log into LinkedIn or scrape LinkedIn directly. LinkedIn automation is brittle and can trigger anti-bot checks.

Plain English: the bot can link to LinkedIn searches and exact public profiles when it is confident, but it will not pretend to be Carlos on LinkedIn.

## Architecture

The project is a standalone Node.js TypeScript repo.

Primary modules:

- `src/sources/`: fetch and parse RSS/news feeds.
- `src/extract/`: normalize feed items into structured startup signals.
- `src/linkedin/`: resolve contact URLs. Use exact LinkedIn profile URLs when confidently found, otherwise fallback to LinkedIn people search URLs.
- `src/format/`: build the Discord message.
- `src/discord/`: use Playwright to post into Discord with a persistent browser profile.
- `scripts/`: setup helpers and Oracle Cloud VM scheduling docs.

Daily flow in plain English:

1. Read recent startup news.
2. Keep only pre-seed, seed, and Series A companies.
3. Keep funding news and useful outreach news.
4. Put AI companies first when there are too many items.
5. Pull out the company name, stage, founder or CEO, contact link, what they do, industry, source link, and outreach angle.
6. Build one short Discord message.
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

Example format:

```text
Early-stage startup intel for May 6

Pre-seed, seed, and Series A. AI prioritized.

Company
$4M Seed
Founder Name, Founder
Contact URL
Plain-language service description
Angle: Seed-stage teams usually need customer proof, early distribution, and sharper ICP clarity

Company
$9M Series A
CEO Name, CEO
Contact URL
Plain-language service description
Angle: Series A teams usually need repeatable sales, stronger hiring, and cleaner positioning

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

Plain English: Oracle Cloud gives us the cloud computer. Ubuntu is the basic software installed on that cloud computer. Node.js runs the bot. Playwright opens the browser.

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

Plain English: Carlos does not need to keep a terminal open. The VM should run the job in the background every weekday.

## Verification

Before posting, the bot should verify:

- At least one eligible item was found.
- The formatted message is non-empty.
- The formatted message is below the configured length limit.
- The Discord channel URL is configured.
- Playwright can see the Discord message box.

After posting, the bot should verify that the posted message appears in the channel.

## Approval Checklist For A Non-Technical Founder

Approve this design if these statements are true:

- The bot should post into Discord by using a logged-in browser, not a webhook.
- The bot should run on an Oracle Cloud VM.
- The bot should only cover pre-seed, seed, and Series A startups.
- The bot should prioritize AI startups, but can include other early startups when the news is useful for outreach.
- Each item should include the company, stage or signal, founder or CEO contact link, what the company does, and an outreach angle.
- The first version does not need a dashboard, database, Discord bot token, or LinkedIn login.

Do not approve yet if any of those statements are wrong.

## Risks

Discord browser automation can break if Discord changes its UI or asks for verification. A persistent Playwright profile reduces repeated login friction, but the VM may still need occasional manual login or security confirmation.

RSS descriptions may not include founder or CEO names. The bot should degrade gracefully by using company-based LinkedIn search URLs instead of guessing exact profiles.

Non-funding signals can be ambiguous. Version 1 only includes them when pre-seed, seed, or Series A stage is clear from the source context.

## If Something Breaks

Most likely issues:

- Discord asks Carlos to log in again.
- Discord changes the message box layout.
- The VM is turned off or has no internet.
- The news sources do not publish enough eligible early-stage items that day.

Expected response:

- If Discord asks for login, Carlos logs in again on the VM.
- If Discord layout changes, the Playwright selector needs a small code update.
- If the VM is down, restart the VM and check the scheduled job logs.
- If there are no good articles, the bot should skip posting instead of posting bad filler.

## Out Of Scope For Version 1

- Discord webhook posting.
- Discord bot token integration.
- LinkedIn login automation.
- Late-stage funding coverage.
- A web dashboard.
- Database storage.
- Multi-channel posting.
