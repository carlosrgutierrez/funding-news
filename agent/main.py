"""
 Intelligence Agent for Imaginary Space.
Pipeline: fetch -> pre-filter -> classify -> enrich -> extract -> post -> memory.

Output: verified funding events from the last 24h. No inference. No generated URLs.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ─── Constants ────────────────────────────────────────────────────────────────

GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DRY_RUN             = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")
MEMORY_PATH         = os.path.join(os.path.dirname(__file__), "memory.json")
CONFIG_PATH         = os.path.join(os.path.dirname(__file__), "config.json")
GROQ_MODEL          = "llama-3.3-70b-versatile"
MAX_CANDIDATES      = 25
MAX_ARTICLE_CHARS   = 1500
MAX_MESSAGE_CHARS   = 1900
SEEN_DAYS           = 7
DATE_WINDOW_HOURS   = 48  # articles older than this are flagged stale; hard-filtered if > 5 days

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"forget\s+(all\s+)?(previous|prior|above|your)\s+instructions",
    r"you\s+are\s+now\s+(a\s+)?(new|different)",
    r"system\s*prompt\s*:",
    r"override\s+(all\s+)?instructions",
    r"jailbreak",
    r"do\s+not\s+follow",
    r"disregard\s+(all\s+)?(previous|prior)",
    r"new\s+instructions?\s*:",
    r"<\s*system\s*>",
]

# ─── Sources ──────────────────────────────────────────────────────────────────

SOURCES = [
    {"name": "TechCrunch s", "url": "https://techcrunch.com/category/s/feed/"},
    {"name": "TechCrunch Funding",  "url": "https://techcrunch.com/tag/funding/feed/"},
    {"name": "TechCrunch",          "url": "https://techcrunch.com/feed/"},
    {"name": "Crunchbase News",     "url": "https://news.crunchbase.com/feed/"},
    {"name": "VentureBeat",         "url": "https://venturebeat.com/feed/"},
    {"name": "EU s",         "url": "https://www.eu-s.com/feed/"},
    {"name": "Sifted",              "url": "https://sifted.eu/feed"},
    {"name": "HN Seed Funding",     "url": "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=seed+funding+&hitsPerPage=25"},
    {"name": "HN Pre-seed",         "url": "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=pre-seed+raise+&hitsPerPage=20"},
    {"name": "HN Raises",           "url": "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=raises+seed+capital+2026&hitsPerPage=20"},
]

STAGE_KEYWORDS = [
    "seed", "pre-seed", "preseed", "pre seed", "angel",
    "raised", "raises", "funding", "funded",
    "secured", "secures", "closed", "closes",
    "announced", "announces", "backed",
    "investment", "invested", "investor",
    "venture", "capital", "financing",
    "million", "series a",
    "oversubscribed", "round",
    "launched", "launch", "debuts", "unveils",
    "ships", "releases", "open source",
]

HARD_DISCARD = [
    "series d", "series e", "series f",
    "ipo", "nasdaq", "nyse", "public offering",
    "acqui", "acquisition",
    "crypto", "blockchain", "nft",
    "pharmaceutical",
]

# ─── Config ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "icp": "Early-stage founders who just raised and need to ship an MVP fast.",
    "amount_min_usd": 250000,
    "amount_max_usd": 5000000,
    "extra_target_keywords": [],
    "extra_ignored_keywords": [],
    "window_hours": DATE_WINDOW_HOURS,
}

def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        min_k = cfg.get("amount_min_usd", 250000)
        max_m = cfg.get("amount_max_usd", 5000000)
        print(f"[CONFIG] Loaded: range ${min_k//1000}K–${max_m//1_000_000}M | window {cfg.get('window_hours', DATE_WINDOW_HOURS)}h")
        return {**DEFAULT_CONFIG, **cfg}
    except FileNotFoundError:
        print("[CONFIG] config.json not found — using defaults")
        return DEFAULT_CONFIG
    except Exception as ex:
        print(f"[CONFIG] Parse error: {ex} — using defaults")
        return DEFAULT_CONFIG

# ─── Memory ───────────────────────────────────────────────────────────────────

def load_memory() -> dict:
    try:
        with open(MEMORY_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "processed_urls": [],
            "blacklist_domains": [],
            "seen_companies": [],
            "preferences": {"target_keywords": [], "ignored_keywords": []},
        }

def save_memory(memory: dict) -> None:
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)

def is_company_seen(company: str, memory: dict) -> bool:
    cutoff = (date.today() - timedelta(days=SEEN_DAYS)).isoformat()
    name = company.lower().strip()
    return any(
        s["company"].lower().strip() == name and s["date_seen"] >= cutoff
        for s in memory.get("seen_companies", [])
    )

def mark_company_seen(company: str, memory: dict) -> None:
    if "seen_companies" not in memory:
        memory["seen_companies"] = []
    memory["seen_companies"].append({"company": company, "date_seen": date.today().isoformat()})
    cutoff = (date.today() - timedelta(days=SEEN_DAYS)).isoformat()
    memory["seen_companies"] = [s for s in memory["seen_companies"] if s["date_seen"] >= cutoff]

# ─── Injection guard ──────────────────────────────────────────────────────────

def sanitize(text: str) -> str:
    if not text:
        return ""
    original = text
    for pattern in INJECTION_PATTERNS:
        text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
    if text != original:
        print("[SECURITY] Injection pattern redacted")
    return text

# ─── Date helpers ─────────────────────────────────────────────────────────────

def parse_rss_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None

def parse_iso_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def age_label(published_at: str | None, now: datetime) -> str | None:
    """Returns a human-readable age warning if article is older than DATE_WINDOW_HOURS."""
    if not published_at:
        return None
    dt = parse_iso_date(published_at)
    if not dt:
        return None
    hours = (now - dt).total_seconds() / 3600
    if hours > DATE_WINDOW_HOURS:
        days = int(hours // 24)
        return f"Published {days} day{'s' if days != 1 else ''} ago"
    return None

# ─── Article extraction ───────────────────────────────────────────────────────

def extract_article_body(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    for selector in ["article", "main", "[class*='article']", "[class*='content']", "[class*='post']"]:
        container = soup.select_one(selector)
        if container:
            paragraphs = container.find_all("p")
            text = " ".join(p.get_text(" ", strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40)
            if len(text) > 200:
                return re.sub(r"\s+", " ", text).strip()[:MAX_ARTICLE_CHARS]
    paragraphs = soup.find_all("p")
    text = " ".join(p.get_text(" ", strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40)
    return re.sub(r"\s+", " ", text).strip()[:MAX_ARTICLE_CHARS]

def fetch_full_article(url: str) -> str:
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0 (compatible; -intel/1.0)"})
        r.raise_for_status()
        body = extract_article_body(r.text)
        if body:
            return body
        print(f"[EXTRACT] Empty body after parsing: {url[:60]}")
        return ""
    except Exception as ex:
        print(f"[EXTRACT] Failed ({url[:60]}): {ex}")
        return ""

# ─── Fetch sources ────────────────────────────────────────────────────────────

def fetch_rss(source: dict) -> list[dict]:
    try:
        feed = feedparser.parse(source["url"])
        items = []
        for e in feed.entries:
            pub = parse_rss_date(e)
            items.append({
                "title":        e.get("title", ""),
                "summary":      e.get("summary", e.get("description", "")),
                "url":          e.get("link", ""),
                "source":       source["name"],
                "published_at": pub.isoformat() if pub else None,
            })
        print(f"[FETCH] {source['name']}: {len(items)} items")
        return items
    except Exception as ex:
        print(f"[FETCH] {source['name']} failed: {ex}")
        return []

def fetch_hn(source: dict) -> list[dict]:
    try:
        r = requests.get(source["url"], timeout=10, headers={"User-Agent": "-intel/1.0"})
        r.raise_for_status()
        items = []
        for h in r.json().get("hits", []):
            pub = parse_iso_date(h.get("created_at"))
            items.append({
                "title":        h.get("title", ""),
                "summary":      h.get("story_text") or "",
                "url":          h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "source":       source["name"],
                "published_at": pub.isoformat() if pub else None,
            })
        print(f"[FETCH] {source['name']}: {len(items)} items")
        return items
    except Exception as ex:
        print(f"[FETCH] {source['name']} failed: {ex}")
        return []

def fetch_all_sources() -> list[dict]:
    items = []
    for src in SOURCES:
        if "algolia" in src["url"]:
            items.extend(fetch_hn(src))
        else:
            items.extend(fetch_rss(src))
    print(f"[FETCH] Total: {len(items)} raw items across {len(SOURCES)} sources")
    return items

# ─── Pre-filter ───────────────────────────────────────────────────────────────

def extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def pre_filter(items: list[dict], memory: dict, window_hours: int) -> list[dict]:
    processed    = set(memory.get("processed_urls", []))
    blacklist    = set(memory.get("blacklist_domains", []))
    ignored_kw   = [k.lower() for k in memory["preferences"].get("ignored_keywords", [])]
    cutoff_date  = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    hard_cutoff  = datetime.now(timezone.utc) - timedelta(days=5)  # never show articles older than 5 days

    seen_urls = set()
    passed = []
    rejected_seen = rejected_date = rejected_kw = rejected_discard = rejected_ignored = 0

    for item in items:
        url = item["url"]
        if not url or url in processed or url in seen_urls:
            rejected_seen += 1
            continue
        if extract_domain(url) in blacklist:
            rejected_seen += 1
            continue

        # Hard date filter: drop anything older than 5 days
        pub_at = item.get("published_at")
        if pub_at:
            pub_dt = parse_iso_date(pub_at)
            if pub_dt and pub_dt < hard_cutoff:
                rejected_date += 1
                continue

        text = f"{item['title']} {item['summary']}".lower()

        if any(kw in text for kw in HARD_DISCARD):
            rejected_discard += 1
            continue
        if any(kw in text for kw in ignored_kw):
            rejected_ignored += 1
            continue
        if not any(kw in text for kw in STAGE_KEYWORDS):
            rejected_kw += 1
            continue

        seen_urls.add(url)
        passed.append(item)

    print(f"[FILTER] {len(passed)} passed | {rejected_date} too old | {rejected_seen} already-seen | {rejected_discard} hard-discard | {rejected_kw} no-keyword | {rejected_ignored} ignored")
    return passed[:MAX_CANDIDATES]

# ─── Groq helpers ─────────────────────────────────────────────────────────────

def call_groq(system: str, user: str, max_tokens: int = 500) -> str | None:
    client = Groq(api_key=GROQ_API_KEY)
    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        return raw
    except Exception as ex:
        print(f"[GROQ] Call failed: {ex}")
        return None

def parse_json_response(raw: str) -> list | None:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        print(f"[GROQ] No JSON array in response: {cleaned[:200]}")
        return None
    try:
        result = json.loads(match.group())
        return result if isinstance(result, list) else None
    except json.JSONDecodeError as ex:
        print(f"[GROQ] JSON parse failed: {ex}")
        return None

# ─── Classification stage ─────────────────────────────────────────────────────

def classify_candidates(candidates: list[dict]) -> list[dict]:
    """Fast Groq call on titles only. Returns articles that are qualifying events."""
    if DRY_RUN:
        print("[CLASSIFY] DRY RUN — returning all candidates")
        return candidates

    lines = "\n".join(
        f"[{i+1}] {sanitize(c['title'])} | {sanitize(c['summary'][:120])}"
        for i, c in enumerate(candidates)
    )
    system = (
        "You are a strict classifier. Return ONLY a JSON array of integer IDs "
        "(e.g. [1,4,7]) for articles that are: "
        "(A) a single company raising Pre-seed or Seed ($250K–$5M), OR "
        "(B) a product/company launch announcement, OR "
        "(C) a technically ambitious open-source or architecture announcement. "
        "Reject: roundups, analysis, opinion, Series B+, no named company. "
        "Return [] if none qualify. No explanation. No markdown."
    )
    raw = call_groq(system, f"Classify:\n\n{lines}", max_tokens=200)
    if not raw:
        print("[CLASSIFY] Groq failed — using all candidates as fallback")
        return candidates

    print(f"[CLASSIFY] Raw: {raw[:200]}")
    try:
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        match = re.search(r"\[.*?\]", cleaned, re.DOTALL)
        ids = json.loads(match.group()) if match else []
        selected = [candidates[i - 1] for i in ids if 1 <= i <= len(candidates)]
        print(f"[CLASSIFY] {len(selected)}/{len(candidates)} selected: {ids}")
        return selected if selected else []
    except Exception as ex:
        print(f"[CLASSIFY] Parse error: {ex} — using all candidates")
        return candidates

# ─── Enrichment ───────────────────────────────────────────────────────────────

def enrich_candidates(candidates: list[dict]) -> list[dict]:
    print(f"[ENRICH] Fetching article bodies for {len(candidates)} articles...")
    for i, c in enumerate(candidates):
        body = fetch_full_article(c["url"])
        c["full_text"] = sanitize(body) if body else sanitize(c["summary"])
        chars = len(c["full_text"])
        print(f"[ENRICH] [{i+1}/{len(candidates)}] {c['source']} — {chars} chars")
    return candidates

# ─── Extraction ───────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM = """You extract structured facts from  news articles.

