"""
Startup Intelligence Agent for Imaginary Space.
Pipeline: fetch -> pre-filter -> Groq analysis -> Groq review -> Discord -> save memory.
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
MAX_CANDIDATES = 30
TARGET_LEADS = 7
MAX_MESSAGE_CHARS = 1950  # Discord hard limit is 2000

SOURCES = [
    {"name": "TechCrunch Startups",  "url": "https://techcrunch.com/category/startups/feed/"},
    {"name": "TechCrunch",           "url": "https://techcrunch.com/feed/"},
    {"name": "VentureBeat",          "url": "https://venturebeat.com/feed/"},
    {"name": "Crunchbase News",      "url": "https://news.crunchbase.com/feed/"},
    {"name": "EU Startups",          "url": "https://www.eu-startups.com/feed/"},
    {"name": "Sifted",               "url": "https://sifted.eu/feed"},
    {"name": "HN Funding",           "url": "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=seed+funding+startup&hitsPerPage=25"},
    {"name": "HN Pre-seed",          "url": "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=pre-seed+raise+startup&hitsPerPage=20"},
    {"name": "Reddit r/startups",    "url": "https://www.reddit.com/r/startups.json?limit=25&t=day"},
    {"name": "Reddit r/vc",          "url": "https://www.reddit.com/r/venturecapital.json?limit=25&t=day"},
]

# Pre-filter: must contain at least one of these to pass
STAGE_KEYWORDS = ["seed", "pre-seed", "preseed", "angel", "raised", "raises", "funding", "pre seed"]

# Pre-filter: drop if contains any of these (obvious non-ICP, handled fast in Python)
HARD_DISCARD = [
    "series b", "series c", "series d", "series e", "series f",
    "ipo", "nasdaq", "nyse", "acqui", "acquisition", "public offering",
    "crypto", "blockchain", "nft", "biotech", "pharmaceutical",
]

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
        return [
            {
                "title": e.get("title", ""),
                "summary": e.get("summary", e.get("description", "")),
                "url": e.get("link", ""),
                "source": source["name"],
            }
            for e in feed.entries
        ]
    except Exception as ex:
        print(f"[WARN] {source['name']}: {ex}")
        return []

def fetch_hn(source: dict) -> list[dict]:
    try:
        r = requests.get(source["url"], timeout=10, headers={"User-Agent": "startup-intel/1.0"})
        r.raise_for_status()
        return [
            {
                "title": h.get("title", ""),
                "summary": h.get("story_text") or "",
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "source": source["name"],
            }
            for h in r.json().get("hits", [])
        ]
    except Exception as ex:
        print(f"[WARN] {source['name']}: {ex}")
        return []

def fetch_reddit(source: dict) -> list[dict]:
    try:
        r = requests.get(source["url"], timeout=10, headers={"User-Agent": "startup-intel/1.0"})
        r.raise_for_status()
        return [
            {
                "title": p["data"].get("title", ""),
                "summary": p["data"].get("selftext", ""),
                "url": p["data"].get("url", ""),
                "source": source["name"],
            }
            for p in r.json().get("data", {}).get("children", [])
        ]
    except Exception as ex:
        print(f"[WARN] {source['name']}: {ex}")
        return []

def fetch_all_sources() -> list[dict]:
    items = []
    for src in SOURCES:
        url = src["url"]
        if "algolia" in url:
            batch = fetch_hn(src)
        elif "reddit.com" in url:
            batch = fetch_reddit(src)
        else:
            batch = fetch_rss(src)
        print(f"[INFO] {src['name']}: {len(batch)} items")
        items.extend(batch)
    return items

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

    seen_urls = set()
    candidates = []

    for item in items:
        url = item["url"]
        if not url or url in processed or url in seen_urls:
            continue
        if extract_domain(url) in blacklist:
            continue

        text = f"{item['title']} {item['summary']}".lower()

        if not any(kw in text for kw in STAGE_KEYWORDS):
            continue
        if any(kw in text for kw in HARD_DISCARD):
            continue
        if any(kw in text for kw in ignored_kw):
            continue

        seen_urls.add(url)
        candidates.append(item)

    print(f"[INFO] Pre-filter: {len(candidates)} candidates from {len(items)} items")
    return candidates[:MAX_CANDIDATES]

# ─── Amount normalizer ───────────────────────────────────────────────────────

def normalize_amount(raw: str) -> str:
    if not raw:
        return "undisclosed"
    s = raw.lower().strip()
    # Already clean: $1.2M, $500K
    if re.match(r"^\$[\d.]+[mkb]$", s, re.I):
        return raw.upper().replace("K", "K").replace("M", "M").replace("B", "B")
    # Extract leading number + optional unit
    m = re.search(r"([\d,.]+)\s*(billion|million|thousand|b|m|k)?", s)
    if not m:
        return raw
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return raw
    unit = (m.group(2) or "").lower()
    if unit in ("billion", "b"):
        return f"${num:.1f}B"
    if unit in ("million", "m"):
        return f"${num:.1f}M"
    if unit in ("thousand", "k"):
        return f"${num:.0f}K"
    # Raw number
    if num >= 1_000_000:
        return f"${num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"${num / 1_000:.0f}K"
    return f"${num:.0f}"

# ─── Groq: analysis pass ─────────────────────────────────────────────────────

def build_analysis_prompt(candidates: list[dict], last_7_reports: list) -> str:
    news_block = "\n\n".join(
        f"[{i+1}] {c['source']}\nTITLE: {c['title']}\nSUMMARY: {c['summary'][:350]}\nURL: {c['url']}"
        for i, c in enumerate(candidates)
    )
    skip = ", ".join(r.get("name", "") for r in last_7_reports if r.get("name"))
    skip_line = f"\nSKIP (already reported this week): {skip}" if skip else ""

    return f"""You are a lead-gen scout for Imaginary Space, a product studio that builds an early-stage founder's MVP into a finished product in 4 weeks. Flat fee, full build: design, engineering, QA, delivery.

