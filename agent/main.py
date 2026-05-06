"""
Startup Intelligence Agent for Imaginary Space.
Daily pipeline: fetch → pre-filter → Gemini analysis → Discord → save memory.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import requests
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")
MEMORY_PATH = os.path.join(os.path.dirname(__file__), "memory.json")
MAX_CANDIDATES_TO_GEMINI = 10
MAX_LEADS_TO_POST = 2

SOURCES = [
    {"name": "TechCrunch Startups", "url": "https://techcrunch.com/category/startups/feed/"},
    {"name": "Hacker News Funding", "url": "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=startup+funding+seed&hitsPerPage=30"},
    {"name": "Reddit r/startups", "url": "https://www.reddit.com/r/startups.json?limit=25&t=day"},
    {"name": "Reddit r/venturecapital", "url": "https://www.reddit.com/r/venturecapital.json?limit=25&t=day"},
]

STAGE_KEYWORDS = ["seed", "pre-seed", "preseed", "angel", "raised", "raises", "funding", "mvp", "pre seed"]
DISCARD_KEYWORDS = ["series b", "series c", "series d", "series e", "ipo", "acqui", "public company", "nasdaq", "nyse"]
TEAM_KEYWORDS = ["cto", "chief technology officer", "in-house engineering", "engineering team", "tech team"]

# ─── Memory ──────────────────────────────────────────────────────────────────

def load_memory() -> dict:
    with open(MEMORY_PATH) as f:
        return json.load(f)

def save_memory(memory: dict) -> None:
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)

# ─── Fetch ───────────────────────────────────────────────────────────────────

def fetch_rss(source: dict) -> list[dict]:
    try:
        feed = feedparser.parse(source["url"])
        items = []
        for entry in feed.entries:
            items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", entry.get("description", "")),
                "url": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source": source["name"],
            })
        return items
    except Exception as e:
        print(f"[WARN] Failed to fetch {source['name']}: {e}")
        return []

def fetch_hn(source: dict) -> list[dict]:
    try:
        resp = requests.get(source["url"], timeout=10, headers={"User-Agent": "startup-intel-agent/1.0"})
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        items = []
        for hit in hits:
            items.append({
                "title": hit.get("title", ""),
                "summary": hit.get("story_text") or hit.get("comment_text") or "",
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                "published": hit.get("created_at", ""),
                "source": source["name"],
            })
        return items
    except Exception as e:
        print(f"[WARN] Failed to fetch {source['name']}: {e}")
        return []

def fetch_reddit(source: dict) -> list[dict]:
    try:
        resp = requests.get(
            source["url"],
            timeout=10,
            headers={"User-Agent": "startup-intel-agent/1.0"},
        )
        resp.raise_for_status()
        posts = resp.json().get("data", {}).get("children", [])
        items = []
        for post in posts:
            d = post.get("data", {})
            items.append({
                "title": d.get("title", ""),
                "summary": d.get("selftext", ""),
                "url": d.get("url", ""),
                "published": "",
                "source": source["name"],
            })
        return items
    except Exception as e:
        print(f"[WARN] Failed to fetch {source['name']}: {e}")
        return []

def fetch_all_sources() -> list[dict]:
    all_items = []
    for source in SOURCES:
        name = source["name"]
        if "algolia" in source["url"]:
            all_items.extend(fetch_hn(source))
        elif "reddit.com" in source["url"]:
            all_items.extend(fetch_reddit(source))
        else:
            all_items.extend(fetch_rss(source))
        print(f"[INFO] Fetched {name}")
    return all_items

# ─── Filter ──────────────────────────────────────────────────────────────────

def extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def pre_filter(items: list[dict], memory: dict) -> list[dict]:
    processed = set(memory.get("processed_urls", []))
    blacklist = set(memory.get("blacklist_domains", []))
    ignored_kw = [k.lower() for k in memory["preferences"]["ignored_keywords"]]

    candidates = []
    for item in items:
        url = item["url"]
        domain = extract_domain(url)
        text = f"{item['title']} {item['summary']}".lower()

        # Skip already processed
        if url in processed:
            continue

        # Skip blacklisted domains
        if domain and domain in blacklist:
            continue

        # Must contain a stage/funding keyword
        if not any(kw in text for kw in STAGE_KEYWORDS):
            continue

        # Drop late-stage and off-topic
        if any(kw in text for kw in DISCARD_KEYWORDS):
            continue

        # Drop companies that already have a full tech team
        if any(kw in text for kw in TEAM_KEYWORDS):
            continue

        # Drop user-configured ignored keywords
        if any(kw in text for kw in ignored_kw):
            continue

        candidates.append(item)

    print(f"[INFO] {len(candidates)} candidates after pre-filter (from {len(items)} total)")
    return candidates[:MAX_CANDIDATES_TO_GEMINI]

# ─── Gemini Analysis ─────────────────────────────────────────────────────────

def build_groq_prompt(candidates: list[dict], last_7_reports: list) -> str:
    news_block = "\n\n".join(
        f"[{i+1}] SOURCE: {c['source']}\nTITLE: {c['title']}\nSUMMARY: {c['summary'][:400]}\nURL: {c['url']}"
        for i, c in enumerate(candidates)
    )

    previous_block = ""
    if last_7_reports:
        previous_names = [r.get("name", "") for r in last_7_reports if r.get("name")]
        if previous_names:
            previous_block = f"\n\nALREADY REPORTED (skip these): {', '.join(previous_names)}"

    return f"""You are a lead-gen scout for Imaginary Space, a product studio that takes early-stage founders from a scrappy MVP to a fully finished product in 4 weeks.

