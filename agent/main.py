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
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
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

# ─── Config file ─────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "icp": "Early-stage founders who just raised and need to ship an MVP fast.",
    "amount_min_usd": 250000,
    "amount_max_usd": 5000000,
    "extra_target_keywords": [],
    "extra_ignored_keywords": [],
    "extra_signal_hints": "",
}

def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        print(f"[CONFIG] Loaded: ICP set | range ${cfg.get('amount_min_usd',250000)//1000}K–${cfg.get('amount_max_usd',5000000)//1000000}M | extra_keywords={cfg.get('extra_target_keywords',[])} | hints={'yes' if cfg.get('extra_signal_hints') else 'none'}")
        return {**DEFAULT_CONFIG, **cfg}
    except FileNotFoundError:
        print("[CONFIG] config.json not found — using defaults")
        return DEFAULT_CONFIG
    except Exception as ex:
        print(f"[CONFIG] Parse error: {ex} — using defaults")
        return DEFAULT_CONFIG

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

def build_pass1_prompt(candidates: list[dict], last_7_reports: list, cfg: dict = None) -> str:
    cfg = cfg or DEFAULT_CONFIG
    news_block = "\n\n".join(
        f"[{i+1}] SOURCE: {c['source']}\nTITLE: {c['title']}\nSUMMARY: {c['summary'][:350]}\nURL: {c['url']}"
        for i, c in enumerate(candidates)
    )
    skip_names = [r.get("company") or r.get("name", "") for r in last_7_reports]
    skip = ", ".join(n for n in skip_names if n)
    skip_line = f"\nREJECT these (already reported this week): {skip}" if skip else ""
    amount_max = cfg["amount_max_usd"]
    amount_max_str = f"${amount_max // 1_000_000}M"
    extra_hints = f"\nExtra signal hints from config: {cfg['extra_signal_hints']}" if cfg.get("extra_signal_hints") else ""

    return f"""You are a lead intelligence analyst for Imaginary Space — an enterprise AI development studio that ships RAG pipelines, AI agents, and full-stack applications in 4–12 weeks.

You will receive a list of startup funding articles. Your job is to extract only the leads that match our ICP and assign them ONE accurate signal.

ICP: Founders who just raised Pre-seed or Seed ($250K–{amount_max_str}), are in a build phase, and likely lack the engineering bandwidth to ship AI fast.
{extra_hints}

HARD REJECTION RULES — discard any lead that:
- Raised Series A or later
- Is a hardware, biotech, or pure fintech company with no software build need
- Has an established engineering team already mentioned
- Amount is not explicitly stated or is above {amount_max_str}
- Opinion piece, product launch, or announcement without a funding event{skip_line}

FOR EACH QUALIFYING LEAD OUTPUT EXACTLY THIS JSON OBJECT:
{{
  "company": "string",
  "amount": "string (e.g. $3.7M — convert currencies to USD)",
  "stage": "Pre-seed or Seed",
  "description": "one sentence, what the product does and who it serves",
  "signal": "one of the signals below — pick the most accurate one",
  "url": "original article URL"
}}

SIGNAL OPTIONS — pick exactly one per lead:
- "just funded, no product yet" — raise announced but no live product mentioned
- "building with AI, no technical co-founder" — non-technical founder in an AI space
- "shipping fast pressure" — language in article suggests urgency to ship or compete
- "enterprise client waiting" — article mentions a customer or pilot already signed
- "replacing manual process with AI" — clear automation or workflow replacement use case
- "expanding to new market" — raise specifically for geographic or vertical expansion

DO NOT use "has not found a repeatable acquisition channel" — that is not our signal.
DO NOT invent signals outside the list above.
OUTPUT ONLY a valid JSON array. No explanation, no markdown, no extra text.

NEWS ITEMS:
{news_block}"""


def build_pass2_prompt(lead: dict) -> str:
    return f"""You are writing a cold LinkedIn DM on behalf of Carlos at Imaginary Space.

Imaginary Space ships production AI systems in 4–12 weeks. RAG pipelines, autonomous agents, full-stack applications. 50+ products shipped. Our ICP is a founder who just raised and needs to move fast on building.

You will receive one lead object. Write ONE outreach message that:
- Opens with a specific observation about THIS company (not a generic line)
- References the raise naturally — not as flattery, as context
- Connects their signal to a specific thing Imaginary Space solves
- Ends with one low-friction question (not "what's your biggest challenge")
- Sounds like a peer talking to a peer — no corporate language
- Is 3 sentences maximum

ALSO output a LinkedIn search URL in this exact format:
https://www.linkedin.com/search/results/people/?keywords={{CompanyName}}+CEO+founder

Replace {{CompanyName}} with the company name only. No repetition. Do NOT add "CEO founder" after the company name if it is already in the URL.

OUTPUT FORMAT (plain text, no JSON):
{lead.get('company', 'Company')} | {lead.get('amount', '')} {lead.get('stage', '')}
https://www.linkedin.com/search/results/people/?keywords={lead.get('company', '').replace(' ', '+')}+CEO+founder

> [your message here]
> Signal: {lead.get('signal', '')}

DO NOT repeat the company name twice in the URL.
DO NOT start the message with "As a seed-stage company".
DO NOT use the word "challenges", "hurdles", or "acquisition channel".

LEAD:
{json.dumps(lead, indent=2)}"""


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