IDEAL CLIENT:
- Pre-seed or Seed round, $100K to $8M. Discard anything above $10M.
- Has a working prototype or MVP, but needs the full product built
- No dedicated CTO or engineering team yet
- Verticals: AI tools, B2B SaaS, marketplaces, developer tools, automation, Fintech

HARD DISCARD (exclude entirely):
- Round above $10M
- Already has CTO, VP Engineering, or "engineering team of X"
- Series A, B, C or later
- Pure research labs, crypto, biotech, academic spinouts{skip_line}

ONLY include items that are real announcements: a funding round closed, a product launched, a founder or VC posted a signal. No opinion pieces, no retrospectives.

Find up to {TARGET_LEADS} qualifying companies. For each, write a cold outreach DM for Carlos (he runs Imaginary Space).

DM rules - read carefully:
- Open with a SPECIFIC observation about their situation (stage, sector, distribution challenge). NOT "congrats" or "love what you're building".
- 2-3 sentences max
- No em-dashes (use commas or periods instead)
- No flattery words: amazing, incredible, impressive, exciting, awesome
- Casual tone, peer-to-peer
- End with ONE open question about their current challenge
- Zero or one emoji total

Return ONLY a JSON array, no markdown fences, no explanation:

[
  {{
    "name": "Startup Name",
    "stage": "Pre-seed",
    "amount": "$1.5M",
    "uvp": "One plain sentence on what they do.",
    "founder": "First Last or unknown",
    "source_url": "https://...",
    "pain_signal": "One sentence on the specific challenge they face at this stage.",
    "outreach_message": "Your 2-3 sentence DM draft here."
  }}
]

If nothing qualifies, return [].

NEWS ITEMS:
{news_block}"""


def build_review_prompt(leads: list[dict]) -> str:
    leads_block = json.dumps(leads, indent=2)
    return f"""You are reviewing cold outreach messages before they are sent. Check each message against these rules:

Rules:
1. No flattery openers ("congrats", "love what you're building", "amazing", "incredible", "impressive")
2. No em-dashes. Replace with commas or periods.
3. Must open with a specific observation about the company's situation, not a generic comment
4. 2-3 sentences max
5. Must end with one open question
6. Peer-to-peer tone, not salesy
7. If founder is "unknown" or "not specified", set it to "unknown"
8. Amount must be in format like "$1.5M", "$500K", "$2B". If in words like "1 million euros", convert to "$1M".

Return the corrected JSON array with the same structure. Fix any violations. Do not add new companies. Return ONLY valid JSON, no markdown:

{leads_block}"""


def call_groq(prompt: str, max_tokens: int = 2500) -> str | None:
    client = Groq(api_key=GROQ_API_KEY)
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception as ex:
        print(f"[ERROR] Groq call failed: {ex}")
        return None


def parse_json_response(raw: str) -> list | None:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
        print(f"[ERROR] Expected JSON array, got: {type(result)}")
        return None
    except json.JSONDecodeError as ex:
        print(f"[ERROR] JSON parse failed: {ex}")
        print(f"[DEBUG] Raw: {raw[:400]}")
        return None


def analyze_and_review(candidates: list[dict], last_7_reports: list) -> list[dict] | None:
    if DRY_RUN:
        print("[DRY RUN] Skipping Groq calls")
        return [
            {
                "name": "Loops",
                "stage": "Seed",
                "amount": "$2.1M",
                "uvp": "Email platform built for SaaS products that replaces Mailchimp for product teams.",
                "founder": "Chris Frantz",
                "source_url": "https://techcrunch.com/example",
                "pain_signal": "Seed-stage SaaS proving early distribution without a dedicated growth team.",
                "outreach_message": "Most SaaS founders at seed stage spend 3 months picking the wrong email tool before they realize their onboarding flow is the real problem. What does your activation funnel look like right now?",
            },
            {
                "name": "Finta",
                "stage": "Pre-seed",
                "amount": "$1.8M",
                "uvp": "Automates investor updates and cap table management for early-stage founders.",
                "founder": "Ramy Adeeb",
                "source_url": "https://techcrunch.com/example2",
                "pain_signal": "Pre-seed fintech proving product-market fit before they can afford a full engineering team.",
                "outreach_message": "Fintech compliance at pre-seed is usually the thing that slows down the product roadmap the most. Are you building the core product in-house or working with outside dev capacity right now?",
            },
        ]

    # Pass 1: find and score leads
    print(f"[INFO] Pass 1: sending {len(candidates)} candidates to Groq")
    raw1 = call_groq(build_analysis_prompt(candidates, last_7_reports), max_tokens=3000)
    if raw1 is None:
        return None

    leads = parse_json_response(raw1)
    if leads is None:
        return None
    if not leads:
        print("[INFO] Groq found no qualifying leads")
        return []

    print(f"[INFO] Pass 1 returned {len(leads)} lead(s) — running review pass")

    # Pass 2: review and fix messages
    raw2 = call_groq(build_review_prompt(leads), max_tokens=2500)
    if raw2 is None:
        print("[WARN] Review pass failed — using pass 1 output as-is")
        return leads

    reviewed = parse_json_response(raw2)
    if reviewed is None:
        print("[WARN] Review pass returned invalid JSON — using pass 1 output")
        return leads

    print(f"[INFO] Pass 2 review complete: {len(reviewed)} lead(s)")
    return reviewed

# ─── Discord formatter ───────────────────────────────────────────────────────

def no_emdash(text: str) -> str:
    return text.replace("—", "-").replace("–", "-")

def linkedin_url(founder: str, company: str) -> str:
    # Use angle brackets to suppress Discord unfurl
    name = founder if founder and founder.lower() not in ("unknown", "not specified", "") else ""
    query = f"{name} {company} CEO founder".strip().replace(" ", "+")
    return f"<https://www.linkedin.com/search/results/people/?keywords={query}>"

def build_discord_message(leads: list[dict]) -> str:
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    count = len(leads)

    # ── Section 1: news summary ──────────────────────────────────────────────
    lines = [
        f"**Startup Intel - {date_str}** | {count} new signal{'s' if count != 1 else ''}",
        "",
    ]
    for lead in leads:
        name = no_emdash(lead.get("name", "Unknown"))
        amount = normalize_amount(lead.get("amount", ""))
        stage = lead.get("stage", "")
        uvp = no_emdash(lead.get("uvp", ""))
        lines.append(f"- **{name}** {amount} {stage} | {uvp}")

    lines += ["", "---", "**Outreach Angles**", ""]

    # ── Section 2: outreach — include as many as fit ─────────────────────────
    header_len = len("\n".join(lines))
    outreach_blocks = []

    for lead in leads:
        name = no_emdash(lead.get("name", "Unknown"))
        amount = normalize_amount(lead.get("amount", ""))
        stage = lead.get("stage", "")
        founder = lead.get("founder", "")
        li = linkedin_url(founder, name)
        msg = no_emdash(lead.get("outreach_message", ""))
        signal = no_emdash(lead.get("pain_signal", ""))

        block = f"**{name} | {amount} {stage}**\n{li}\n> {msg}\n*{signal}*\n"
        outreach_blocks.append(block)

    # Add blocks until we'd exceed the limit
    included = []
    running = header_len
    for block in outreach_blocks:
        if running + len(block) + 1 > MAX_MESSAGE_CHARS:
            break
        included.append(block)
        running += len(block) + 1

    lines += included
    return "\n".join(lines).strip()

# ─── Post ────────────────────────────────────────────────────────────────────

def post_to_discord(leads: list[dict]) -> None:
    if not leads:
        print("[INFO] No leads — nothing to post")
        return

    message = build_discord_message(leads)

    if DRY_RUN:
        print("[DRY RUN] Discord message preview:")
        print("-" * 60)
        print(message)
        print("-" * 60)
        print(f"[DRY RUN] Length: {len(message)} chars")
        return

    resp = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"username": "Startup Intel", "content": message},
        timeout=10,
    )
    if resp.status_code in (200, 204):
        print(f"[INFO] Posted {len(leads)} lead(s) to Discord ({len(message)} chars)")
    else:
        print(f"[ERROR] Discord {resp.status_code}: {resp.text}")

# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    print(f"[INFO] Agent start | dry_run={DRY_RUN} | {datetime.now(timezone.utc).isoformat()}")

    memory = load_memory()

    raw_items = fetch_all_sources()
    print(f"[INFO] Total fetched: {len(raw_items)}")

    candidates = pre_filter(raw_items, memory)
    if not candidates:
        print("[INFO] No candidates after filter. Done.")
        memory["last_run"] = datetime.now(timezone.utc).isoformat()
        save_memory(memory)
        return

    leads = analyze_and_review(candidates, memory.get("last_7_reports", []))
    if leads is None:
        print("[ERROR] Groq failed — memory not updated so articles can be retried tomorrow.")
        return

    print(f"[INFO] Final leads: {len(leads)}")
    post_to_discord(leads)

    if DRY_RUN:
        print("[DRY RUN] Skipping memory save.")
        return

    # Update memory only after successful live run
    new_urls = [c["url"] for c in candidates]
    memory["processed_urls"] = list(set(memory.get("processed_urls", []) + new_urls))
    memory["last_7_reports"] = (memory.get("last_7_reports", []) + leads)[-7:]
    memory["last_run"] = datetime.now(timezone.utc).isoformat()
    save_memory(memory)
    print("[INFO] Memory saved. Done.")


if __name__ == "__main__":
    if not DRY_RUN:
        missing = [v for v in ("GROQ_API_KEY", "DISCORD_WEBHOOK_URL") if not os.getenv(v)]
        if missing:
            print(f"[ERROR] Missing env vars: {', '.join(missing)}")
            sys.exit(1)
    run()