INCLUDE only items that are:
- A new funding announcement, a new product launch, or a signal from a VC, investor, or founder
- From a reliable source (TechCrunch, Bloomberg, HN, reputable VC blogs)
- Pre-seed or Seed round, $250K to $5M. HARD DISCARD any round above $10M.
- Company does NOT mention a CTO, VP Eng, or in-house engineering team

DISCARD: rounds above $10M, Series A/B/C+, research labs, crypto, biotech, pure research spinouts, companies with existing eng teams.

For each qualifying company, write a short outreach message (3 sentences max). Rules:
- Casual and peer-to-peer, not salesy
- No flattery (no "amazing", "incredible", "love what you're building")
- No em-dashes
- End with one open question
- One emoji max

Identify the real pain signal from stage:
- Pre-seed/Seed: figuring out who their customer is, proving the model, early distribution
- AI company: distribution problem (everyone has the tech, few have the customers)
- Marketplace: chicken-and-egg, retention
- B2B SaaS: sales cycles, proving ROI

Return ONLY valid JSON, no markdown, no explanation:

[
  {{
    "name": "Startup Name",
    "stage": "Pre-seed",
    "amount": "$1.2M",
    "uvp": "One plain-language sentence on what they do.",
    "founder": "First Last",
    "source_url": "https://...",
    "pain_signal": "One sentence on what challenge this startup faces right now.",
    "outreach_message": "3-sentence casual DM ending with a question."
  }}
]

If none qualify, return [].{previous_block}

