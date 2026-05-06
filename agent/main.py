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
TARGET_LEADS = 10   # Groq will filter down; we want enough raw input
MAX_OUTREACH = 4    # Max companies in the outreach section
MAX_MESSAGE_CHARS = 1800

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

# ─── Groq pipeline ───────────────────────────────────────────────────────────

def build_analysis_prompt(candidates: list[dict], last_7_reports: list) -> str:
    news_block = "\n\n".join(
        f"[{i+1}] SOURCE: {c['source']}\nTITLE: {c['title']}\nSUMMARY: {c['summary'][:350]}\nURL: {c['url']}"
        for i, c in enumerate(candidates)
    )
    skip = ", ".join(r.get("name", "") for r in last_7_reports if r.get("name"))
    skip_line = f"\nREJECT these (already reported this week): {skip}" if skip else ""

    return f"""You are operating a strict data pipeline. Your job is to transform noisy startup news into structured lead data. No creativity. No gap-filling. If data is missing or weak, reject the company.

--- STEP 1: HARD REJECTION ---

Include a company ONLY if ALL conditions are true:
- Contains a clear funding event (raised, secured, closed a round)
- Funding amount is EXPLICITLY stated in the article
- Funding is between $250K and $5M USD (convert currencies if needed; reject if above $5M)
- Stage is Pre-seed, Seed, or Series A at most
- Source is credible (TechCrunch, VentureBeat, Sifted, Crunchbase, EU Startups, HN)

REJECT if:
- No funding amount stated
- Amount is above $3M
- Famous founders or well-known companies
- Opinion pieces, product launches, or announcements without funding
- Quantum computing, biotech, defense, deep tech research labs{skip_line}

--- STEP 2: DATA NORMALIZATION ---

For each accepted company:
- Amount: convert to USD millions. Examples: €1M = $1.1M, €2.8M = $3.1M, $750K = $0.75M
- Round: must be Pre-seed, Seed, or Series A. If unclear, reject.
- Founder: if missing, use "{{Company}} CEO"
- UVP: one plain sentence, no buzzwords, no fluff

--- STEP 3 + 4: OUTREACH MESSAGES ---

For max {MAX_OUTREACH} of the accepted companies, write a cold DM.

BANNED phrases (hard reject any message containing):
- "You're building"
- "Interesting"
- "Curious about"
- "Love what"
- Any compliment or flattery
- Restating the company description

Each message MUST:
1. Open with a specific observation about their stage and operational challenge
2. Be max 3 sentences
3. End with exactly one direct question about a growth bottleneck
4. Use at most one emoji
5. Be peer-to-peer, not salesy

Signal mapping (use the right one, no guessing):
- Pre-seed: unclear ICP, early distribution, validating product
- Seed: finding repeatable acquisition channel, early scaling issues
- AI company: distribution problem (tech is commoditized, differentiation missing)
- Marketplace: supply-demand imbalance, retention
- Health/regulated: slow sales cycles, compliance friction

BAD signal: "needs to scale"
GOOD signal: "has not found a repeatable acquisition channel yet"

--- STEP 5: SELF-REWRITE ---

After generating your output, rewrite every message that:
- Contains any banned phrase
- Has a weak or vague signal
- Opens with a compliment
- Restates the company description
- Uses an em-dash

Do NOT validate. REWRITE.

--- OUTPUT ---

Return ONLY a valid JSON array, no markdown fences, no explanation:

[
  {{
    "name": "Startup Name",
    "stage": "Seed",
    "amount": "$1.5M",
    "uvp": "One plain sentence.",
    "founder": "First Last",
    "source_url": "https://...",
    "signal": "has not found a repeatable acquisition channel yet",
    "outreach_message": "2-3 sentence DM ending with a question.",
    "include_outreach": true
  }}
]

Set "include_outreach": true for the top {MAX_OUTREACH} companies only. Set false for the rest.
If nothing qualifies after filtering, return [].

NEWS ITEMS:
{news_block}"""


def build_rewrite_prompt(leads: list[dict]) -> str:
    return f"""You are a strict editor. Check every outreach_message in this JSON for violations. Do NOT validate — REWRITE any message that breaks the rules.

Rules:
1. No em-dashes. Replace with a comma or period.
2. No banned openers: "You're building", "Interesting", "Curious", "Love what", any compliment.
3. Must open with a specific observation about the company's stage and problem.
4. Max 3 sentences. End with one question.
5. Signal must be concrete: BAD "needs to scale" / GOOD "has not found a repeatable acquisition channel yet"
6. Amount must be "$XM" or "$X.XM" format (e.g. "$1.5M", "$0.75M"). Fix if wrong.
7. If founder is blank, "unknown", or "not specified", set to "{{name}} CEO".

Return the corrected JSON array with identical structure. Return ONLY valid JSON, no markdown:

{json.dumps(leads, indent=2)}"""


