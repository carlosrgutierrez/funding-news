import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import main


class PreFilterDateWindowTests(unittest.TestCase):
    def setUp(self):
        self.memory = {
            "processed_urls": [],
            "blacklist_domains": [],
            "preferences": {"ignored_keywords": []},
        }

    def article(self, url, published_at):
        return {
            "title": "Seed startup raises funding",
            "summary": "A startup raised a seed round",
            "url": url,
            "source": "Test",
            "published_at": published_at,
        }

    def test_rejects_articles_older_than_configured_window(self):
        old = datetime.now(timezone.utc) - timedelta(hours=25)

        result = main.pre_filter(
            [self.article("https://example.com/old", old.isoformat())],
            self.memory,
            window_hours=24,
        )

        self.assertEqual(result, [])

    def test_rejects_articles_without_publish_date(self):
        result = main.pre_filter(
            [self.article("https://example.com/unknown", None)],
            self.memory,
            window_hours=24,
        )

        self.assertEqual(result, [])

    def test_classification_prompt_uses_configured_amount_range(self):
        captured = {}

        def fake_call(system, user, max_tokens=500):
            captured["system"] = system
            return "[1]"

        original_call = main.call_groq
        original_dry_run = main.DRY_RUN
        try:
            main.call_groq = fake_call
            main.DRY_RUN = False
            result = main.classify_candidates(
                [self.article("https://example.com/seed", datetime.now(timezone.utc).isoformat())],
                {"amount_min_usd": 100000, "amount_max_usd": 20000000},
            )
        finally:
            main.call_groq = original_call
            main.DRY_RUN = original_dry_run

        self.assertEqual(len(result), 1)
        self.assertIn("$100K-$20M", captured["system"])

    def test_processed_urls_only_include_posted_events(self):
        memory = {"processed_urls": ["https://example.com/old"]}
        events = [{"article_url": "https://example.com/posted"}]

        main.mark_processed_event_urls(events, memory)

        self.assertEqual(
            sorted(memory["processed_urls"]),
            ["https://example.com/old", "https://example.com/posted"],
        )

    def test_watchlist_keeps_startup_momentum_and_filters_big_company_noise(self):
        now = datetime.now(timezone.utc).isoformat()
        items = [
            {
                "title": "Nvidia has already committed $40B to equity AI deals this year",
                "summary": "Public company investment activity.",
                "url": "https://example.com/nvidia",
                "source": "TechCrunch",
                "published_at": now,
            },
            {
                "title": "YC startup launches developer tooling beta after seed round",
                "summary": "A startup launches a developer platform for AI teams.",
                "url": "https://example.com/yc-startup",
                "source": "HN",
                "published_at": now,
            },
        ]

        watchlist = main.select_watchlist_items(items)

        self.assertEqual(len(watchlist), 1)
        self.assertEqual(watchlist[0]["url"], "https://example.com/yc-startup")

    def test_watchlist_rejects_human_interest_articles_even_with_startup_metadata(self):
        now = datetime.now(timezone.utc).isoformat()
        items = [
            {
                "title": "People who keep their phone face-down on the table often are not being polite",
                "summary": "Startup readers may find lessons about work and AI.",
                "url": "https://example.com/human-interest",
                "source": "Silicon Canals",
                "published_at": now,
            }
        ]

        watchlist = main.select_watchlist_items(items)

        self.assertEqual(watchlist, [])

    def test_fallback_digest_posts_no_major_events_with_optional_watchlist(self):
        now = datetime(2026, 5, 9, tzinfo=timezone.utc)
        candidates = [
            {
                "title": "YC startup launches developer tooling beta after seed round",
                "summary": "A startup launches a developer platform for AI teams.",
                "url": "https://example.com/yc-startup",
                "source": "HN",
                "published_at": now.isoformat(),
            }
        ]

        message = main.build_fallback_digest_message(
            {"amount_min_usd": 100000, "amount_max_usd": 20000000},
            window_hours=24,
            candidates=candidates,
            now=now,
        )

        self.assertIn("No major pre-seed, seed, angel, or Series A funding events found", message)
        self.assertIn("Watchlist:", message)
        self.assertIn("<https://example.com/yc-startup>", message)

    def test_event_message_wraps_url_to_suppress_discord_preview(self):
        message = main.build_discord_message(
            [
                {
                    "company": "Acme AI",
                    "amount": "$1M",
                    "stage": "Seed",
                    "founder_name": "Jane Founder",
                    "source": "Test",
                    "article_url": "https://example.com/acme",
                    "event_type": "raised",
                    "published_at": datetime(2026, 5, 9, tzinfo=timezone.utc).isoformat(),
                }
            ],
            {"amount_min_usd": 100000, "amount_max_usd": 20000000},
            window_hours=24,
        )

        self.assertIn("URL:       <https://example.com/acme>", message)
        self.assertNotIn("URL:       https://example.com/acme", message)

    def test_send_discord_message_handles_generic_messages(self):
        calls = []

        class Response:
            status_code = 204
            text = ""

        def fake_post(url, json, timeout):
            calls.append((url, json, timeout))
            return Response()

        original_post = main.requests.post
        original_dry_run = main.DRY_RUN
        original_webhook = main.DISCORD_WEBHOOK_URL
        try:
            main.requests.post = fake_post
            main.DRY_RUN = False
            main.DISCORD_WEBHOOK_URL = "https://example.com/webhook"

            main.send_discord_message("Founding Radar test")
        finally:
            main.requests.post = original_post
            main.DRY_RUN = original_dry_run
            main.DISCORD_WEBHOOK_URL = original_webhook

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["username"], "Founding Radar")
        self.assertEqual(calls[0][1]["content"], "Founding Radar test")


if __name__ == "__main__":
    unittest.main()
