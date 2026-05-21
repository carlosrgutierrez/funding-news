# Funding News Discord Bot

Daily Discord digest of early-stage startup funding news (pre-seed to Series A). Runs automatically via GitHub Actions and posts via Discord webhook.

## What It Posts

Each company block includes:

- company name and funding round
- founder or CEO LinkedIn contact link
- what the company does
- outreach angle

## How It Works

The agent (`agent/main.py`) runs on a schedule, fetches RSS feeds, classifies articles with an LLM (Groq / llama-3.3-70b), and posts a digest to Discord via webhook.

Schedule: daily at 6:00 AM UTC.

## Secrets Required

Set these in the repo **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `GROQ_API_KEY` | Groq API key |
| `DISCORD_WEBHOOK_URL` | Discord channel webhook URL |

## Local dry run

```bash
cd agent
pip install -r requirements.txt
DRY_RUN=true GROQ_API_KEY=... python main.py
```

## Tuning

Edit `agent/config.json` directly on GitHub to adjust funding range, keywords, and other parameters without touching code.