def call_groq(prompt: str, max_tokens: int = 3000) -> str | None:
    client = Groq(api_key=GROQ_API_KEY)
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception as ex:
        print(f"[ERROR] Groq call failed: {ex}")
        return None


def parse_json_response(raw: str) -> list | None:
    # Strip code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Extract the JSON array even if Groq adds text before/after
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        print(f"[ERROR] No JSON array found in response")
        print(f"[DEBUG] Raw snippet: {cleaned[:300]}")
        return None
    try:
        result = json.loads(match.group())
        if isinstance(result, list):
            return result
        print(f"[ERROR] Expected list, got: {type(result)}")
        return None
    except json.JSONDecodeError as ex:
        print(f"[ERROR] JSON parse failed: {ex}")
        print(f"[DEBUG] Extracted: {match.group()[:400]}")
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
                "signal": "has not found a repeatable acquisition channel beyond direct sales yet",
                "outreach_message": "Most seed-stage SaaS founders I talk to realize 6 months in that their onboarding flow is killing activation before email even matters. What does your week-1 retention look like right now?",
                "include_outreach": True,
            },
            {
                "name": "Finta",
                "stage": "Pre-seed",
                "amount": "$1.8M",
                "uvp": "Automates investor updates and cap table management for early-stage founders.",
                "founder": "Ramy Adeeb",
                "source_url": "https://techcrunch.com/example2",
                "signal": "unclear ICP: targeting all founders when the real pain is felt by solo technical founders post-raise",
                "outreach_message": "Pre-seed fintech tools usually die on ICP clarity before they hit distribution. Who is the one founder profile you are seeing close fastest right now?",
                "include_outreach": True,
            },
        ]

    print(f"[INFO] Pass 1: analysis — {len(candidates)} candidates")
    raw1 = call_groq(build_analysis_prompt(candidates, last_7_reports))
    if raw1 is None:
        return None

    leads = parse_json_response(raw1)
    if leads is None:
        return None
    if not leads:
        print("[INFO] No qualifying leads after filtering")
        return []

    print(f"[INFO] Pass 1: {len(leads)} lead(s) — running rewrite pass")

    raw2 = call_groq(build_rewrite_prompt(leads), max_tokens=2500)
    if raw2 is None:
        print("[WARN] Rewrite pass failed — using pass 1 output")
        return leads

    rewritten = parse_json_response(raw2)
    if rewritten is None:
        print("[WARN] Rewrite pass returned invalid JSON — using pass 1 output")
        return leads

    print(f"[INFO] Pass 2: rewrite complete — {len(rewritten)} lead(s)")
    return rewritten

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

    # ── Section 1: funding summary ───────────────────────────────────────────
    lines = [
        f"🔥 Here is what's new friends - {date_str} | {count} round{'s' if count != 1 else ''} 🌍🚀",
        "",
    ]
    for lead in leads:
        name = no_emdash(lead.get("name", "Unknown"))
        amount = normalize_amount(lead.get("amount", ""))
        stage = lead.get("stage", "")
        uvp = no_emdash(lead.get("uvp", ""))
        lines.append(f"📌 {name} raised {amount} {stage} - {uvp}")

    lines += ["", "Cold Outreach Angles", ""]

    # ── Section 2: outreach (max MAX_OUTREACH, must fit in char limit) ───────
    outreach_included = 0
    for lead in leads:
        if not lead.get("include_outreach"):
            continue
        if outreach_included >= MAX_OUTREACH:
            break

        name = no_emdash(lead.get("name", "Unknown"))
        amount = normalize_amount(lead.get("amount", ""))
        stage = lead.get("stage", "")
        founder = lead.get("founder", "") or f"{name} CEO"
        li = linkedin_url(founder, name)
        msg = no_emdash(lead.get("outreach_message", ""))
        signal = no_emdash(lead.get("signal", ""))

        block = [
            f"🎯 Potential Message",
            f"{name} | {amount} {stage}",
            li,
            "",
            f"> {msg}",
            f"> Signal: {signal}",
            "",
        ]
        candidate = "\n".join(lines + block)
        if len(candidate) > MAX_MESSAGE_CHARS:
            break
        lines += block
        outreach_included += 1

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
