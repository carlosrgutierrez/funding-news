# Startup News Discord Bot

This is a small robot that checks startup news and posts a short Discord update.

It focuses only on:

- pre-seed
- seed
- Series A

It prioritizes AI startups, but it can include other early-stage startup news when the news is useful for outreach.

## What It Posts

Each company block includes:

- company name
- funding round or useful signal
- founder or CEO LinkedIn contact link
- what the company does
- outreach angle

The bot posts through Playwright. That means it opens Discord in a browser and types into the channel like a person. It does not use a Discord webhook.

## Commands

Install dependencies:

```bash
npm install
```

Install Playwright Chromium:

```bash
npx playwright install --with-deps chromium
```

Preview the message without posting:

```bash
npm run preview
```

Open Discord so Carlos can log in once:

```bash
npm run login
```

Post to Discord:

```bash
npm run post
```

Run tests:

```bash
npm run test
```

Run TypeScript checks:

```bash
npm run typecheck
```

## Configuration

Create a `.env` file from `.env.example`:

```bash
cp .env.example .env
```

Important values:

```env
DISCORD_CHANNEL_URL=https://discord.com/channels/1227423505726050386/1365337100861575188
TIMEZONE=America/Costa_Rica
MAX_ITEMS=7
DRY_RUN=true
HEADLESS=true
PLAYWRIGHT_PROFILE_DIR=./playwright-profile
```

Use `DRY_RUN=true` while testing. Change it to `false` only when the Discord login works and you are ready to post.

## Plain-English Setup Flow

1. Put this code on a Google Cloud VM.
2. Install Node.js and Playwright.
3. Run `npm run login`.
4. Log into Discord in the browser that opens.
5. Run `npm run preview` to check the message.
6. Set `DRY_RUN=false`.
7. Run `npm run post`.
8. Add the daily scheduler.

See [docs/google-cloud-setup.md](docs/google-cloud-setup.md) for the Google Cloud setup.