NEWS ITEMS:
{news_block}"""

def analyze_with_gemini(candidates: list[dict], last_7_reports: list) -> list[dict]:
    if DRY_RUN:
        print("[DRY RUN] Skipping Groq call")
        return [
            {
                "name": "DryRun Co",
                "stage": "Seed",
                "amount": "$1.2M",
                "uvp": "Automates contract review for small law firms with no technical staff.",
                "founder": "Jane Smith",
                "source_url": "https://example.com",
                "pain_signal": "Seed-stage B2B SaaS proving early distribution without a sales team.",
                "outreach_message": "Saw DryRun Co just closed their seed round. Most legal-tech founders at this stage spend the first 6 months building features nobody asked for. What does your roadmap look like for the next 90 days?",
            }
        ]

    client = Groq(api_key=GROQ_API_KEY)
    prompt = build_groq_prompt(candidates, last_7_reports)
    print(f"[INFO] Sending {len(candidates)} candidates to Groq")

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1500,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if the model wraps the JSON
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        leads = json.loads(raw)
        if not isinstance(leads, list):
            raise ValueError("Model did not return a JSON array")
        return leads

    except json.JSONDecodeError as e:
        print(f"[ERROR] Groq returned invalid JSON: {e}")
        print(f"[DEBUG] Raw response: {raw[:500]}")
        return None
    except Exception as e:
        print(f"[ERROR] Groq call failed: {e}")
        return None

# ─── Discord ─────────────────────────────────────────────────────────────────

MAX_MESSAGE_CHARS = 1800

def build_linkedin_url(founder: str, company: str) -> str:
    query = f"{founder} {company}".strip().replace(" ", "+")
    return f"https://www.linkedin.com/search/results/people/?keywords={query}"

def no_emdash(text: str) -> str:
    return text.replace("—", "-").replace("–", "-")

def build_discord_message(leads: list[dict]) -> str:
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    count = len(leads)

    lines = [f"🔥 **Here is what's new friends - {date_str}** | {count} round{'s' if count != 1 else ''} 🌍🚀", ""]

    for lead in leads:
        name = no_emdash(lead.get("name", "Unknown"))
        amount = lead.get("amount", "undisclosed")
        stage = lead.get("stage", "")
        uvp = no_emdash(lead.get("uvp", ""))
        lines.append(f"📌 **{name}** raised {amount} {stage} - {uvp}")

    lines.append("")
    lines.append("")
    lines.append("**Cold Outreach Angles**")
    lines.append("")

    for lead in leads:
        name = no_emdash(lead.get("name", "Unknown"))
        amount = lead.get("amount", "undisclosed")
        stage = lead.get("stage", "")
        founder = lead.get("founder", "")
        company = lead.get("name", "")
        linkedin_url = build_linkedin_url(founder, company)
        message = no_emdash(lead.get("outreach_message", ""))
        signal = no_emdash(lead.get("pain_signal", ""))

        lines.append(f"🎯 **Potential Message**")
        lines.append(f"**{name} - {amount} {stage}**")
        lines.append(linkedin_url)
        lines.append(f"> {message}")
        lines.append(f"*Signal: {signal}*")
        lines.append("")

    full = "\n".join(lines).strip()

    # Hard truncate to Discord limit with a note
    if len(full) > MAX_MESSAGE_CHARS:
        full = full[:MAX_MESSAGE_CHARS - 20].rsplit("\n", 1)[0] + "\n...(truncated)"

    return full

def post_to_discord(leads: list[dict]) -> None:
    if not leads:
        print("[INFO] No leads to post")
        return

    message = build_discord_message(leads)

    if DRY_RUN:
        print("[DRY RUN] Would post to Discord:")
        print(message)
        return

    payload = {
        "username": "Startup Intel",
        "content": message,
    }

    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    if resp.status_code in (200, 204):
        print(f"[INFO] Posted {len(leads)} lead(s) to Discord")
    else:
        print(f"[ERROR] Discord returned {resp.status_code}: {resp.text}")

# ─── Main pipeline ────────────────────────────────────────────────────────────

def run():
    print(f"[INFO] Starting agent | dry_run={DRY_RUN} | {datetime.now(timezone.utc).isoformat()}")

    memory = load_memory()

    # 1. Fetch
    raw_items = fetch_all_sources()
    print(f"[INFO] Total items fetched: {len(raw_items)}")

    # 2. Pre-filter
    candidates = pre_filter(raw_items, memory)
    if not candidates:
        print("[INFO] No candidates passed the filter today. Exiting.")
        save_memory(memory)
        return

    # 3. Gemini — returns None on API error, [] if no qualifying leads
    leads = analyze_with_gemini(candidates, memory.get("last_7_reports", []))
    if leads is None:
        print("[ERROR] Gemini failed — skipping Discord and memory update so articles can be retried.")
        return
    print(f"[INFO] Groq returned {len(leads)} lead(s)")

    # 4. Discord
    post_to_discord(leads)

    # 5. Update memory — only reached if Gemini succeeded
    new_urls = [c["url"] for c in candidates]
    memory["processed_urls"] = list(set(memory.get("processed_urls", []) + new_urls))

    # Keep last 7 reports for Gemini context
    memory["last_7_reports"] = (memory.get("last_7_reports", []) + leads)[-7:]

    memory["last_run"] = datetime.now(timezone.utc).isoformat()

    save_memory(memory)
    print("[INFO] Memory updated. Done.")

if __name__ == "__main__":
    missing = []
    if not DRY_RUN:
        if not GROQ_API_KEY:
            missing.append("GROQ_API_KEY")
        if not DISCORD_WEBHOOK_URL:
            missing.append("DISCORD_WEBHOOK_URL")
    if missing:
        print(f"[ERROR] Missing required env vars: {', '.join(missing)}")
        sys.exit(1)
    run()
