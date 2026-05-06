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

def build_gemini_prompt(candidates: list[dict], last_7_reports: list) -> str:
    news_block = "\n\n".join(
        f"[{i+1}] SOURCE: {c['source']}\nTITLE: {c['title']}\nSUMMARY: {c['summary'][:400]}\nURL: {c['url']}"
        for i, c in enumerate(candidates)
    )

    previous_block = ""
    if last_7_reports:
        previous_names = [r.get("name", "") for r in last_7_reports if r.get("name")]
        if previous_names:
            previous_block = f"\n\nALREADY REPORTED (do NOT include these): {', '.join(previous_names)}"

    return f"""You are a lead-gen scout for Imaginary Space, a product studio that takes early-stage founders from a scrappy MVP to a fully finished product in 4 weeks. We charge a flat fee and do the full build — design, engineering, QA, delivery.

Our ideal client:
- Just received Pre-seed or Seed funding in the $250K–$5M range. DISCARD any round above $10M — those companies can hire their own team.
- Has a working MVP or prototype but not a finished product
- Does NOT have an in-house CTO or dedicated engineering team
- Is in a vertical we can serve: AI tools, SaaS, B2B software, marketplaces, developer tools, automation
- Founder is likely a solo founder or small non-technical team who needs to move fast

Hard discard rules (exclude entirely):
- Round size above $10M
- Company already mentions CTO, VP Engineering, or engineering team
- Late stage: Series A, B, C, or beyond
- Pure research labs or academic spinouts with no product intent

From the news items below, identify the top {MAX_LEADS_TO_POST} that are the strongest leads for Imaginary Space. If fewer than {MAX_LEADS_TO_POST} genuinely qualify, return only those. If none qualify, return [].

Return ONLY valid JSON — no markdown, no explanation — in this exact format:

[
  {{
    "name": "Startup Name",
    "stage": "Pre-seed / Seed",
    "amount": "$1.2M",
    "what_they_do": "One sentence description.",
    "why_imaginary_space": "Specific reason why they need us right now — what gap we fill.",
    "linkedin_search": "founder name Imaginary Space CEO",
    "source_url": "https://...",
    "hours_ago": "~4h"
  }}
]

If fewer than {MAX_LEADS_TO_POST} items are genuinely good leads, return only those. If none qualify, return an empty array [].{previous_block}

NEWS ITEMS:
{news_block}"""

def analyze_with_gemini(candidates: list[dict], last_7_reports: list) -> list[dict]:
    if DRY_RUN:
        print("[DRY RUN] Skipping Groq call")
        return [
            {
                "name": "DryRun Co",
                "stage": "Seed",
                "amount": "$1M",
                "what_they_do": "A test startup for dry run mode.",
                "why_imaginary_space": "They need a full product build and have no engineering team.",
                "linkedin_search": "DryRun Co CEO",
                "source_url": "https://example.com",
                "hours_ago": "~2h",
            }
        ]

    client = Groq(api_key=GROQ_API_KEY)
    prompt = build_gemini_prompt(candidates, last_7_reports)
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

def build_linkedin_url(search_query: str) -> str:
    query = search_query.replace(" ", "%20")
    return f"https://www.linkedin.com/search/results/people/?keywords={query}"

def post_to_discord(leads: list[dict]) -> None:
    if not leads:
        print("[INFO] No leads to post")
        return

    if DRY_RUN:
        print("[DRY RUN] Would post to Discord:")
        for lead in leads:
            print(json.dumps(lead, indent=2))
        return

    date_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
    embeds = []

    for lead in leads:
        linkedin_url = build_linkedin_url(lead.get("linkedin_search", lead.get("name", "")))
        embed = {
            "title": f"🚀 {lead.get('name', 'Unknown')}",
            "color": 0x5865F2,
            "fields": [
                {
                    "name": "Stage & Funding",
                    "value": f"{lead.get('stage', '—')} · {lead.get('amount', '—')}",
                    "inline": True,
                },
                {
                    "name": "Published",
                    "value": lead.get("hours_ago", "—"),
                    "inline": True,
                },
                {
                    "name": "What They Do",
                    "value": lead.get("what_they_do", "—"),
                    "inline": False,
                },
                {
                    "name": "Why Imaginary Space",
                    "value": lead.get("why_imaginary_space", "—"),
                    "inline": False,
                },
                {
                    "name": "Find the Founder",
                    "value": f"[LinkedIn Search]({linkedin_url})",
                    "inline": True,
                },
                {
                    "name": "Source",
                    "value": f"[Read Article]({lead.get('source_url', '#')})",
                    "inline": True,
                },
            ],
            "footer": {"text": f"Imaginary Space Intel · {date_str}"},
        }
        embeds.append(embed)

    payload = {
        "username": "Startup Intel",
        "avatar_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f680.png",
        "embeds": embeds,
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