def analyze_and_review(candidates: list[dict], last_7_reports: list, cfg: dict = None) -> list[dict] | None:
    if DRY_RUN:
        print("[DRY RUN] Skipping Groq calls")
        return [
            {
                "company": "Loops",
                "stage": "Seed",
                "amount": "$2.1M",
                "description": "Email platform built for SaaS products replacing Mailchimp for product teams.",
                "signal": "replacing manual process with AI",
                "url": "https://techcrunch.com/example",
                "outreach_block": "Loops | $2.1M Seed\nhttps://www.linkedin.com/search/results/people/?keywords=Loops+CEO+founder\n\n> Most seed-stage SaaS founders ship email tooling last, then realize it's the thing killing activation. You raised to go fast — is the product-to-email handoff already wired or still manual?\n> Signal: replacing manual process with AI",
            },
            {
                "company": "Finta",
                "stage": "Pre-seed",
                "amount": "$1.8M",
                "description": "Automates investor updates and cap table management for early-stage founders.",
                "signal": "just funded, no product yet",
                "url": "https://techcrunch.com/example2",
                "outreach_block": "Finta | $1.8M Pre-seed\nhttps://www.linkedin.com/search/results/people/?keywords=Finta+CEO+founder\n\n> Cap table tooling at pre-seed usually gets built last, right after the thing that actually closes the next round. What does your current investor reporting look like?\n> Signal: just funded, no product yet",
            },
        ]

    print(f"[INFO] Pass 1: extraction — {len(candidates)} candidates")
    raw1 = call_groq(build_pass1_prompt(candidates, last_7_reports, cfg))
    if raw1 is None:
        return None

    leads = parse_json_response(raw1)
    if leads is None:
        return None
    if not leads:
        print("[INFO] No qualifying leads after filtering")
        return []

    print(f"[INFO] Pass 1: {len(leads)} lead(s) — running outreach pass (per-lead)")

    # Pass 2: one Groq call per lead, up to MAX_OUTREACH
    for i, lead in enumerate(leads[:MAX_OUTREACH]):
        raw2 = call_groq(build_pass2_prompt(lead), max_tokens=400)
        if raw2:
            lead["outreach_block"] = raw2.strip()
            print(f"[INFO] Pass 2: outreach written for {lead.get('company', '?')}")
        else:
            print(f"[WARN] Pass 2: failed for {lead.get('company', '?')} — skipping outreach")

    print(f"[INFO] Pipeline complete — {len(leads)} lead(s)")
    return leads

# ─── Discord formatter ───────────────────────────────────────────────────────

def no_emdash(text: str) -> str:
    return text.replace("—", "-").replace("–", "-")

def build_discord_message(leads: list[dict], cfg: dict = None) -> str:
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    count = len(leads)

    # ── Section 1: funding summary ───────────────────────────────────────────
    lines = [
        f"🔥 Here is what's new friends - {date_str} | {count} round{'s' if count != 1 else ''} 🌍🚀",
        "",
    ]
    for lead in leads:
        name = no_emdash(lead.get("company", "Unknown"))
        amount = normalize_amount(lead.get("amount", ""))
        stage = lead.get("stage", "")
        desc = no_emdash(lead.get("description", ""))
        lines.append(f"📌 {name} raised {amount} {stage} - {desc}")

    lines += ["", "Cold Outreach Angles", ""]

    # ── Section 2: outreach blocks written by Pass 2 ─────────────────────────
    outreach_included = 0
    for lead in leads:
        block_text = lead.get("outreach_block", "")
        if not block_text:
            continue
        if outreach_included >= MAX_OUTREACH:
            break

        block = ["🎯 Potential Message", no_emdash(block_text), ""]
        candidate = "\n".join(lines + block)
        if len(candidate) > MAX_MESSAGE_CHARS:
            break
        lines += block
        outreach_included += 1

    # ── Footer: active config confirmation ───────────────────────────────────
    cfg = cfg or DEFAULT_CONFIG
    min_str = f"${cfg['amount_min_usd'] // 1000}K" if cfg["amount_min_usd"] < 1_000_000 else f"${cfg['amount_min_usd'] // 1_000_000}M"
    max_str = f"${cfg['amount_max_usd'] // 1_000_000}M"
    kw = cfg.get("extra_target_keywords", [])
    hints = " | hints on" if cfg.get("extra_signal_hints") else ""
    kw_str = f" | +{','.join(kw)}" if kw else ""
    lines.append(f"\n⚙️ Config: range {min_str}–{max_str}{kw_str}{hints}")

    return "\n".join(lines).strip()

# ─── Post ────────────────────────────────────────────────────────────────────

def post_to_discord(leads: list[dict], cfg: dict = None) -> None:
    if not leads:
        print("[INFO] No leads — nothing to post")
        return

    message = build_discord_message(leads, cfg)

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

    cfg = load_config()
    memory = load_memory()

    # Merge extra keywords from config into the filter
    if cfg.get("extra_target_keywords"):
        for kw in cfg["extra_target_keywords"]:
            if kw.lower() not in STAGE_KEYWORDS:
                STAGE_KEYWORDS.append(kw.lower())
    if cfg.get("extra_ignored_keywords"):
        memory["preferences"]["ignored_keywords"] = list(set(
            memory["preferences"].get("ignored_keywords", []) + cfg["extra_ignored_keywords"]
        ))

    raw_items = fetch_all_sources()
    print(f"[INFO] Total fetched: {len(raw_items)}")

    candidates = pre_filter(raw_items, memory)
    if not candidates:
        print("[INFO] No candidates after filter. Done.")
        memory["last_run"] = datetime.now(timezone.utc).isoformat()
        save_memory(memory)
        return

    leads = analyze_and_review(candidates, memory.get("last_7_reports", []), cfg)
    if leads is None:
        print("[ERROR] Groq failed — memory not updated so articles can be retried tomorrow.")
        return

    print(f"[INFO] Final leads: {len(leads)}")
    post_to_discord(leads, cfg)

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