Each result must use EXACTLY these field names:
{
  "source_id": <integer>,
  "company": "company name",
  "amount": "$XM or null",
  "stage": "Pre-seed/Seed/Series A/Series B/Series C or null",
  "event_type": "raised/launched/technical",
  "founder_name": "full name or null"
}

Rules:
- Use the field name "company" (not "company_name").
- "amount" must be a dollar figure explicitly mentioned (e.g. "$3M"). Null if not stated.
- "stage" must be explicitly stated. Null if not stated.
- "founder_name" must be explicitly named in the article. Null if not found.
- "event_type" must be one of: raised / launched / technical
- "source_id" is the integer ID from the article tag — return it exactly.

Qualify only:
- raised: single named company with an explicitly stated funding amount
- launched: product or company public debut
- technical: open-source release, notable architecture announcement

Reject: roundups, industry analysis, opinion pieces, no explicit company name.

Output: JSON array only. No markdown. Empty array [] if nothing qualifies."""

def extract_events(candidates: list[dict], memory: dict, cfg: dict) -> list[dict] | None:
    if DRY_RUN:
        print("[GROQ] DRY RUN — returning mock events")
        return [
            {"source_id": 1, "company": "Loops", "amount": "$2.1M", "stage": "Seed",
             "event_type": "raised", "founder_name": "Chris Frantz"},
        ]

    cutoff = (date.today() - timedelta(days=SEEN_DAYS)).isoformat()
    skip_names = [s["company"] for s in memory.get("seen_companies", []) if s["date_seen"] >= cutoff]
    skip_line = f"\nSkip (already reported): {', '.join(skip_names)}" if skip_names else ""

    news_block = "\n\n".join(
        f'<article id="{i+1}">\nSOURCE: {c["source"]}\nTITLE: {sanitize(c["title"])}\n'
        f'BODY: {c.get("full_text", sanitize(c["summary"]))}\n</article>'
        for i, c in enumerate(candidates)
    )
    user = f"Amount range: ${cfg['amount_min_usd']//1000}K–${cfg['amount_max_usd']//1_000_000}M{skip_line}\n\n{news_block}"

    print(f"[GROQ] Sending {len(candidates)} articles for extraction...")
    raw = call_groq(EXTRACTION_SYSTEM, user, max_tokens=800)
    if raw is None:
        return None

    print(f"[GROQ] Raw response ({len(raw)} chars): {raw[:600]}")

    events = parse_json_response(raw)
    if events is None:
        return None
    if not events:
        print("[GROQ] No qualifying events found")
        return []

    # Resolve URLs from candidates — LLM never generates URLs
    resolved = []
    for ev in events:
        sid = ev.get("source_id")
        if not isinstance(sid, int) or not (1 <= sid <= len(candidates)):
            print(f"[VALIDATE] Invalid source_id {sid} — skipping")
            continue
        candidate = candidates[sid - 1]
        ev["article_url"]   = candidate["url"]
        ev["published_at"]  = candidate.get("published_at")
        ev["source"]        = candidate["source"]
        company = (ev.get("company") or ev.get("company_name") or "").strip()
        ev["company"] = company  # normalize field name
        if not company:
            print(f"[VALIDATE] Missing company name — skipping")
            continue
        event_type = ev.get("event_type", "")
        if event_type not in ("raised", "launched", "technical"):
            print(f"[VALIDATE] Invalid event_type '{event_type}' for {company} — skipping")
            continue
        # Injection check on text fields
        flagged = False
        for field in ("company", "founder_name"):
            val = ev.get(field) or ""
            if any(re.search(p, val, re.IGNORECASE) for p in INJECTION_PATTERNS):
                print(f"[VALIDATE] Injection in '{field}' for {company} — skipping")
                flagged = True
                break
        if flagged:
            continue
        print(f"[VALIDATE] PASS — {company} | {ev.get('event_type')} | {ev.get('amount') or 'amount not stated'}")
        resolved.append(ev)

    print(f"[VALIDATE] {len(resolved)} valid / {len(events) - len(resolved)} rejected")
    return resolved

# ─── Amount normalizer ────────────────────────────────────────────────────────

def normalize_amount(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw.lower().strip()
    if re.match(r"^\$[\d.]+[mkb]$", s, re.I):
        return raw.upper()
    m = re.search(r"([\d,.]+)\s*(billion|million|thousand|b|m|k)?", s)
    if not m:
        return raw
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return raw
    unit = (m.group(2) or "").lower()
    if unit in ("billion", "b"):  return f"${num:.1f}B"
    if unit in ("million", "m"):  return f"${num:.1f}M"
    if unit in ("thousand", "k"): return f"${num:.0f}K"
    if num >= 1_000_000: return f"${num / 1_000_000:.1f}M"
    if num >= 1_000:     return f"${num / 1_000:.0f}K"
    return f"${num:.0f}"

# ─── Discord ──────────────────────────────────────────────────────────────────

def build_discord_message(events: list[dict], cfg: dict, window_hours: int) -> str:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%B %d, %Y")
    lines = [f"Recent News | {date_str} | last {window_hours}h", ""]

    for ev in events:
        company     = ev.get("company", "Unknown")
        amount      = normalize_amount(ev.get("amount"))
        stage       = ev.get("stage") or ""
        founder     = ev.get("founder_name")
        source      = ev.get("source", "")
        url         = ev.get("article_url", "")
        event_type  = (ev.get("event_type") or "event").upper()
        pub_at      = ev.get("published_at")

        # Published date display
        pub_display = ""
        stale_warn  = ""
        if pub_at:
            pub_dt = parse_iso_date(pub_at)
            if pub_dt:
                pub_display = pub_dt.strftime("%b %d, %Y")
                hours_old   = (now - pub_dt).total_seconds() / 3600
                if hours_old > window_hours:
                    days_old  = int(hours_old // 24)
                    stale_warn = f"⚠️  Published {days_old} day{'s' if days_old != 1 else ''} ago"

        block = [f"{event_type}"]
        block.append(f"Company:   {company}")
        if amount:
            block.append(f"Amount:    {amount}")
        if stage:
            block.append(f"Stage:     {stage}")
        if founder:
            block.append(f"Founder:   {founder}")
        src_line = source
        if pub_display:
            src_line += f" | {pub_display}"
        block.append(f"Source:    {src_line}")
        block.append(f"URL:       {url}")
        if stale_warn:
            block.append(stale_warn)
        block.append("")

        if len("\n".join(lines + block)) > MAX_MESSAGE_CHARS:
            print(f"[DISCORD] Char limit reached — dropping remaining events")
            break
        lines += block

    min_s = f"${cfg['amount_min_usd']//1000}K" if cfg["amount_min_usd"] < 1_000_000 else f"${cfg['amount_min_usd']//1_000_000}M"
    max_s = f"${cfg['amount_max_usd']//1_000_000}M"
    lines.append(f"---\n{len(events)} event(s) | {min_s}–{max_s} | {window_hours}h window")
    return "\n".join(lines).strip()


def post_to_discord(events: list[dict], cfg: dict, window_hours: int) -> None:
    if not events:
        print("[DISCORD] No events — nothing to post")
        return

    message = build_discord_message(events, cfg, window_hours)
    print(f"[DISCORD] Message ({len(message)} chars):\n{'-'*60}\n{message}\n{'-'*60}")

    if DRY_RUN:
        print("[DISCORD] DRY RUN — skipping post")
        return

    resp = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"username": "Founding Radar", "content": message},
        timeout=10,
    )
    if resp.status_code in (200, 204):
        print(f"[DISCORD] Posted {len(events)} event(s) ✓")
    else:
        print(f"[DISCORD] Error {resp.status_code}: {resp.text}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*60}")
    print(f"[START] Imaginary Space Intel | dry_run={DRY_RUN} | {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}\n")

    cfg    = load_config()
    memory = load_memory()
    window = int(cfg.get("window_hours", DATE_WINDOW_HOURS))

    if cfg.get("extra_target_keywords"):
        for kw in cfg["extra_target_keywords"]:
            if kw.lower() not in STAGE_KEYWORDS:
                STAGE_KEYWORDS.append(kw.lower())
    if cfg.get("extra_ignored_keywords"):
        memory["preferences"]["ignored_keywords"] = list(set(
            memory["preferences"].get("ignored_keywords", []) + cfg["extra_ignored_keywords"]
        ))

    # Stage 1: Fetch
    raw_items = fetch_all_sources()

    # Stage 2: Pre-filter (keyword match + hard date cut)
    candidates = pre_filter(raw_items, memory, window)
    if not candidates:
        print("[FILTER] No candidates passed. Done.")
        memory["last_run"] = datetime.now(timezone.utc).isoformat()
        save_memory(memory)
        return

    # Stage 3: Classify (fast Groq call on titles only)
    classified = classify_candidates(candidates)
    if not classified:
        print("[CLASSIFY] No articles classified. Done.")
        memory["last_run"] = datetime.now(timezone.utc).isoformat()
        save_memory(memory)
        return

    # Stage 4: Enrich (fetch full article body)
    enriched = enrich_candidates(classified)

    # Stage 5: Extract events
    events = extract_events(enriched, memory, cfg)
    if events is None:
        print("[GROQ] Extraction failed — will retry tomorrow.")
        return
    if not events:
        print("[GROQ] No qualifying events. Done.")
        memory["last_run"] = datetime.now(timezone.utc).isoformat()
        save_memory(memory)
        return

    # Stage 6: Deduplicate
    new_events = [e for e in events if not is_company_seen(e.get("company", ""), memory)]
    skipped    = len(events) - len(new_events)
    if skipped:
        print(f"[DEDUP] Skipped {skipped} company/ies seen in last {SEEN_DAYS} days")
    if not new_events:
        print("[DEDUP] All events already seen. Done.")
        memory["last_run"] = datetime.now(timezone.utc).isoformat()
        save_memory(memory)
        return

    # Stage 7: Post
    print(f"\n[FINAL] {len(new_events)} new event(s) ready to post")
    post_to_discord(new_events, cfg, window)

    if DRY_RUN:
        print("[MEMORY] DRY RUN — skipping memory save")
        return

    # Stage 8: Save memory
    memory["processed_urls"] = list(set(
        memory.get("processed_urls", []) + [c["url"] for c in candidates]
    ))
    for ev in new_events:
        mark_company_seen(ev.get("company", ""), memory)
    memory["last_run"] = datetime.now(timezone.utc).isoformat()
    save_memory(memory)
    print("[MEMORY] Saved ✓")


if __name__ == "__main__":
    if not DRY_RUN:
        missing = [v for v in ("GROQ_API_KEY", "DISCORD_WEBHOOK_URL") if not os.getenv(v)]
        if missing:
            print(f"[ERROR] Missing env vars: {', '.join(missing)}")
            sys.exit(1)
    run()
